import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.observability.dashboard.run_projection import (
    list_benchmark_runs,
)


class BenchmarkOwner:
    def __init__(self):
        self.calls = []

    def list_benchmark_jobs(self, **kwargs):
        self.calls.append(kwargs)
        result = {
            "schema": "anvil-serving.benchmark-job-list/v1",
            "items": [{
                "native_id": "jobs/2026/7",
                "suite": "context",
                "profile": "smoke",
                "model": "local-model",
                "native_state": "completed",
                "submitted_at": "2026-09-19T00:00:00Z",
                "updated_at": "2026-09-19T00:01:00Z",
                "started_at": "2026-09-19T00:00:01Z",
                "finished_at": "2026-09-19T00:01:00Z",
                "artifact": {"schema": "artifact/v1", "sha256": "a" * 64},
            }],
            "next_cursor": None,
            "source": {
                "id": "controller-a",
                "status": "fresh",
                "observed_at": "2026-09-19T00:01:01Z",
                "deadline_seconds": 1.0,
            },
        }
        if kwargs.get("refresh_refs"):
            result["refresh"] = {"items": [{
                "suite": "context", "run_id": "benchmark-fixture-1", "native_state": "completed", "updated_at": "2026-09-19T00:01:02Z",
            }], "partial": False}
        return result


def test_benchmark_projection_authorizes_before_owner_rows_or_cursor():
    owner = BenchmarkOwner()
    with pytest.raises(ObservatoryError) as exc:
        list_benchmark_runs(owner, can_read=lambda _: False, resource_id="evaluation.runs")
    assert exc.value.code == "forbidden"
    assert owner.calls == []


def test_benchmark_refresh_refs_authorize_before_validation_or_owner_call():
    owner = BenchmarkOwner()
    with pytest.raises(ObservatoryError) as exc:
        list_benchmark_runs(owner, can_read=lambda _: False, resource_id="evaluation.runs", refresh_refs=[])
    assert exc.value.code == "forbidden"
    assert owner.calls == []
