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


@pytest.mark.parametrize("failure", ["timeout", "swe_process_output_overflow", "swe_process_boundary_failed", "generic"])
def test_process_failure_retains_safe_evidence_and_sanitizes_client_environment(tmp_path, monkeypatch, failure):
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
        assert "UNRELATED_CLOUD_KEY" not in env and "PYTHONPATH" not in env and "PYTHONHOME" not in env
        assert "TEST_KEY" not in env
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, timeout, output="partial upstream output")
        if failure == "generic":
            raise RuntimeError("private diagnostic value")
        raise BenchmarkJobError(failure, "private diagnostic value", {"stderr": "private diagnostic value"})
    result = aider.run_aider_benchmark(plan, command_runner=runner,
        environ={"TEST_KEY": "test-reference-value", "UNRELATED_CLOUD_KEY": "must not inherit",
                 "PYTHONPATH": "/untrusted/product", "PYTHONHOME": "/untrusted/python"})
    assert result["state"] == "incomplete"
    assert result["quality"]["status"] == "unknown"
    if failure == "timeout":
        assert result["failure"] == {"class": "budget_exhausted", "code": "deadline_s"}
        assert result["stage"]["stdout"] == "partial upstream output"
    else:
        expected_code = "aider_process_failed" if failure == "generic" else failure
        assert result["failure"] == {"class": "infrastructure_failure", "code": expected_code}
        expected_stage = {"status": "failed", "error_type": "RuntimeError" if failure == "generic" else "BenchmarkJobError"}
        if failure != "generic":
            expected_stage["code"] = failure
        assert result["stage"] == expected_stage
    assert json.loads((base / "process.json").read_text()) == result["stage"]
    assert json.loads((base / "result.json").read_text())["failure"] == result["failure"]
    assert "private diagnostic value" not in json.dumps(result)
    assert all("private diagnostic value" not in path.read_text() for path in base.glob("*.json"))
    assert result["retention"]["state"] == "incomplete"
    assert (base / "original" / task / "solution.py").exists()


