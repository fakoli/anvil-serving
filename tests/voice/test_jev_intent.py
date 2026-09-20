import queue
import sys
import threading
import time

import pytest

from anvil_serving import jev
from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.jev_intent import IntentAdvisor
from anvil_serving.voice.messages import Transcription
from anvil_serving.voice.pipeline import VoicePipeline
from anvil_serving.voice.realtime.pool import SessionPool
from anvil_serving.voice.realtime.service import RealtimeService
from types import SimpleNamespace


def policy():
    return jev.validate_policy({"enabled": True, "capabilities": ["voice_intent"], "allow_api": True,
                                "allow_export": True, "anvil_binary": sys.executable})


def text(scope, turn="one", final=True):
    return Transcription(turn_id=turn, turn_revision=0, generation=scope.current(), text="restart the model", is_final=final)


def test_disabled_partial_and_cancelled_turns_never_start_worker():
    scope = CancelScope()
    advisor = IntentAdvisor(scope, policy_reader=policy)
    advisor.submit(text(scope))
    assert advisor.thread is None
    advisor.configure(True)
    advisor.submit(text(scope, final=False))
    stale = text(scope)
    scope.cancel()
    advisor.submit(stale)
    assert advisor.thread is None


def test_slow_classifier_is_bounded_and_cancel_never_waits_or_emits_stale():
    scope = CancelScope()
    entered, finish = threading.Event(), threading.Event()
    calls = []
    def slow(*args, **kwargs):
        calls.append(args)
        entered.set()
        finish.wait(2)
        return jev.report("voice_intent", "unavailable", "timeout", started=True)
    advisor = IntentAdvisor(scope, policy_reader=policy, advise=slow)
    advisor.configure(True)
    advisor.submit(text(scope))
    assert entered.wait(1)
    worker = advisor.thread
    for number in range(20):
        advisor.submit(text(scope, str(number)))
    assert advisor.pending.qsize() == 1 and advisor.thread is worker
    start = time.monotonic()
    scope.cancel()
    assert time.monotonic() - start < .1
    finish.set()
    advisor.close()
    assert advisor.drain() is None and len(calls) == 1 and not worker.is_alive()


def test_final_text_extension_is_disclosed_and_never_sent_to_llm_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    scope = CancelScope()
    llm_configs = []
    pipeline = SimpleNamespace(cancel_scope=scope, audio_in=queue.Queue(), audio_out=queue.Queue(),
                               transcript_events=queue.Queue(), vad_events=queue.Queue(),
                               llm=SimpleNamespace(in_queue=queue.Queue(), configure_realtime_session=llm_configs.append))
    events = []
    service = RealtimeService(pipeline=pipeline, send_event=events.append, session_id="session")
    # Disabled policy never starts a worker or reads the credential.
    service.handle_client_message('{"type":"session.update","session":{"anvil_jev":{"enabled":true,"allow_export":true}}}')
    assert "anvil_jev" not in llm_configs[-1]
    assert events[-1]["session"]["anvil_jev"]["model"] == jev.MODEL
    pipeline.jev_advisor.close()


def test_turn_and_generation_attribution_survives_only_current_enabled_result():
    scope = CancelScope()
    advisor = IntentAdvisor(scope, policy_reader=policy,
        advise=lambda *args, **kwargs: jev.report("voice_intent", "unavailable", "offline", started=True))
    advisor.configure(True)
    advisor.submit(text(scope))
    result = advisor.results.get(timeout=1)
    advisor.results.put_nowait(result)
    event = advisor.drain()
    assert event["type"] == "anvil.voice.intent" and event["turn_id"] == "one"
    assert event["annotation"]["advisory"] and event["annotation"]["request_started"]
    assert "restart" not in str(event)
    advisor.close()


def test_policy_revocation_drops_completed_advice_and_future_requests():
    scope = CancelScope()
    settings = policy()
    advisor = IntentAdvisor(scope, policy_reader=lambda: settings,
        advise=lambda *args, **kwargs: jev.report("voice_intent", "unavailable", "offline", started=True))
    advisor.configure(True)
    advisor.submit(text(scope))
    result = advisor.results.get(timeout=1)
    advisor.results.put_nowait(result)
    settings["allow_export"] = False
    assert advisor.drain() is None
    advisor.submit(text(scope, "later"))
    assert advisor.pending.empty()
    advisor.close()


@pytest.mark.parametrize("revoke", ["consent", "generation", "newer_turn"])
def test_revocation_at_dispatch_boundary_prevents_bridge_call(revoke):
    scope = CancelScope()
    entered, resume, finished = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def paused(*args, **kwargs):
        entered.set()
        assert resume.wait(2)
        result = jev.advise(*args, **kwargs, bridge=lambda *_: calls.append(True))
        finished.set()
        return result
    advisor = IntentAdvisor(scope, policy_reader=policy, advise=paused)
    advisor.configure(True)
    advisor.submit(text(scope))
    try:
        assert entered.wait(1)
        if revoke == "consent":
            advisor.configure(False)
        elif revoke == "generation":
            scope.cancel()
        else:
            advisor.latest = ("newer", 0, scope.current())
    finally:
        resume.set()
        assert finished.wait(1)
        advisor.close()
    assert calls == [] and advisor.drain() is None


