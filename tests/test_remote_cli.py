from __future__ import annotations

import json

import pytest

from anvil_serving import cli, mcp
from anvil_serving.benchmarking import jobs_cli
from anvil_serving.benchmarking.artifacts import REQUIRED_MEASURED_IDENTITIES
from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.control_plane.controller.store import BenchmarkJobStore
from anvil_serving.control_plane.mcp.tools import benchmarks


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


@pytest.fixture
def rejected_store(monkeypatch, tmp_path):
    store = BenchmarkJobStore(str(tmp_path / "jobs.sqlite3"), run_root=str(tmp_path / "runs"))
    monkeypatch.setattr(jobs_cli, "_store", lambda: store)
    monkeypatch.setattr(benchmarks, "_benchmark_job_store", lambda: store)
    monkeypatch.setattr(benchmarks, "launch_benchmark_job", lambda **_kwargs: pytest.fail("invalid MCP submit launched worker"))
    yield store
    for suite in ("context", "agentic", "swe"):
        assert store.status(f"{suite}-001") is None


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
            "spec_json": _spec("agentic", {"identities": {"model": {"alias": "candidate"}}}),
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
def test_submit_rejects_invalid_native_controls_before_run_creation_or_launch(
    monkeypatch, suite, parameters, code, rejected_store,
):
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **_kwargs: pytest.fail("invalid submit launched worker"))
    for extra in (["--dry-run"], ["--confirm"]):
        with pytest.raises(BenchmarkJobError) as raised:
            jobs_cli.run([suite, "submit", "--spec-json", _spec(suite, parameters), *extra])
        assert getattr(raised.value, "code", None) == code


@pytest.mark.parametrize("parameters", [
    {"identities": {"recipe": {}}},
    _declared_identities(),
    _declared_identities(model="spoofed-scalar"),
])
def test_submit_rejects_malformed_declared_identities_before_run_creation_or_launch(
    monkeypatch, parameters, rejected_store,
):
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **_kwargs: pytest.fail("invalid submit launched worker"))
    for extra in (["--dry-run"], ["--confirm"]):
        with pytest.raises(BenchmarkJobError) as raised:
            jobs_cli.run(["context", "submit", "--spec-json", _spec("context", parameters), *extra])
        assert raised.value.code == "bad_identities"


def test_remote_submit_dry_run_is_not_dispatched(monkeypatch):
    path, rest, unknown, _siblings = cli._resolve(
        ["eval", "benchmark", "context", "submit", "--spec-json", _spec(), "--dry-run"]
    )
    assert unknown is None
    from anvil_serving import mcp
    monkeypatch.setattr(mcp, "call_tool", lambda *_args, **_kwargs: pytest.fail("remote dry-run dispatched"))
    with pytest.raises(cli.UsageError, match="--dry-run is not supported"):
        cli._remote_arguments(path[-1], rest, confirmed=False)


@pytest.mark.parametrize("suite", ["aider", "performance"])
def test_file_inline_and_remote_submission_preserve_one_digest(monkeypatch, tmp_path, suite):
    from anvil_serving.benchmarking import submission

    spec = json.loads(_spec(suite))
    spec["profile"] = "pilot-v2"
    if suite == "performance":
        spec["parameters"] = {"runner": "lil", "workload": {"input_tokens": 0, "output_tokens": 32}, "request_count": 2}
    raw = json.dumps(spec, indent=2)
    file = tmp_path / "job.json"
    file.write_text(raw, encoding="utf-8")
    monkeypatch.setenv("ANVIL_BENCHMARK_JOB_DB", str(tmp_path / "jobs.sqlite3"))
    monkeypatch.setenv("ANVIL_BENCHMARK_RUN_ROOT", str(tmp_path / "runs"))
    launches = []
    monkeypatch.setattr(jobs_cli, "launch_benchmark_job", lambda **kw: launches.append(kw) or {"launched": True})
    monkeypatch.setattr(benchmarks, "launch_benchmark_job", lambda **kw: pytest.fail("retry relaunched worker"))

    first = jobs_cli.run([suite, "submit", "--spec", str(file), "--confirm"])
    assert first["disposition"] == "submitted"
    assert len(launches) == 1
    path, rest, _, _ = cli._resolve(["eval", "benchmark", suite, "submit", "--spec", str(file)])
    remote = cli._remote_arguments(path[-1], rest, confirmed=True)
    assert set(remote) == {"suite", "spec_json", "confirm"}
    assert json.loads(remote["spec_json"]) == spec
    assert str(file) not in json.dumps(remote)
    inline = cli._remote_arguments(path[-1], ["--spec-json", raw], confirmed=True)
    assert inline == remote

    # Existing records remain readable if the installed profile disappears or drifts.
    monkeypatch.setattr(submission, "load_profile", lambda *_a, **_kw: pytest.fail("retry read current profile"))
    retry = jobs_cli.run([suite, "submit", "--spec-json", raw, "--confirm"])
    remote_retry = mcp.call_tool("benchmark_job_submit", remote)
    assert remote_retry["ok"] is True
    for result in (retry, remote_retry["data"]):
        assert result["disposition"] == "existing"
        assert result["job"]["spec_sha256"] == first["job"]["spec_sha256"]
        assert result["worker"]["launched"] is False
    assert len(launches) == 1


@pytest.mark.parametrize("action", ["submit", "preflight"])
def test_remote_spec_sources_are_exclusive_and_bounded(tmp_path, action):
    from anvil_serving.benchmarking.limits import MAX_BENCHMARK_JOB_SPEC_BYTES

    file = tmp_path / "job.json"
    file.write_bytes(b" " * (MAX_BENCHMARK_JOB_SPEC_BYTES + 1))
    path, _, _, _ = cli._resolve(["eval", "benchmark", "aider", action])
    for options, code in [
        ([], "bad_spec_source"),
        (["--spec", str(file), "--spec-json", "{}"], "bad_spec_source"),
        (["--spec", str(file)], "spec_too_large"),
        (["--spec-json", _spec("context")], "suite_mismatch"),
    ]:
        with pytest.raises(cli.UsageError) as exc:
            cli._remote_arguments(path[-1], options, confirmed=action == "submit")
        assert exc.value.code == code


def test_benchmark_response_limits_are_scoped_and_within_controller_cap():
    from anvil_serving.commands import manifest_data
    from anvil_serving.transports import MAX_CONTROLLER_RESPONSE_BYTES

    for record in manifest_data()["commands"]:
        remote = record["remote_operation"]
        if remote and remote["tool"] in {
            "benchmark_job_submit", "benchmark_job_status", "benchmark_job_logs",
            "benchmark_job_cancel", "benchmark_job_artifact",
        }:
            expected = 8 * 1024 * 1024 if remote["tool"] == "benchmark_job_artifact" else 4 * 1024 * 1024
            assert remote["max_response_bytes"] == expected <= MAX_CONTROLLER_RESPONSE_BYTES
    path, _, _, _ = cli._resolve(["router", "status"])
    assert path[-1].remote_operation.max_response_bytes is None
