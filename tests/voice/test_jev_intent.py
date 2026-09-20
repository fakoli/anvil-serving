import queue
import threading
import time

import pytest

from anvil_serving import jev
from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.jev_intent import IntentAdvisor
from anvil_serving.voice.messages import Transcription
from anvil_serving.voice.realtime.service import RealtimeService
from types import SimpleNamespace


def policy():
    return jev.validate_policy({"enabled": True, "capabilities": ["voice_intent"], "allow_api": True,
                                "allow_export": True, "anvil_binary": "/usr/bin/anvil"})


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
    from anvil_serving.voice.pipeline import VoicePipeline
    actions = []
    pipeline = VoicePipeline.__new__(VoicePipeline)
    pipeline.manager = SimpleNamespace(stop_all=lambda **kwargs: actions.append(("core_stop", kwargs)))
    def close():
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
