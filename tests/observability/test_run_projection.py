import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.observability.dashboard.run_projection import (
    list_benchmark_runs,
    projected_run_id,
)


class BenchmarkOwner:
    def __init__(self):
        self.calls = []

    def list_benchmark_jobs(self, **kwargs):
        self.calls.append(kwargs)
        return {
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


def test_benchmark_projection_authorizes_before_owner_rows_or_cursor():
    owner = BenchmarkOwner()
    with pytest.raises(ObservatoryError) as exc:
        list_benchmark_runs(owner, can_read=lambda _: False, resource_id="evaluation.runs")
    assert exc.value.code == "forbidden"
    assert owner.calls == []


def test_benchmark_projection_keeps_native_ids_opaque_and_artifacts_pathless():
    owner = BenchmarkOwner()
    result = list_benchmark_runs(
        owner, can_read=lambda resource: resource == "evaluation.runs",
        resource_id="evaluation.runs", limit=1,
    )
    row = result["items"][0]
    assert row["id"] == projected_run_id("controller-a", "benchmark", "jobs/2026/7")
    assert row["native_id"] == "jobs/2026/7"
    assert row["correlation_id"] is None
    assert row["id"] != projected_run_id("controller-b", "benchmark", "jobs/2026/7")
    assert row["evidence_refs"] == [{
        "owner_id": "controller-a", "artifact_id": "jobs/2026/7", "sha256": "a" * 64,
    }]
    assert "path" not in repr(result)
    assert owner.calls == [{"limit": 1, "cursor": None}]


def test_benchmark_projection_uses_each_declared_owner_for_identical_native_ids():
    first, second = BenchmarkOwner(), BenchmarkOwner()
    original = second.list_benchmark_jobs
    def second_owner(**kwargs):
        response = original(**kwargs)
        response["source"]["id"] = "controller-b"
        return response
    second.list_benchmark_jobs = second_owner

    def can_read(resource):
        return resource == "evaluation.runs"

    first_row = list_benchmark_runs(first, can_read=can_read, resource_id="evaluation.runs")["items"][0]
    second_row = list_benchmark_runs(second, can_read=can_read, resource_id="evaluation.runs")["items"][0]
    assert first_row["native_id"] == second_row["native_id"]
    assert first_row["id"] != second_row["id"]


def test_benchmark_correlation_uses_only_its_declared_owner_and_job_native_id():
    first, second = BenchmarkOwner(), BenchmarkOwner()
    original = second.list_benchmark_jobs

    def valid_first(**kwargs):
        response = BenchmarkOwner().list_benchmark_jobs(**kwargs)
        response["items"][0]["native_id"] = "context-001"
        response["items"][0]["correlation_id"] = "forged-bare-string"
        return response

    def valid_second(**kwargs):
        response = original(**kwargs)
        response["source"]["id"] = "controller-b"
        response["items"][0]["native_id"] = "context-001"
        response["items"][0]["correlation_id"] = "forged-bare-string"
        return response

    first.list_benchmark_jobs = valid_first
    second.list_benchmark_jobs = valid_second
    first_row = list_benchmark_runs(first, can_read=lambda _: True, resource_id="evaluation.runs")["items"][0]
    second_row = list_benchmark_runs(second, can_read=lambda _: True, resource_id="evaluation.runs")["items"][0]
    assert first_row["correlation_id"].startswith("benchmark-job-")
    assert second_row["correlation_id"].startswith("benchmark-job-")
    assert first_row["correlation_id"] != second_row["correlation_id"]


def test_benchmark_projection_rejects_stale_or_undeclared_source_identity():
    owner = BenchmarkOwner()
    owner.list_benchmark_jobs = lambda **_kwargs: {
        **BenchmarkOwner().list_benchmark_jobs(),
        "source": {"id": "controller-a", "status": "stale", "observed_at": "2026-09-19T00:01:01Z", "deadline_seconds": 1.0},
    }
    with pytest.raises(ObservatoryError, match="fresh"):
        list_benchmark_runs(owner, can_read=lambda _: True, resource_id="evaluation.runs")


def test_benchmark_projection_preserves_partial_source_coverage():
    owner = BenchmarkOwner()
    original = owner.list_benchmark_jobs

    def partial_owner(**kwargs):
        response = original(**kwargs)
        response["source"]["partial"] = True
        return response

    owner.list_benchmark_jobs = partial_owner
    result = list_benchmark_runs(owner, can_read=lambda _: True, resource_id="evaluation.runs")

    assert result["sources"] == [{
        "id": "controller-a",
        "status": "fresh",
        "observed_at": "2026-09-19T00:01:01Z",
        "deadline_seconds": 1.0,
        "truncated": False,
        "partial": True,
    }]
