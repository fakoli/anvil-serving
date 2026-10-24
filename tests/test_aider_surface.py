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
    assert {"--confirm", "--profile"} <= flags("eval benchmark aider prepare")
