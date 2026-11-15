"""Bounded session pool: N sessions isolated, drain-before-release, and
clean over-pool rejection (``anvil_serving.voice.realtime.pool``).

Dependency-light: pipelines are built with a fake ``llm_stream_fn`` (no real
HTTP). No GPU, no torch, no network.
"""
from __future__ import annotations


import pytest

from anvil_serving.voice.pipeline import VoicePipeline
from anvil_serving.voice.realtime.pool import SessionPool
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


def test_release_is_idempotent(pool):
    unit_a = pool.claim("session-a")
    pool.release(unit_a, drain_timeout=2.0)
    pool.release(unit_a, drain_timeout=2.0)  # must not raise or double-count
    assert pool.usage_stats()["releases_total"] == 1


# --------------------------------------------------------------------------- #
# B3 regression: claim/release race
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# isolation: N sessions never see each other's output
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# drain-before-release
# --------------------------------------------------------------------------- #


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
