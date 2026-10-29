from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking import jobs_cli
from anvil_serving.benchmarking.harnesses import HARNESS_ASSETS_SCHEMA
from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA, job_spec_sha256, resolve_owned_run_path
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.control_plane.mcp.errors import ToolError
from anvil_serving.control_plane.mcp.tools import benchmarks


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_asset_status_enforces_selected_suite(tmp_path, monkeypatch, surface):
    root = str(tmp_path / "runs")
    Path(root).mkdir()
    path = Path(resolve_owned_run_path(root, ownership_id="owner", run_id="run", relative="assets.json"))
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema": HARNESS_ASSETS_SCHEMA, "suite": "swe"}))
    module = jobs_cli if surface == "cli" else benchmarks
    monkeypatch.setattr(module, "_store" if surface == "cli" else "_benchmark_job_store",
                        lambda: SimpleNamespace(run_root=root))

    def status(suite):
        if surface == "cli":
            return jobs_cli.run([suite, "assets", "--run-id", "run", "--ownership-id", "owner"])
        return benchmarks.tool_benchmark_harness_status({"suite": suite, "run_id": "run", "ownership_id": "owner"})

    with pytest.raises((BenchmarkJobError, ToolError)) as exc:
        status("aider")
    assert exc.value.code == "suite_mismatch"
    assert status("swe")


def test_submit_preview_returns_frozen_profile_spec_without_store(monkeypatch):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("preview opened store"))
    spec = {
        "schema": JOB_SPEC_SCHEMA, "run_id": "preview", "ownership_id": "owner",
        "suite": "aider", "profile": "pilot-v2",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        "worker": {"id": "worker"}, "submitted_at": "2026-10-24T12:00:00Z",
        "timeout_s": 600, "parameters": {},
    }
    result = jobs_cli.run(["aider", "submit", "--spec-json", json.dumps(spec), "--dry-run"])
    assert result["spec"]["profile_sha256"] == load_profile("pilot-v2")["content_sha256"]
    assert result["spec_sha256"] == job_spec_sha256(result["spec"])


def test_aider_command_manifest_advertises_plan_and_prepare_options():
    from anvil_serving.commands import render_manifest

    records = {entry["path"]: entry for entry in json.loads(render_manifest())["commands"]}
    def flags(path):
        return {flag for option in records[path]["options"] for flag in option["flags"]}
    assert {"--dry-run", "--profile"} <= flags("eval benchmark aider")
    assert {"--confirm", "--profile", "--spec"} <= flags("eval benchmark aider prepare")


@pytest.mark.parametrize("suite", ["aider", "performance"])
def test_focused_prepare_help_lists_required_profile(capsys, suite):
    from anvil_serving import cli

    assert cli.main(["eval", "benchmark", suite, "prepare", "--help"]) == 0
    help_text = capsys.readouterr().out
    usage = " ".join(help_text.split("options:", 1)[0].split())
    assert "--profile {" in usage
    if suite == "aider":
        assert "[--profile" in usage and "--spec" in usage
    else:
        assert "[--profile" not in usage
    assert "pilot-v2" in help_text


@pytest.mark.parametrize("action", ["submit", "preflight"])
@pytest.mark.parametrize("options", [[], ["--spec", "job.json", "--spec-json", "{}"]])
def test_spec_sources_are_required_and_exclusive_before_store(monkeypatch, action, options):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("invalid sources opened store"))
    with pytest.raises(SystemExit) as exc:
        jobs_cli.run(["aider", action, *options])
    assert exc.value.code == 2


def test_aider_prepare_spec_dispatches_validated_selection(tmp_path, monkeypatch):
    ids = [f"{language}/exercises/practice/example" for language in ("cpp", "python", "rust", "java", "javascript", "go")]
    spec = {"schema": JOB_SPEC_SCHEMA, "run_id": "run", "ownership_id": "owner", "suite": "aider", "profile": "pilot-v2",
            "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "fixture"}, "worker": {"id": "worker"},
            "submitted_at": "2026-10-24T12:00:00Z", "timeout_s": 300, "parameters": {"instance_ids": ids}}
    path = tmp_path / "spec.json"; path.write_text(json.dumps(spec))
    monkeypatch.setattr(jobs_cli, "_store", lambda: SimpleNamespace(run_root=str(tmp_path)))
    calls = []
    monkeypatch.setattr(jobs_cli, "prepare_harness_assets", lambda *args, **kwargs: calls.append(kwargs) or {"ok": True})
    from anvil_serving import cli
    assert cli.main(["eval", "benchmark", "aider", "prepare", "--spec", str(path), "--offline", "--confirm"]) == 0
    assert calls[0]["aider_instance_ids"] == ids and calls[0]["run_id"] == "run" and calls[0]["offline"]
    for bad_ids in ([], ids[:-1] + ids[:1], [ids[0], "cpp/exercises/practice/other"], ids[:-1] + ["go/../escape"], ids[:-1] + ["swift/exercises/practice/example"]):
        spec["parameters"]["instance_ids"] = bad_ids; path.write_text(json.dumps(spec))
        with pytest.raises(BenchmarkJobError): jobs_cli.run(["aider", "prepare", "--spec", str(path), "--confirm"])
    with pytest.raises(BenchmarkJobError): jobs_cli.run(["aider", "prepare", "--spec", str(path), "--profile", "pilot-v2", "--confirm"])
    assert len(calls) == 1


def test_aider_prepare_spec_cli_reaches_environment_selection(tmp_path, monkeypatch):
    from anvil_serving import cli
    from anvil_serving.benchmarking import aider_prepare, harnesses
    ids = [f"{language}/exercises/practice/example" for language in ("cpp", "python", "rust", "java", "javascript", "go")]
    spec = {"schema": JOB_SPEC_SCHEMA, "run_id": "selected", "ownership_id": "owner", "suite": "aider", "profile": "pilot-v2",
            "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "fixture"}, "worker": {"id": "worker"},
            "submitted_at": "2026-10-24T12:00:00Z", "timeout_s": 300, "parameters": {"instance_ids": ids}}
    path = tmp_path / "spec.json"; path.write_text(json.dumps(spec))
    monkeypatch.setattr(jobs_cli, "_store", lambda: SimpleNamespace(run_root=str(tmp_path / "runs")))
    monkeypatch.setenv("ANVIL_BENCHMARK_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setattr(harnesses, "_prepare_repository", lambda **kwargs: ({"kind": "git", "cache_key": kwargs["name"]}, []))
    calls = []
    def prepare_environment(**kwargs):
        calls.append(kwargs)
        return {"fixture": "python"}, {"fixture": "environment", "selection": kwargs.get("instance_ids")}, []
    monkeypatch.setattr(aider_prepare, "prepare_aider_environment", prepare_environment)
    assert cli.main(["eval", "benchmark", "aider", "prepare", "--spec", str(path), "--offline", "--confirm"]) == 0
    assert calls[0]["instance_ids"] == ids and calls[0]["offline"] is True
    manifest = json.loads((tmp_path / "runs/owner/selected/assets.json").read_text())
    assert manifest["aider_environment"]["selection"] == ids
    assert cli.main(["eval", "benchmark", "aider", "prepare", "--profile", "pilot-v2", "--run-id", "legacy", "--ownership-id", "owner", "--offline", "--confirm"]) == 0
    assert "instance_ids" not in calls[1]
