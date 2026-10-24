from __future__ import annotations

import json

import pytest

from anvil_serving.benchmarking import aider
from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.profiles import load_profile


def test_admission_preserves_relative_task_ids_and_request_controls():
    value = aider.validate_aider_parameters(load_profile("pilot-v2"), {
        "instance_ids": ["python/exercises/practice/example"], "thinking_mode": "disabled"})
    assert value["selection"]["instance_ids"] == ["python/exercises/practice/example"]
    assert value["request_controls"]["chat_template_kwargs"] == {"enable_thinking": False}
    with pytest.raises(BenchmarkJobError):
        aider.validate_aider_parameters(load_profile("pilot-v2"), {"command": "arbitrary"})
    with pytest.raises(BenchmarkJobError):
        aider.validate_aider_parameters(load_profile("pilot-v2"), {"instance_ids": ["../escape"]})


def test_retention_is_bounded_immutable_and_partial(tmp_path):
    base = tmp_path / "aider"
    base.mkdir()
    (base / "selection.json").write_text(json.dumps({"instance_ids": ["python/exercises/practice/example"]}))
    first = aider.retain_aider_artifacts(run_path=str(tmp_path))
    assert first["state"] == "incomplete"
    assert first["manifest_reference"]["sha256"]
    assert aider.retain_aider_artifacts(run_path=str(tmp_path)) == first
    (base / "selection.json").write_text(json.dumps({"instance_ids": ["python/exercises/practice/changed"]}))
    assert aider.retain_aider_artifacts(run_path=str(tmp_path))["state"] == "failed"


def test_retention_rejects_symlink_and_large_selection(tmp_path):
    base = tmp_path / "aider"
    base.mkdir()
    (tmp_path / "outside").write_text("{}")
    (base / "selection.json").symlink_to(tmp_path / "outside")
    assert aider.retain_aider_artifacts(run_path=str(tmp_path))["state"] == "failed"
    (base / "selection.json").unlink()
    (base / "selection.json").write_text(" " * (1024 * 1024 + 1))
    assert aider.retain_aider_artifacts(run_path=str(tmp_path))["state"] == "failed"


def test_native_result_only_measured_with_exact_model_and_whole_edit(tmp_path):
    task = "python/exercises/practice/example"
    result_dir = tmp_path / task
    result_dir.mkdir(parents=True)
    file = result_dir / ".aider.results.json"
    file.write_text(json.dumps({"model": "openai/llm.primary", "edit_format": "whole", "tests_outcomes": [False, True]}))
    plan = {"selection": {"instance_ids": [task]}, "paths": {"results": str(tmp_path)}, "endpoint": {"model": "llm.primary"}}
    result = {"instances": [], "failure": None}
    aider._normalize(plan, result)
    assert result["instances"][0]["first_attempt_passed"] is False
    assert result["instances"][0]["second_attempt_passed"] is True
    assert result["summary"]["resolve_rate"] == 1
    file.write_text(json.dumps({"model": "cloud", "edit_format": "whole", "tests_outcomes": [True]}))
    result = {"instances": [], "failure": None}
    aider._normalize(plan, result)
    assert result["summary"]["resolve_rate"] is None
    assert result["failure"]["class"] == "broken_harness"


def test_tampered_plan_rejected_before_any_run():
    with pytest.raises(BenchmarkJobError, match="identity"):
        aider.verify_plan({"schema": aider.AIDER_PLAN_SCHEMA, "plan_sha256": "0" * 64})