def prepared_plan_inputs(tmp_path, monkeypatch):
    import hashlib
    from anvil_serving.benchmarking import aider_prepare as prep
    # Reuse preparation's fixture shape: the same public validator gates this plan.
    from tests.test_aider_prepare import assets, environment, selection, python_environment
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
    python_receipt = python_environment(cache)
    python = cache / python_receipt["cache_key"] / "bin/python"
    profile = load_profile("pilot-v2")
    assets_manifest = {"schema": aider.HARNESS_ASSETS_SCHEMA, "suite": "aider",
        "profile_sha256": profile["content_sha256"], "assets": manifest_assets,
        "aider_environment": receipt, "python_environment": python_receipt}
    run_root = tmp_path / "runs"
    run_root.mkdir()
    monkeypatch.setattr(aider, "resolve_container_binary", lambda: "/usr/bin/docker")
    return profile, assets_manifest, dict(
        endpoint={"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        run_root=str(run_root), cache_root=str(cache), ownership_id="owner", run_id="run",
        request_controls={"thinking_mode": "disabled"}), python, rows, receipt


def test_prepared_six_language_manifest_builds_bound_plan(tmp_path, monkeypatch):
    profile, assets_manifest, kwargs, python, rows, receipt = prepared_plan_inputs(tmp_path, monkeypatch)
    run_root = tmp_path / "runs"
    cache = tmp_path / "cache"
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


@pytest.mark.parametrize("when", ["after-assets", "after-plan"])
def test_prepared_python_drift_rejected_before_command_runner(tmp_path, monkeypatch, when):
    profile, manifest, kwargs, python, *_ = prepared_plan_inputs(tmp_path, monkeypatch)
    plan = aider.build_aider_run_plan(profile, manifest, **kwargs) if when == "after-plan" else None
    (python.parent.parent / "installed.py").write_text("altered imported dependency")
    with pytest.raises(BenchmarkJobError, match="bytes or receipt"):
        if plan is None:
            aider.build_aider_run_plan(profile, manifest, **kwargs)
        else:
            aider.run_aider_benchmark(plan, command_runner=lambda *_: pytest.fail("model command executed"))


def test_python_drift_during_run_setup_rejected_before_command_runner(tmp_path, monkeypatch):
    profile, manifest, kwargs, python, *_ = prepared_plan_inputs(tmp_path, monkeypatch)
    plan = aider.build_aider_run_plan(profile, manifest, **kwargs)
    original_copy = aider.shutil.copytree
    def copy_and_drift(*args, **kwargs):
        result = original_copy(*args, **kwargs)
        (python.parent.parent / "installed.py").write_text("changed after initial run gate")
        return result
    monkeypatch.setattr(aider.shutil, "copytree", copy_and_drift)
    with pytest.raises(BenchmarkJobError, match="bytes or receipt"):
        aider.run_aider_benchmark(plan, command_runner=lambda *_: pytest.fail("model command executed"))


@pytest.mark.parametrize("changed", ["interpreter", "product_root", "bootstrap"])
def test_prepared_python_command_cannot_substitute_interpreter(tmp_path, monkeypatch, changed):
    profile, manifest, kwargs, *_ = prepared_plan_inputs(tmp_path, monkeypatch)
    plan = aider.build_aider_run_plan(profile, manifest, **kwargs)
    position = {"interpreter": 0, "bootstrap": plan["command"].index("-c") + 1,
                "product_root": plan["command"].index("-c") + 2}[changed]
    plan["command"][position] = "/untrusted/substitute"
    plan["plan_sha256"] = aider._digest({k:v for k,v in plan.items() if k != "plan_sha256"})
    with pytest.raises(BenchmarkJobError, match="prepared interpreter"):
        aider.run_aider_benchmark(plan, command_runner=lambda *_: pytest.fail("substitute Python executed"))


@pytest.mark.parametrize("adapter", ["aider", "performance"])
def test_real_isolated_adapter_startup_from_path_separator_checkout(tmp_path, monkeypatch, adapter):
    """No installed product or upstream packages: reach the real plan gate offline."""
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import venv
    from anvil_serving.benchmarking import aider_prepare, performance_prepare

    source = Path(aider.__file__).resolve().parents[2]
    checkout = tmp_path / ("product" + os.pathsep + "checkout")
    shutil.copytree(source / "anvil_serving", checkout / "anvil_serving",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    environment = tmp_path / "isolated"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    cwd = tmp_path / "upstream"
    cwd.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.defpath, "HOME": str(home), "PYTHONNOUSERSITE": "1"}
    absent = subprocess.run([python, "-I", "-c", "import importlib.util; assert importlib.util.find_spec('anvil_serving') is None"],
                            cwd=cwd, env=env, capture_output=True, timeout=10)
    assert absent.returncode == 0, absent.stderr
    if adapter == "aider":
        # Neither cwd nor ambient Python settings may supply trusted code.
        hostile = cwd / "anvil_serving"
        hostile.mkdir()
        (hostile / "__init__.py").write_text("raise RuntimeError('untrusted product imported')\n")
        (cwd / "sitecustomize.py").write_text("raise RuntimeError('untrusted startup imported')\n")
        env.update(PYTHONPATH=str(cwd), PYTHONHOME=str(tmp_path / "absent-python-home"))
    preparer = aider_prepare if adapter == "aider" else performance_prepare
    monkeypatch.setattr(preparer, "__file__", str(checkout / "anvil_serving/benchmarking" / (adapter + "_prepare.py")))
    plan_path = tmp_path / "invalid-plan.json"
    plan_path.write_text("{}")
    command = preparer._adapter_command(python, str(plan_path))
    help_result = subprocess.run([*command[:-2], "--help"], cwd=cwd, env=env,
                                 capture_output=True, text=True, timeout=10)
    assert help_result.returncode == 0, help_result.stderr
    assert "--plan" in help_result.stdout
    rejected = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=10)
    assert rejected.returncode != 0
    assert ("Aider" if adapter == "aider" else "performance") + " plan identity changed" in rejected.stderr
    assert str(checkout / "anvil_serving/benchmarking" / (adapter + "_prepare.py")) in rejected.stderr
    assert "ModuleNotFoundError" not in rejected.stderr
    assert "untrusted" not in help_result.stderr + rejected.stderr


