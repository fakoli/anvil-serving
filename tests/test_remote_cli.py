from __future__ import annotations

import json
import time

import pytest

from anvil_serving import cli, mcp
from anvil_serving.benchmarking import jobs_cli
from anvil_serving.benchmarking.artifacts import REQUIRED_MEASURED_IDENTITIES
from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.control_plane.controller.store import BenchmarkJobStore
from anvil_serving.control_plane.mcp.tools import benchmarks
from anvil_serving.observability.dashboard.access import Session
from anvil_serving.observability.dashboard.console import Console


def _declared_identities(**overrides) -> dict:
    values = {name: {} for name in REQUIRED_MEASURED_IDENTITIES}
    values.update(overrides)
    return {"identities": values}


def _spec(suite: str = "context", parameters: dict | None = None) -> str:
    values = {"instance_ids": ["django__django-11099"]} if suite == "swe" else {}
    values.update(parameters or {})
    return json.dumps(
        {
            "schema": JOB_SPEC_SCHEMA,
            "run_id": f"{suite}-001",
            "ownership_id": "campaign-001",
            "suite": suite,
            "profile": "smoke",
            "endpoint": {
                "base_url": "http://127.0.0.1:8000/v1",
                "model": "deepseek",
            },
            "worker": {"id": "benchmark-worker"},
            "submitted_at": "2026-08-03T12:00:00Z",
            "timeout_s": 600,
            "parameters": values,
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
    monkeypatch.setenv("ANVIL_COMMAND_HOST", "controller-a")
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
    expected_ref = {
        "schema": "anvil-serving.run-correlation/v1", "issuer": "controller-a",
        "namespace": "benchmark-job", "native_id": "agentic-001",
    }
    assert result["data"]["job"]["job_ref"] == expected_ref
    status = mcp.call_tool(
        "benchmark_job_status", {"suite": "agentic", "run_id": "agentic-001"}
    )
    assert status["data"]["spec"] == result["data"]["job"]["spec"]
    assert status["data"]["job_ref"] == expected_ref
    listed = mcp.call_tool("benchmark_job_list", {"limit": 1})
    assert listed["data"]["items"] == [{
        "native_id": "agentic-001",
        "suite": "agentic",
        "profile": "smoke",
        "model": "deepseek",
        "native_state": "queued",
        "submitted_at": "2026-08-03T12:00:00Z",
        "updated_at": "2026-08-03T12:00:00Z",
        "started_at": None,
        "finished_at": None,
        "artifact": None,
        "correlation_id": None,
    }]
    assert listed["data"]["source"]["id"] == "controller-a"


def test_disposable_cli_job_projects_through_supported_mcp_and_console(monkeypatch, tmp_path, capsys):
    """Prove the first owner path without starting an inference benchmark."""
    database = tmp_path / "jobs.sqlite3"
    run_root = tmp_path / "runs"
    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(database))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ANVIL_COMMAND_HOST", "controller-a")
    monkeypatch.setattr(
        "anvil_serving.benchmarking.jobs_cli.launch_benchmark_job",
        lambda **_kwargs: {"launched": True, "pid": 123},
    )

    assert cli.main([
        "eval", "benchmark", "context", "submit", "--spec-json", _spec(), "--confirm",
    ]) == 0
    submitted = json.loads(capsys.readouterr().out)
    assert submitted["data"]["worker"] == {"launched": True, "pid": 123}

    store = BenchmarkJobStore(str(database), run_root=str(run_root))
    store.claim("context-001")
    store.transition("context-001", "completed", results={"proof": "synthetic"})

    assert "benchmark_job_list" in {item["name"] for item in mcp.list_tools()}
    listed = mcp.call_tool("benchmark_job_list", {"limit": 1})
    assert listed["ok"] is True
    native = listed["data"]["items"][0]
    assert native["native_id"] == "context-001"
    assert native["artifact"]["sha256"]

    class MCPListOwner:
        def list_benchmark_jobs(self, *, limit=100, cursor=None):
            result = mcp.call_tool("benchmark_job_list", {"limit": limit, **(
                {"cursor": cursor} if cursor is not None else {}
            )})
            assert result["ok"] is True
            return result["data"]

    console = Console({
        "origin": "https://console.example.test", "base_path": "/", "operate": False,
        "users": [{"id": "reader", "username": "reader", "role": "viewer",
                   "resources": ["benchmark.runs"], "actions": []}],
        "authentication": {"mode": "legacy"}, "state_path": str(tmp_path / "intents.sqlite3"),
        "prometheus_url": "http://127.0.0.1:9090", "inventory": {},
        "runs": {"benchmark": {"resource_id": "benchmark.runs"}},
    }, metrics=object(), adapter=MCPListOwner(), authenticate=lambda _user, _password: False)
    try:
        session = Session("synthetic", "csrf", console.access.users["reader"], time.time() + 60)
        projected = console.read("runs/benchmark", {"limit": "1"}, session)
    finally:
        console.close()

    assert projected["sources"][0]["id"] == "controller-a"
    assert projected["items"][0]["native_id"] == "context-001"
    assert projected["items"][0]["evidence_refs"] == [{
        "owner_id": "controller-a", "artifact_id": "context-001",
        "sha256": native["artifact"]["sha256"],
    }]


def test_remote_capability_is_declared_without_ssh_fallback():
    tools = {item["name"] for item in mcp.list_tools()}
    assert {
        "benchmark_job_submit",
        "benchmark_job_status",
        "benchmark_job_list",
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


def test_missing_stage_artifact_returns_typed_not_found(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(tmp_path / "runs"))

    assert cli.main([
        "eval",
        "benchmark",
        "context",
        "artifact",
        "--run-id",
        "missing-run",
        "--path",
        "evidence/0-context.json",
    ]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "job_not_found"


@pytest.mark.parametrize("suite", ["context", "agentic", "swe"])
def test_submit_dry_run_validates_without_store(monkeypatch, suite):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("dry-run opened job store"))
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **_kwargs: pytest.fail("dry-run launched worker"))
    result = jobs_cli.run([suite, "submit", "--spec-json", _spec(suite), "--dry-run"])
    assert result["suite"] == suite
    assert result["dry_run"] is True


def test_submit_dry_run_rejects_malformed_without_store(monkeypatch):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("malformed dry-run opened job store"))
    with pytest.raises(Exception):
        jobs_cli.run(["context", "submit", "--spec-json", "{}", "--dry-run"])


