from __future__ import annotations

import json

from anvil_serving import cli, mcp
from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.control_plane.mcp.tools import benchmarks


def _spec(suite: str = "context") -> str:
    return json.dumps(
        {
            "schema": JOB_SPEC_SCHEMA,
            "run_id": f"{suite}-001",
            "ownership_id": "campaign-001",
            "suite": suite,
            "profile": f"{suite}-smoke-v1",
            "endpoint": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "deepseek",
            },
            "worker": {"id": "benchmark-worker"},
            "submitted_at": "2026-08-03T12:00:00Z",
            "timeout_s": 600,
            "parameters": {},
        }
    )


def test_local_context_job_controls_persist(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(
        "anvil_serving.benchmarking.jobs_cli.launch_benchmark_job",
        lambda **_kwargs: {"launched": True, "pid": 123},
    )
    assert cli.main(
        ["eval", "benchmark", "context", "submit", "--spec-json", _spec(), "--confirm"]
    ) == 0
    submitted = json.loads(capsys.readouterr().out)
    assert submitted["ok"] is True
    assert cli.main(
        ["eval", "benchmark", "context", "status", "--run-id", "context-001"]
    ) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["data"]["state"] == "queued"


def test_remote_job_tools_share_the_portable_spec(monkeypatch, tmp_path):
    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(tmp_path / "runs"))
    monkeypatch.setattr(
        benchmarks,
        "launch_benchmark_job",
        lambda **_kwargs: {"launched": True, "pid": 123},
    )
    result = mcp.call_tool(
        "benchmark_job_submit",
        {
            "suite": "agentic",
            "spec_json": _spec("agentic"),
            "detach": True,
            "confirm": True,
        },
    )
    assert result["ok"] is True
    status = mcp.call_tool(
        "benchmark_job_status", {"suite": "agentic", "run_id": "agentic-001"}
    )
    assert status["data"]["spec"] == result["data"]["job"]["spec"]


def test_remote_capability_is_declared_without_ssh_fallback():
    tools = {item["name"] for item in mcp.list_tools()}
    assert {
        "benchmark_job_submit",
        "benchmark_job_status",
        "benchmark_job_logs",
        "benchmark_job_cancel",
        "benchmark_job_artifact",
    } <= tools


def test_suite_mismatch_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(tmp_path / "runs"))
    result = mcp.call_tool(
        "benchmark_job_submit",
        {"suite": "swe", "spec_json": _spec("context"), "confirm": True},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "suite_mismatch"