def test_core_voice_stops_before_advisor_cleanup_even_without_join_wait():
    actions = []
    pipeline = VoicePipeline.__new__(VoicePipeline)
    pipeline.manager = SimpleNamespace(stop_all=lambda **kwargs: actions.append(("core_stop", kwargs)))
    def close(*, join_timeout):
        assert join_timeout == 0
        assert actions[-1] == ("core_stop", {"join_timeout": 0})
        actions.append("advisory_cleanup")
    pipeline.jev_advisor = SimpleNamespace(configure=lambda enabled: actions.append(("consent", enabled)), close=close)
    pipeline.stop(join_timeout=0)
    assert actions == [("consent", False), ("core_stop", {"join_timeout": 0}), "advisory_cleanup"]


def test_operator_disable_reenable_discards_queued_voice_advice(tmp_path, monkeypatch):
    from anvil_serving.jev_cli import atomic_write_json
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(tmp_path))
    path = jev.policy_path()
    initial = policy()
    atomic_write_json(path, initial)
    scope = CancelScope()
    advisor = IntentAdvisor(scope,
        advise=lambda *args, **kwargs: jev.report("voice_intent", "unavailable", "offline", started=True))
    advisor.configure(True)
    advisor.submit(text(scope))
    try:
        result = advisor.results.get(timeout=1)
        advisor.results.put_nowait(result)
        atomic_write_json(path, initial | {"enabled": False})
        atomic_write_json(path, initial)
        assert advisor.drain() is None
    finally:
        advisor.close()


@pytest.fixture
def blocked_advisor():
    scope = CancelScope()
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(*args, **kwargs):
        calls.append(args)
        entered.set()
        release.wait(1)
        return jev.report("voice_intent", "unavailable", "offline", started=True)

    advisor = IntentAdvisor(scope, policy_reader=policy, advise=blocked)
    advisor.configure(True)
    advisor.submit(text(scope))
    assert entered.wait(1)
    try:
        yield advisor, release, calls
    finally:
        release.set()
        advisor.close()


@pytest.mark.parametrize("method", ["stop", "shutdown_gracefully"])
@pytest.mark.parametrize("budget", [0, .05])
def test_shutdown_bounds_blocked_advisor_and_discards_late_results(blocked_advisor, method, budget):
    advisor, release, calls = blocked_advisor
    pipeline = VoicePipeline(cancel_scope=advisor.scope)
    pipeline.jev_advisor = advisor
    advisor.submit(text(advisor.scope, "queued"))
    start = time.monotonic()
    getattr(pipeline, method)(join_timeout=budget)
    elapsed = time.monotonic() - start
    assert elapsed < budget + .1
    assert advisor.thread.is_alive(), "shutdown should return before the blocked call finishes"
    assert advisor.stop.is_set() and advisor.pending.empty()
    advisor.configure(True)
    advisor.submit(text(advisor.scope, "after-close"))
    release.set()
    advisor.thread.join(timeout=1)
    assert not advisor.thread.is_alive() and len(calls) == 1
    assert advisor.drain() is None


@pytest.mark.parametrize("method,remaining", [("stop", .20), ("shutdown_gracefully", .10)])
def test_shutdown_does_not_restart_advisor_budget(monkeypatch, method, remaining):
    from anvil_serving.voice import pipeline as pipeline_module
    clock = [10.0]
    joins = []
    pipeline = VoicePipeline()

    def drain(*, timeout):
        assert timeout == .25
        clock[0] += .10

    def core_stop(*, join_timeout):
        clock[0] += .05

    pipeline._wait_for_last_stage_to_drain = drain
    pipeline.manager.stop_all = core_stop
    pipeline.jev_advisor = SimpleNamespace(configure=lambda enabled: None,
        close=lambda **kwargs: joins.append(kwargs["join_timeout"]))
    monkeypatch.setattr(pipeline_module.time, "monotonic", lambda: clock[0])
    getattr(pipeline, method)(join_timeout=.25)
    assert joins == [pytest.approx(remaining)]


def test_explicit_unbounded_shutdown_waits_for_advisor(blocked_advisor):
    advisor, release, calls = blocked_advisor
    pipeline = VoicePipeline(cancel_scope=advisor.scope)
    pipeline.jev_advisor = advisor
    closer = threading.Thread(target=lambda: pipeline.stop(join_timeout=None))
    closer.start()
    try:
        assert advisor.stop.wait(1)
        assert closer.is_alive()
        release.set()
        closer.join(timeout=1)
        assert not closer.is_alive() and not advisor.thread.is_alive()
        assert len(calls) == 1 and advisor.drain() is None
    finally:
        release.set()
        closer.join(timeout=2)


def test_pool_release_does_not_wait_for_blocked_advisor(blocked_advisor):
    advisor, release, calls = blocked_advisor
    pool = SessionPool(1)
    unit = pool.claim("old-session")
    old_pipeline = unit.pipeline
    old_pipeline.jev_advisor = advisor
    try:
        start = time.monotonic()
        pool.release(unit, drain_timeout=0)
        assert time.monotonic() - start < .1
        assert advisor.thread.is_alive() and advisor.stop.is_set()
        reclaimed = pool.claim("new-session")
        assert reclaimed is unit and reclaimed.pipeline is not old_pipeline
        release.set()
        advisor.thread.join(timeout=1)
        assert not advisor.thread.is_alive() and len(calls) == 1
        assert advisor.drain() is None
    finally:
        release.set()
        old_pipeline.stop()
        pool.release(unit)
