from __future__ import annotations

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA


def _spec(parameters, *, suite="swe"):
    return {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": "regrade-001",
        "ownership_id": "campaign-001",
        "suite": suite,
        "profile": "smoke",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        "worker": {"id": "benchmark-worker"},
        "submitted_at": "2026-10-24T12:00:00Z",
        "timeout_s": 600,
        "parameters": parameters,
    }


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_cleanup_surface_checks_durable_owner_before_dispatch(monkeypatch, surface):
    from anvil_serving.benchmarking import jobs_cli
    from anvil_serving.control_plane.mcp.tools import benchmarks
    from anvil_serving.control_plane.mcp.errors import ToolError

    class Store:
        def status(self, run_id):
            assert run_id == "source"
            return {"spec": {"suite": "swe", "ownership_id": "owner"}, "state": "completed"}

    calls = []
    module = jobs_cli if surface == "cli" else benchmarks
    monkeypatch.setattr(module, "_store" if surface == "cli" else "_benchmark_job_store", Store)
    monkeypatch.setattr(module, "cleanup_benchmark_job", lambda store, run: calls.append(run) or {"cleaned": True})

    def cleanup(owner):
        if surface == "cli":
            return jobs_cli.run(["swe", "cleanup", "--run-id", "source", "--ownership-id", owner, "--confirm"])
        return benchmarks.tool_benchmark_harness_cleanup({
            "suite": "swe", "run_id": "source", "ownership_id": owner, "confirm": True,
        })

    with pytest.raises((BenchmarkJobError, ToolError)) as exc:
        cleanup("other")
    assert exc.value.code == "ownership_mismatch"
    assert not calls
    cleanup("owner")
    assert calls == ["source"]
