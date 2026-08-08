from __future__ import annotations

import json

from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.control_plane.mcp.tools import benchmarks


def _spec():
    return {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": "preflight-remote-001",
        "ownership_id": "campaign-001",
        "suite": "context",
        "profile": "context-smoke-v1",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"},
        "worker": {"id": "benchmark-worker"},
        "submitted_at": "2026-08-03T12:00:00Z",
        "timeout_s": 600,
        "parameters": {},
    }


def test_controller_preflight_preserves_typed_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(tmp_path / "runs"))
    expected = {"schema": "anvil-serving.benchmark-preflight/v1", "passed": True}
    monkeypatch.setattr(benchmarks, "run_benchmark_preflight", lambda *_args, **_kwargs: expected)
    result = benchmarks.tool_benchmark_job_preflight(
        {
            "suite": "context",
            "spec_json": json.dumps(_spec()),
            "requirements_json": json.dumps({"min_free_disk_bytes": 1}),
        }
    )
    assert result == {"ok": True, "data": expected}


def test_controller_preflight_rejects_suite_mismatch():
    result = benchmarks.FAMILY.tools["benchmark_job_preflight"]["handler"]
    try:
        result({"suite": "swe", "spec_json": json.dumps(_spec())})
    except Exception as exc:
        assert getattr(exc, "code", None) == "suite_mismatch"
    else:
        raise AssertionError("suite mismatch was accepted")
