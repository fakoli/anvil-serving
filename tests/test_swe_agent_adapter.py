from __future__ import annotations

import json

import pytest

from anvil_serving.benchmarking import swe_agent_adapter as adapter


class _State:
    def __init__(self, ordinal, error=None, sleep=None):
        self.attempt_number = ordinal
        self.outcome = type("Outcome", (), {"exception": lambda self: error})()
        self.next_action = None if sleep is None else type("Action", (), {"sleep": sleep})()


class _Retrying:
    def __init__(self):
        self.before = None
        self.before_sleep = None


def _ready(recorder):
    recorder.record("adapter_ready", mini_revision=adapter.MINI_REVISION, source_hashes=adapter.SOURCE_HASHES,
                    adapter_sha256="a" * 64, dependencies=adapter.SUPPORTED_DEPENDENCIES,
                    transport_seam="httpx.Client.send", retry_seam="minisweagent.models.litellm_model.retry",
                    clock={"wall": "UTC system clock", "wall_precision_s": 0.000001,
                           "wall_resolution_s": 0.000001, "duration": "monotonic",
                           "duration_resolution_s": 0.000001, "synchronized": None})


def test_observer_records_ordered_transport_and_agent_retry_layers(tmp_path):
    events = tmp_path / "events.jsonl"
    recorder = adapter.EventRecorder(events)
    _ready(recorder)
    calls = []

    class Response:
        def __init__(self, status, headers=None):
            self.status_code, self.headers = status, headers or {}

    class Client:
        responses = [Response(504), Response(503, {"x-request-id": "sdk-2"}),
                     Response(200, {"openai-request-id": "provider-3", "authorization": "nope"})]
        def send(self, request, *args, **kwargs):
            calls.append((request, args, kwargs))
            return self.responses.pop(0)

    hook_calls = []
    class Lite:
        @staticmethod
        def retry(*args, **kwargs):
            value = _Retrying()
            value.before = lambda state: hook_calls.append(("before", state.attempt_number))
            value.before_sleep = lambda state: hook_calls.append(("before_sleep", state.next_action.sleep))
            return value

    class Default:
        def __init__(self):
            self.n_calls, self.instance_id = 0, "task-1"
            self.model = self
        def query(self):
            self.n_calls += 1
            retrying = Lite.retry("unchanged", named="unchanged")
            retrying.before(_State(1))
            Client().send("first", timeout="preserved")
            retrying.before_sleep(_State(1, TimeoutError(), 4.0))
            retrying.before(_State(2))
            Client().send("second")
            Client().send("third")
            return {"ok": True}

    restore = adapter.install_observers(default_agent=Default, litellm_model=Lite, httpx_client=Client, recorder=recorder)
    try:
        assert Default().query() == {"ok": True}
    finally:
        restore()
        recorder.close(outcome="completed")

    evidence = adapter.read_attempt_evidence(events, "task-1")
    transport = [item for item in evidence["events"] if item["event"] == "transport_response"]
    assert [item["http_status"] for item in transport] == [504, 503, 200]
    assert [item["sdk_transport_attempt_ordinal"] for item in transport] == [1, 2, 3]
    assert [item["agent_attempt_ordinal"] for item in transport] == [1, 2, 2]
    assert transport[0]["ids"] == {}
    assert transport[1]["ids"] == {"http_request_id": "sdk-2"}
    assert transport[2]["ids"] == {"openai_request_id": "provider-3"}
    assert "authorization" not in json.dumps(transport)
    retry = next(item for item in evidence["events"] if item["event"] == "agent_retry_planned")
    assert retry["retry_reason_type"] == "TimeoutError"
    assert retry["planned_backoff_s"] == 4.0
    assert calls[0] == ("first", (), {"timeout": "preserved"})
    assert hook_calls == [("before", 1), ("before_sleep", 4.0), ("before", 2)]
    assert evidence["status"] == "complete"


def test_sdk_retry_observer_preserves_openai_decision_and_delay(tmp_path):
    recorder = adapter.EventRecorder(tmp_path / "events.jsonl")
    _ready(recorder)

    class Client:
        def send(self, *_args, **_kwargs):
            return type("Response", (), {"status_code": 200, "headers": {}})()

    class Lite:
        retry = staticmethod(lambda: _Retrying())

    class Default:
        n_calls = 0
        instance_id = "task-sdk"
        def query(self):
            self.n_calls += 1
            base = Base()
            assert base._should_retry(type("Response", (), {"status_code": 503})()) is True
            assert base._calculate_retry_timeout("unchanged") == 1.75

    class Base:
        def _should_retry(self, response):
            return response.status_code == 503
        def _calculate_retry_timeout(self, *args, **kwargs):
            assert args == ("unchanged",) and kwargs == {}
            return 1.75

    restore = adapter.install_observers(default_agent=Default, litellm_model=Lite, httpx_client=Client, recorder=recorder,
                                        openai_base=Base)
    try:
        Default().query()
    finally:
        restore()
        recorder.close(outcome="completed")
    event = next(item for item in adapter.read_attempt_evidence(tmp_path / "events.jsonl", "task-sdk")["events"]
                 if item["event"] == "sdk_retry_planned")
    assert event["retry_reason_type"] == "HTTP_503"
    assert event["planned_backoff_s"] == 1.75


