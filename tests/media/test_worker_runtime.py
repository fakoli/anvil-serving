
import pytest

from anvil_serving.media import worker_runtime as runtime


def configured(monkeypatch):
    clock = [1_000_000_000_000]
    monkeypatch.setattr(runtime.time, "monotonic_ns", lambda: clock[0])
    monkeypatch.setenv(runtime.ENVIRONMENT, "inherited untrusted value")
    environment, deadline = runtime.child_environment({
        "timeout_seconds": 120, "admission_seconds": 60, "admission_margin_seconds": 5})
    assert deadline == clock[0] + 120_000_000_000
    monkeypatch.setenv(runtime.ENVIRONMENT, environment[runtime.ENVIRONMENT])
    runtime._runtime.cache_clear()
    status = runtime.runtime_status()
    extra = {"client_id": "anvil-job_1", "anvil_runtime": {
        "runId": status["runId"], "requiredSeconds": 60}}
    return clock, extra


def test_deadline_counts_idle_and_never_renews_or_uses_wall_clock(monkeypatch):
    clock, extra = configured(monkeypatch)
    try:
        first = runtime.runtime_status()
        monkeypatch.setattr(runtime.time, "time", lambda: -10**10)
        clock[0] += 54_000_000_000
        assert runtime.runtime_status()["remainingSeconds"] == 66
        runtime.require_admission(extra, busy=False)
        monkeypatch.setenv(runtime.ENVIRONMENT, "later request cannot change cached deadline")
        clock[0] += 1_000_000_000
        assert runtime.runtime_status()["runId"] == first["runId"]
        with pytest.raises(runtime.WorkerRuntimeRefused) as refused:
            runtime.require_admission(extra, busy=False)
        assert refused.value.reason == "worker_budget_insufficient"
        assert refused.value.response() == {
            "schema": runtime.REFUSAL_SCHEMA, "accepted": False,
            "reason": "worker_budget_insufficient", "requestRunId": first["runId"],
            "clientId": "anvil-job_1"}
        clock[0] += 200_000_000_000
        assert runtime.runtime_status()["remainingSeconds"] == 0
    finally:
        runtime._runtime.cache_clear()