def test_process_timeout_retains_partial_output_and_sanitizes_client_environment(tmp_path, monkeypatch):
    import subprocess
    task = "python/exercises/practice/example"
    source = tmp_path / "source"
    (source / task).mkdir(parents=True)
    (source / task / "solution.py").write_text("pass")
    base = tmp_path / "run" / "aider"
    paths = {name: str(base / filename) for name, filename in {
        "aider": ".", "original": "original", "home": "home", "results": "results",
        "plan": "plan.json", "selection": "selection.json", "result": "result.json",
        "process": "process.json", "adapter_result": "adapter-result.json", "request_events": "requests.jsonl"}.items()}
    paths.update(polyglot_root=str(source), aider_root=str(source))
    plan = {"paths": paths, "run_path": str(base.parent), "run_id": "run", "profile": "pilot-v2",
            "plan_sha256": "a" * 64, "adapter_sha256": "b" * 64, "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary", "auth_env": "TEST_KEY"},
            "request_controls": {}, "selection": {"instance_ids": [task]}, "command": ["python", "adapter"], "timeout_s": 10}
    monkeypatch.setattr(aider, "verify_plan", lambda value: None)
    def runner(command, cwd, timeout, env):
        assert env["OPENAI_API_KEY"] == "test-reference-value"
        assert "UNRELATED_CLOUD_KEY" not in env and "PYTHONPATH" in env
        assert "TEST_KEY" not in env
        raise subprocess.TimeoutExpired(command, timeout, output="partial upstream output")
    result = aider.run_aider_benchmark(plan, command_runner=runner,
        environ={"TEST_KEY": "test-reference-value", "UNRELATED_CLOUD_KEY": "must not inherit"})
    assert result["state"] == "incomplete"
    assert result["quality"]["status"] == "unknown"
    assert result["failure"]["class"] == "budget_exhausted"
    assert result["stage"]["stdout"] == "partial upstream output"
    assert result["retention"]["state"] == "incomplete"
    assert (base / "original" / task / "solution.py").exists()


def test_prepared_six_language_manifest_builds_bound_plan(tmp_path, monkeypatch):
    import hashlib
    from anvil_serving.benchmarking import aider_prepare as prep
    # Reuse preparation's fixture shape: the same public validator gates this plan.
    from tests.test_aider_prepare import assets, environment, selection
    cache = tmp_path / "cache"
    manifest_assets = assets()
    for asset in manifest_assets.values():
        asset["dirty"] = False
    aider_root = cache / manifest_assets["aider"]["cache_key"]
    polyglot = cache / manifest_assets["polyglot-benchmark"]["cache_key"]
    rows = selection(polyglot)
    for relative in aider.SOURCE_HASHES:
        path = aider_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("pinned-source-fixture")
        monkeypatch.setitem(aider.SOURCE_HASHES, relative, hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(prep, "SOURCE_HASHES", {**aider.SOURCE_HASHES, "benchmark/Dockerfile": "d" * 64})
    receipt = environment(rows)
    packages = ["aider-chat==0.86.1"]
    python = cache / "aider-python-environments/test/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("isolated interpreter fixture")
    profile = load_profile("pilot-v2")
    assets_manifest = {"schema": aider.HARNESS_ASSETS_SCHEMA, "suite": "aider",
        "profile_sha256": profile["content_sha256"], "assets": manifest_assets,
        "aider_environment": receipt, "python_environment": {
            "schema": prep.AIDER_PYTHON_ENVIRONMENT_SCHEMA, "cache_key": "aider-python-environments/test",
            "executable": "bin/python", "resolved_packages": packages,
            "resolved_packages_sha256": aider._digest(packages)}}
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(aider, "resolve_container_binary", lambda: "/usr/bin/docker")
    plan = aider.build_aider_run_plan(profile, assets_manifest,
        endpoint={"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        run_root=str(run_root), cache_root=str(cache), ownership_id="owner", run_id="run",
        request_controls={"thinking_mode": "disabled"})
    assert plan["command"][0] == str(python)
    assert plan["selection"]["instance_ids"] == sorted(row["task_id"] for row in rows)
    assert plan["aider_environment"]["image_id"] == receipt["oracle"]["image_id"]
    assert plan["request_controls"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert plan["selection"]["sha256"] == aider._digest(plan["selection"]["instance_ids"])
    assert plan["paths"]["results"].endswith("/owner/run/aider/results")
    assert not (run_root / "owner/run").exists()
    with pytest.raises(BenchmarkJobError, match="prepared"):
        aider.build_aider_run_plan(profile, assets_manifest,
            endpoint={"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
            run_root=str(run_root), cache_root=str(cache), ownership_id="owner", run_id="run",
            instance_ids=["python/exercises/practice/unprepared"])