def _budgets(**changes):
    return {"max_model_calls": 3, "max_completion_tokens": 8, "deadline_s": 30,
            "max_retries": 1, "min_free_disk_bytes": 0, **changes}


def _request(**body):
    from types import SimpleNamespace
    return SimpleNamespace(method="POST", url=SimpleNamespace(path="/v1/chat/completions"),
                           content=json.dumps({"max_tokens": 8, **body}).encode(),
                           extensions={"timeout": {"read": 60, "connect": 1}})


def test_v2_budget_counts_all_failed_sdk_and_agent_attempts_across_instances(tmp_path):
    from types import SimpleNamespace
    recorder = adapter.EventRecorder(tmp_path / "events.jsonl")
    _ready(recorder)
    calls = []
    class Client:
        def send(self, request, **kwargs):
            calls.append(request)
            if len(calls) == 1:
                raise TimeoutError("private response")
            return SimpleNamespace(status_code=200, headers={})
    class Lite:
        @staticmethod
        def retry(**kwargs):
            assert adapter.BudgetStopped in kwargs["abort_exceptions"]
            return _Retrying()
    class Agent:
        n_calls = 0
        def __init__(self, instance): self.instance_id = instance
        def query(self):
            retrying = Lite.retry(abort_exceptions=[KeyboardInterrupt])
            retrying.before(_State(1))
            try:
                return Client().send(_request())
            except TimeoutError:
                retrying.before(_State(2))
                return Client().send(_request())
    restore = adapter.install_observers(default_agent=Agent, litellm_model=Lite, httpx_client=Client,
                                        recorder=recorder, budgets=_budgets())
    try:
        Agent("first").query()
        Agent("second").query()
        with pytest.raises(adapter.BudgetStopped, match="max_model_calls"):
            Agent("third").query()
    finally:
        restore(); recorder.close(outcome="failed")
    assert len(calls) == 3
    assert 0 < calls[0].extensions["timeout"]["read"] <= 30
    assert calls[0].extensions["timeout"]["connect"] == 1
    evidence = adapter.read_attempt_evidence(recorder.path)
    assert evidence["status"] == "complete"
    assert evidence["budget"]["model_calls"] == 3
    assert evidence["budget"]["reason"] == "max_model_calls"
    assert "private response" not in json.dumps(evidence)


def test_v2_deadline_uses_monotonic_and_checks_transport_return(tmp_path, monkeypatch):
    from types import SimpleNamespace
    now = [10.0]
    monkeypatch.setattr(adapter.time, "monotonic", lambda: now[0])
    recorder = adapter.EventRecorder(tmp_path / "events.jsonl")
    _ready(recorder)
    class Client:
        def send(self, request, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert request.extensions["timeout"]["read"] == 30
            now[0] += 31
            return SimpleNamespace(status_code=200, headers={})
    class Lite: retry = staticmethod(lambda **kwargs: _Retrying())
    class Agent:
        n_calls, instance_id = 0, "deadline-task"
        def query(self): return Client().send(_request())
    restore = adapter.install_observers(default_agent=Agent, litellm_model=Lite, httpx_client=Client,
                                        recorder=recorder, budgets=_budgets())
    try:
        with pytest.raises(adapter.BudgetStopped, match="deadline_s"):
            Agent().query()
    finally:
        restore(); recorder.close(outcome="failed")
    evidence = adapter.read_attempt_evidence(recorder.path)
    assert evidence["budget"]["elapsed_s"] == 31
    assert evidence["budget"]["model_calls"] == 1
    assert any(event["event"] == "transport_response" for event in evidence["events"])


def test_v2_rejects_unqualified_model_config_before_run():
    argv = ["--output", "output", "--model", "openai/local", "--subset", "dataset", "--split", "test",
            "--filter", "task", "--workers", "1", "--environment-class", "docker", "--config", "pinned"]
    config = {"model": {"model_class": "litellm", "model_kwargs": {"custom_llm_provider": "openai", "max_tokens": 8}}}
    adapter.validate_budget_agent_config(argv, _budgets(), lambda _: config, lambda value: value)
    config["model"]["model_class"] = "other"
    with pytest.raises(ValueError, match="model configuration"):
        adapter.validate_budget_agent_config(argv, _budgets(), lambda _: config, lambda value: value)
    with pytest.raises(ValueError, match="agent arguments"):
        adapter.validate_budget_agent_config([*argv, "--model-class", "other"], _budgets(), lambda _: {}, lambda value: value)


def test_recorder_close_is_serialized_idempotent_and_late_writes_do_not_raise(tmp_path):
    import threading
    recorder = adapter.EventRecorder(tmp_path / "events.jsonl")
    _ready(recorder)
    barrier = threading.Barrier(3)
    def close():
        barrier.wait()
        recorder.close(outcome="completed")
    threads = [threading.Thread(target=close) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    rows = [json.loads(line) for line in recorder.path.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["adapter_ready", "adapter_finished"]
    before = recorder.path.read_bytes()
    assert recorder.record("late_event") is False
    assert recorder.write_failed is True
    recorder.close(outcome="failed")
    assert recorder.path.read_bytes() == before
