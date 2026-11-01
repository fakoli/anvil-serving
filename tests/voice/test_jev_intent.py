import queue
import sys
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