def test_submit_dry_run_cli_preview_preserves_normalized_target(monkeypatch, capsys):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("dry-run opened job store"))
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **_kwargs: pytest.fail("dry-run launched worker"))
    assert cli.main(["eval", "benchmark", "context", "submit", "--spec-json", _spec(), "--confirm", "--dry-run"]) == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["endpoint"] == {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"}
    assert data["ownership_id"] == "campaign-001"
    assert data["worker"] == {"id": "benchmark-worker"}
    assert data["suite_admission"]["request_controls"]["thinking_mode"] == "default"


@pytest.mark.parametrize(("suite", "parameters", "code"), [
    ("context", {"thinking_mode": "bogus"}, "bad_thinking_mode"),
    ("agentic", {
        "thinking_mode": "enabled", "reasoning_effort": "max", "force_nonempty_content": True,
    }, "conflicting_reasoning_controls"),
    ("swe", {"force_nonempty_content": True}, "force_nonempty_content_requires_enabled_thinking"),
])
def test_submit_rejects_invalid_native_controls_before_store_or_launch(
    monkeypatch, suite, parameters, code,
):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("invalid submit opened job store"))
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **_kwargs: pytest.fail("invalid submit launched worker"))
    for extra in (["--dry-run"], ["--confirm"]):
        with pytest.raises(BenchmarkJobError) as raised:
            jobs_cli.run([suite, "submit", "--spec-json", _spec(suite, parameters), *extra])
        assert getattr(raised.value, "code", None) == code


@pytest.mark.parametrize(("suite", "parameters", "code"), [
    ("context", {"thinking_mode": "bogus"}, "bad_thinking_mode"),
    ("agentic", {
        "thinking_mode": "enabled", "reasoning_effort": "max", "force_nonempty_content": True,
    }, "conflicting_reasoning_controls"),
    ("swe", {"force_nonempty_content": True}, "force_nonempty_content_requires_enabled_thinking"),
])
def test_mcp_submit_rejects_invalid_native_controls_before_store(monkeypatch, suite, parameters, code):
    monkeypatch.setattr(benchmarks, "_benchmark_job_store", lambda: pytest.fail("invalid MCP submit opened job store"))
    result = mcp.call_tool("benchmark_job_submit", {
        "suite": suite, "spec_json": _spec(suite, parameters), "confirm": True,
    })
    assert result["ok"] is False
    assert result["error"]["code"] == code


@pytest.mark.parametrize(("suite", "parameters"), [
    ("context", {"thinking_mode": "enabled", "force_nonempty_content": True}),
    ("agentic", {"thinking_mode": "enabled", "clear_thinking": True}),
    ("swe", {"thinking_mode": "enabled", "clear_thinking": True}),
    ("context", {"instance_ids": ["django__django-11099"]}),
    ("agentic", {"instance_ids": ["django__django-11099"]}),
    ("swe", {"case_ids": ["planning"]}),
    ("swe", {"case_limit": 1}),
])
def test_submit_rejects_suite_incompatible_parameters_before_store_or_launch(
    monkeypatch, suite, parameters,
):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("incompatible submit opened job store"))
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **_kwargs: pytest.fail("incompatible submit launched worker"))
    for extra in (["--dry-run"], ["--confirm"]):
        with pytest.raises(BenchmarkJobError) as raised:
            jobs_cli.run([suite, "submit", "--spec-json", _spec(suite, parameters), *extra])
        assert raised.value.code == "suite_incompatible_parameter"


