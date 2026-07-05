"""Bounded session pool: N sessions isolated, drain-before-release, and
clean over-pool rejection (``anvil_serving.voice.realtime.pool``).

Dependency-light: pipelines are built with a fake ``llm_stream_fn`` (no real
HTTP) exactly like ``tests/voice/test_pipeline_spine.py``. No GPU, no torch,
no network.
"""
from __future__ import annotations

import pytest

from anvil_serving.voice.messages import AudioOut, EndOfResponse
from anvil_serving.voice.pipeline import VoicePipeline
from anvil_serving.voice.realtime.pool import SessionPool, SessionPoolExhausted
from anvil_serving.voice.stages.vad import VADConfig

SPEECH = b"\x01\x02\x03\x04"
SILENCE = b"\x00\x00\x00\x00"


def _fake_stream(text, config):
    yield "Hi there. "


def _pipeline_factory():
    return VoicePipeline(
        vad_config=VADConfig(frame_ms=50, silence_ms=200),  # 4 silent frames end a turn
        llm_stream_fn=_fake_stream,
    )


def _feed_one_turn(pipeline, *, silence_frames=4):
    pipeline.audio_in.put(SPEECH)
    for _ in range(silence_frames):
        pipeline.audio_in.put(SILENCE)


@pytest.fixture
def pool():
    p = SessionPool(size=2, pipeline_factory=_pipeline_factory)
    yield p
    # Best-effort cleanup so a failed assertion doesn't leak live threads
    # into the next test.
    for unit in p._units:
        if unit.in_use:
            p.release(unit, drain_timeout=1.0)
        else:
            unit.pipeline.shutdown_gracefully(join_timeout=1.0)


# --------------------------------------------------------------------------- #
# claim / release basics + over-pool rejection
# --------------------------------------------------------------------------- #


def test_claim_reserves_distinct_units(pool):
    unit_a = pool.claim("session-a")
    unit_b = pool.claim("session-b")
    assert unit_a.unit_id != unit_b.unit_id
    assert unit_a.in_use and unit_b.in_use
    assert unit_a.session_id == "session-a"
    assert unit_b.session_id == "session-b"


def test_claim_starts_the_units_pipeline(pool):
    unit = pool.claim("session-a")
    assert unit.pipeline.manager.all_alive()


def test_over_pool_claim_is_cleanly_rejected(pool):
    pool.claim("session-a")
    pool.claim("session-b")
    with pytest.raises(SessionPoolExhausted):
        pool.claim("session-c")
    stats = pool.usage_stats()
    assert stats["rejections_total"] == 1
    assert stats["claims_total"] == 2


def test_pool_status_reflects_occupancy(pool):
    pool.claim("session-a")
    status = pool.pool_status()
    assert status["size"] == 2
    assert status["in_use"] == 1
    assert status["idle"] == 1
    occupied = [u for u in status["units"] if u["in_use"]]
    assert occupied == [{"unit_id": occupied[0]["unit_id"], "in_use": True, "session_id": "session-a"}]


def test_release_frees_a_slot_for_reclaim(pool):
    unit_a = pool.claim("session-a")
    pool.claim("session-b")
    pool.release(unit_a, drain_timeout=2.0)
    assert pool.pool_status()["idle"] == 1
    # The freed slot can now be claimed again (was previously rejected while
    # both units were occupied).
    unit_c = pool.claim("session-c")
    assert unit_c.unit_id == unit_a.unit_id


def test_release_is_idempotent(pool):
    unit_a = pool.claim("session-a")
    pool.release(unit_a, drain_timeout=2.0)
    pool.release(unit_a, drain_timeout=2.0)  # must not raise or double-count
    assert pool.usage_stats()["releases_total"] == 1


# --------------------------------------------------------------------------- #
# isolation: N sessions never see each other's output
# --------------------------------------------------------------------------- #


def test_two_claimed_sessions_are_fully_isolated(pool):
    unit_a = pool.claim("session-a")
    unit_b = pool.claim("session-b")
    assert unit_a.pipeline is not unit_b.pipeline

    _feed_one_turn(unit_a.pipeline)
    items_a = unit_a.pipeline.drain_audio_out(timeout=3.0)

    assert any(isinstance(m, AudioOut) for m in items_a)
    assert any(isinstance(m, EndOfResponse) for m in items_a)

    # session-b's pipeline never received any audio -> its out queue is empty.
    assert unit_b.pipeline.audio_out.empty()

    pool.release(unit_a, drain_timeout=2.0)
    pool.release(unit_b, drain_timeout=2.0)


# --------------------------------------------------------------------------- #
# drain-before-release
# --------------------------------------------------------------------------- #


def test_release_drains_the_outgoing_pipeline_before_freeing_it(pool):
    unit = pool.claim("session-a")
    old_pipeline = unit.pipeline
    _feed_one_turn(old_pipeline)

    # Release immediately -- shutdown_gracefully must process the already-
    # queued turn (FIFO: PIPELINE_END is enqueued AFTER the turn's frames)
    # before the threads actually stop.
    pool.release(unit, drain_timeout=3.0)

    # The OLD pipeline's threads are fully stopped (drained, not abandoned
    # mid-flight): no cross-session leakage is possible once release returns.
    assert not old_pipeline.manager.all_alive()

    # The unit was reconstructed with a fresh pipeline instance for reuse
    # (BaseStage has no in-place restart path -- see pool.py's module
    # docstring honesty note).
    assert unit.pipeline is not old_pipeline

    # And the outgoing turn's output really was produced (proves the drain
    # actually let the in-flight turn finish, rather than truncating it).
    items = old_pipeline.drain_audio_out(timeout=1.0)
    end_items = [m for m in items if isinstance(m, EndOfResponse)]
    assert end_items, f"expected the in-flight turn to finish before shutdown, got: {items}"


def test_reclaimed_unit_starts_with_a_clean_pipeline(pool):
    unit = pool.claim("session-a")
    _feed_one_turn(unit.pipeline)
    unit.pipeline.drain_audio_out(timeout=2.0)
    pool.release(unit, drain_timeout=2.0)

    reclaimed = pool.claim("session-b")
    assert reclaimed.unit_id == unit.unit_id
    # A brand-new pipeline has nothing buffered on its out queue.
    assert reclaimed.pipeline.audio_out.empty()
    pool.release(reclaimed, drain_timeout=2.0)


# --------------------------------------------------------------------------- #
# barge-in cancellation
# --------------------------------------------------------------------------- #


def test_cancel_active_response_bumps_generation_for_the_right_session(pool):
    unit_a = pool.claim("session-a")
    unit_b = pool.claim("session-b")
    gen_a_before = unit_a.pipeline.cancel_scope.current()
    gen_b_before = unit_b.pipeline.cancel_scope.current()

    assert pool.cancel_active_response("session-a") is True

    assert unit_a.pipeline.cancel_scope.current() == gen_a_before + 1
    assert unit_b.pipeline.cancel_scope.current() == gen_b_before  # untouched

    pool.release(unit_a, drain_timeout=2.0)
    pool.release(unit_b, drain_timeout=2.0)


def test_cancel_active_response_for_unknown_session_returns_false(pool):
    pool.claim("session-a")
    assert pool.cancel_active_response("nonexistent") is False


# --------------------------------------------------------------------------- #
# constructor validation
# --------------------------------------------------------------------------- #


def test_pool_size_must_be_positive():
    with pytest.raises(ValueError):
        SessionPool(size=0)
