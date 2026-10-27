import json
import threading
from concurrent.futures import ThreadPoolExecutor

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


@pytest.mark.parametrize("change", [
    None, {}, {"totalSeconds": True}, {"deadlineNs": 1}, {"startedNs": -1},
    {"reservationSeconds": 0}, {"marginSeconds": float("nan")}, {"marginSeconds": 60},
    {"runId": "bad"}, {"other": 1}, {"totalSeconds": 10**400},
])
def test_invalid_runtime_is_closed(monkeypatch, change):
    configured(monkeypatch)
    value = json.loads(runtime.os.environ[runtime.ENVIRONMENT])
    if change is not None:
        value.update(change)
    monkeypatch.setenv(runtime.ENVIRONMENT, "" if change is None else json.dumps(value if change else {}))
    runtime._runtime.cache_clear()
    try:
        status = runtime.runtime_status()
        assert not status["acceptingNewWork"] and status["reason"] == "worker_budget_unknown"
        with pytest.raises(runtime.WorkerRuntimeRefused):
            runtime.require_admission({}, busy=False)
    finally:
        runtime._runtime.cache_clear()


def test_queue_lock_serializes_and_rejects_wrong_or_smaller_reservations(monkeypatch):
    _, extra = configured(monkeypatch)
    try:
        lock, queue = threading.RLock(), []
        def put():
            with lock:
                try:
                    runtime.require_admission(extra, busy=bool(queue))
                except runtime.WorkerRuntimeRefused as exc:
                    return exc.reason
                queue.append("one")
                return "accepted"
        with ThreadPoolExecutor(max_workers=2) as executor:
            assert sorted(executor.map(lambda _: put(), range(2))) == ["accepted", "worker_busy"]
        assert len(queue) == 1
        extra["anvil_runtime"]["runId"] = "0" * 32
        with pytest.raises(runtime.WorkerRuntimeRefused, match="worker_instance_changed"):
            runtime.require_admission(extra, busy=False)
        extra["anvil_runtime"]["requiredSeconds"] = 61
        with pytest.raises(runtime.WorkerRuntimeRefused, match="worker_reservation_invalid"):
            runtime.require_admission(extra, busy=False)
        extra["anvil_runtime"]["requiredSeconds"] = 1
        clock = runtime._runtime().deadline - 64_000_000_000
        monkeypatch.setattr(runtime.time, "monotonic_ns", lambda: clock)
        # A caller's small estimate never reduces the fixed 60+5-second reservation.
        extra["anvil_runtime"]["runId"] = runtime._runtime().run_id
        with pytest.raises(runtime.WorkerRuntimeRefused, match="worker_budget_insufficient"):
            runtime.require_admission(extra, busy=False)
    finally:
        runtime._runtime.cache_clear()