@pytest.mark.parametrize(("suite", "parameters"), [
    ("context", {"force_nonempty_content": True}),
    ("agentic", {"clear_thinking": True}),
    ("swe", {"clear_thinking": True}),
    ("context", {"instance_ids": ["django__django-11099"]}),
    ("agentic", {"instance_ids": ["django__django-11099"]}),
    ("swe", {"case_ids": ["planning"]}),
    ("swe", {"case_limit": 1}),
])
def test_mcp_submit_rejects_suite_incompatible_parameters_before_store(
    monkeypatch, suite, parameters,
):
    monkeypatch.setattr(benchmarks, "_benchmark_job_store", lambda: pytest.fail("incompatible MCP submit opened job store"))
    result = mcp.call_tool("benchmark_job_submit", {
        "suite": suite, "spec_json": _spec(suite, parameters), "confirm": True,
    })
    assert result["ok"] is False
    assert result["error"]["code"] == "suite_incompatible_parameter"


@pytest.mark.parametrize("parameters", [
    {"identities": {"recipe": {}}},
    _declared_identities(model="spoofed-scalar"),
])
def test_submit_rejects_malformed_declared_identities_before_store_or_launch(
    monkeypatch, parameters,
):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("invalid submit opened job store"))
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **_kwargs: pytest.fail("invalid submit launched worker"))
    for extra in (["--dry-run"], ["--confirm"]):
        with pytest.raises(BenchmarkJobError) as raised:
            jobs_cli.run(["context", "submit", "--spec-json", _spec("context", parameters), *extra])
        assert raised.value.code == "bad_identities"


@pytest.mark.parametrize("parameters", [
    {"identities": {"recipe": {}}},
    _declared_identities(model="spoofed-scalar"),
])
def test_mcp_submit_rejects_malformed_declared_identities_before_store(monkeypatch, parameters):
    monkeypatch.setattr(benchmarks, "_benchmark_job_store", lambda: pytest.fail("invalid MCP submit opened job store"))
    result = mcp.call_tool("benchmark_job_submit", {
        "suite": "context", "spec_json": _spec("context", parameters), "confirm": True,
    })
    assert result["ok"] is False
    assert result["error"]["code"] == "bad_identities"


def test_remote_submit_dry_run_is_not_dispatched(monkeypatch):
    path, rest, unknown, _siblings = cli._resolve(
        ["eval", "benchmark", "context", "submit", "--spec-json", _spec(), "--dry-run"]
    )
    assert unknown is None
    from anvil_serving import mcp
    monkeypatch.setattr(mcp, "call_tool", lambda *_args, **_kwargs: pytest.fail("remote dry-run dispatched"))
    with pytest.raises(cli.UsageError, match="--dry-run is not supported"):
        cli._remote_arguments(path[-1], rest, confirmed=False)

@pytest.mark.parametrize(("suite", "parameters", "code"), [
    ("context", {"offline": "false"}, "bad_offline"),
    ("context", {"client_topology": "bogus"}, "bad_client_topology"),
    ("context", {"model_host_id": ["model-host"]}, "bad_model_host_id"),
    ("context", {"model_host_id": " model-host "}, "bad_model_host_id"),
    ("swe", {"paired_image_repo_digests": []}, "bad_swe_image_policy"),
])
def test_submit_rejects_mis_typed_common_or_swe_image_parameters_before_store_or_launch(
    monkeypatch, suite, parameters, code,
):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("invalid submit opened job store"))
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **_kwargs: pytest.fail("invalid submit launched worker"))
    for extra in (["--dry-run"], ["--confirm"]):
        with pytest.raises(BenchmarkJobError) as raised:
            jobs_cli.run([suite, "submit", "--spec-json", _spec(suite, parameters), *extra])
        assert raised.value.code == code


@pytest.mark.parametrize(("suite", "parameters", "code"), [
    ("context", {"offline": "false"}, "bad_offline"),
    ("context", {"client_topology": "bogus"}, "bad_client_topology"),
    ("context", {"model_host_id": ["model-host"]}, "bad_model_host_id"),
    ("context", {"model_host_id": " model-host "}, "bad_model_host_id"),
    ("swe", {"paired_image_repo_digests": []}, "bad_swe_image_policy"),
])
def test_mcp_submit_rejects_mis_typed_common_or_swe_image_parameters_before_store(
    monkeypatch, suite, parameters, code,
):
    monkeypatch.setattr(benchmarks, "_benchmark_job_store", lambda: pytest.fail("invalid MCP submit opened job store"))
    result = mcp.call_tool("benchmark_job_submit", {
        "suite": suite, "spec_json": _spec(suite, parameters), "confirm": True,
    })
    assert result["ok"] is False
    assert result["error"]["code"] == code