def test_real_aider_default_capture_limit_preserves_small_retention(tmp_path, monkeypatch):
    from pathlib import Path
    import subprocess
    from anvil_serving.benchmarking import swe
    profile, manifest, kwargs, *_ = prepared_plan_inputs(tmp_path, monkeypatch)
    plan = aider.build_aider_run_plan(profile, manifest, **kwargs)
    calls = []
    def bounded(argv, **options):
        calls.append(options)
        assert argv == plan["command"]
        assert options["max_output_bytes"] == aider.MAX_AIDER_PROCESS_OUTPUT_BYTES == 8 * 1024**2
        assert options["owned_process_group"] is True
        assert options["timeout"] == plan["timeout_s"]
        assert options["cwd"] == plan["paths"]["aider_root"]
        assert "UNRELATED_KEY" not in options["env"]
        return subprocess.CompletedProcess(argv, 0, stdout=b"a" * 65537, stderr=b"b" * 65537)
    monkeypatch.setattr(swe, "_run_bounded_process", bounded)
    # Exercise the definition-time default, not an injected command_runner.
    result = aider.run_aider_benchmark(plan, environ={"UNRELATED_KEY": "private"})
    assert len(calls) == 1
    assert result["stage"]["returncode"] == 0
    assert result["stage"]["stdout"] == "a" * 65536
    assert result["stage"]["stderr"] == "b" * 65536
    assert result["stage"]["output_truncated"] is True
    assert json.loads(Path(plan["paths"]["process"]).read_text()) == result["stage"]


@pytest.mark.parametrize("owner, count, expected", [
    ("aider", 8 * 1024**2, "completed"),
    ("aider", 8 * 1024**2 + 1, "swe_process_output_overflow"),
    ("swe", 65537, "swe_process_output_overflow"),
    ("performance", 65537, "swe_process_output_overflow"),
    ("aider", 0, "timeout"),
])
def test_actual_process_capture_caps_deadline_and_owned_group_cleanup(tmp_path, owner, count, expected):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import textwrap
    if os.name != "posix":
        pytest.skip("owned process groups require POSIX")
    script = textwrap.dedent("""\
        import json, os, subprocess, sys
        sys.path.insert(0, sys.argv[1])
        from anvil_serving.benchmarking import aider, swe, performance
        from anvil_serving.benchmarking.jobs import BenchmarkJobError
        from anvil_serving.transports import _owned_group_members
        owner, count = sys.argv[2], int(sys.argv[3])
        runner = (swe._budget_runner if owner == 'swe' else
                  (aider.run_aider_benchmark if owner == 'aider' else
                   performance.run_performance_benchmark).__kwdefaults__['command_runner'])
        child = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']); "
        child += ("time.sleep(5)" if not count else
                  f"sys.stdout.buffer.write(b'a'*{count // 2}); sys.stdout.flush(); sys.stderr.buffer.write(b'b'*{count - count // 2}); sys.stderr.flush()")
        try:
            result = runner([sys.executable, '-I', '-B', '-c', child], os.getcwd(),
                            0.2 if not count else 5, {'PATH': os.defpath})
            observed = {'outcome': 'completed', 'bytes': len(result.stdout) + len(result.stderr),
                        'returncode': result.returncode}
        except BenchmarkJobError as exc:
            observed = {'outcome': exc.code}
        except subprocess.TimeoutExpired:
            observed = {'outcome': 'timeout'}
        observed['remaining_peers'] = _owned_group_members(os.getpid(), timeout=1)
        print(json.dumps(observed))
    """)
    process = subprocess.run([sys.executable, "-I", "-B", "-c", script,
                              str(Path(aider.__file__).resolve().parents[2]), owner, str(count)],
                             cwd=tmp_path, env={"PATH": os.defpath}, start_new_session=True,
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stderr
    observed = json.loads(process.stdout)
    assert observed["outcome"] == expected
    assert observed["remaining_peers"] == []
    if expected == "completed":
        assert observed["returncode"] == 0 and observed["bytes"] == count
