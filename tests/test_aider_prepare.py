from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess

import pytest

from anvil_serving.benchmarking import aider_prepare as prep
from anvil_serving.benchmarking import aider_adapter, aider_containment
from anvil_serving.benchmarking.jobs import BenchmarkJobError


def assets():
    return {
        "aider": {"source": "https://github.com/Aider-AI/aider", "revision": prep.AIDER_REVISION,
                  "cache_key": "aider/" + prep.AIDER_REVISION},
        "polyglot-benchmark": {"source": "https://github.com/paraemsi/polyglot-benchmark",
                              "revision": prep.POLYGLOT_REVISION,
                              "cache_key": "polyglot-benchmark/" + prep.POLYGLOT_REVISION},
    }


def selection(tmp_path):
    for language in prep.LANGUAGES:
        task = tmp_path / language / "exercises/practice/example"
        (task / ".meta").mkdir(parents=True)
        (task / ".docs").mkdir()
        (task / ".docs/instructions.md").write_text("instructions")
        (task / "solution.txt").write_text("starter")
        (task / "test.txt").write_text("tests")
        (task / ".meta/example.txt").write_text("reference")
        solutions = ["solution.txt"]
        if language == "rust":
            solutions.append("Cargo.toml")
            (task / "Cargo.toml").write_text("dependencies")
        (task / ".meta/config.json").write_text(json.dumps({"files": {
            "solution": solutions, "example": [".meta/example.txt"], "test": ["test.txt"],
        }}))
    return prep._selection(tmp_path)


def environment(rows):
    image = "sha256:" + "1" * 64
    return {
        "schema": prep.AIDER_ENVIRONMENT_SCHEMA, "image_id": image,
        "source_hashes": prep.SOURCE_HASHES, "selection": rows,
        "selection_sha256": prep._digest(rows),
        "oracle": {"status": "passed", "image_id": image, "results": [
            {"task_id": row["task_id"], "language": row["language"],
             "starter_returncode": 1, "reference_returncode": 0} for row in rows]},
        "package_inventory": "packages", "package_inventory_sha256": prep._digest("packages"),
    }


def test_six_language_receipt_rejects_missing_failed_or_different_image(tmp_path):
    rows = selection(tmp_path / "source")
    assert rows[-1]["solutions"] == ["solution.txt"]
    good = environment(rows)
    assert prep.validate_aider_environment(good, cache_root=str(tmp_path), assets=assets())["image_id"] == good["image_id"]
    for mutate in (
        lambda value: value["oracle"]["results"].pop(),
        lambda value: value["oracle"].update(status="incomplete"),
        lambda value: value["oracle"].update(image_id="sha256:" + "2" * 64),
        lambda value: value["oracle"]["results"][0].update(starter_returncode=0),
        lambda value: value["oracle"]["results"][0].update(starter_returncode=125),
        lambda value: value["oracle"]["results"][0].update(reference_returncode=1),
        lambda value: value["oracle"]["results"][0].update(reference_timeout=True),
        lambda value: value.update(image_id="mutable:latest"),
        lambda value: value.update(package_inventory="changed"),
        lambda value: value["selection"][0].update(task_id="changed"),
    ):
        bad = copy.deepcopy(good)
        mutate(bad)
        with pytest.raises(BenchmarkJobError):
            prep.validate_aider_environment(bad, cache_root=str(tmp_path), assets=assets())


def test_selection_rejects_symlink_and_missing_reference(tmp_path):
    selection(tmp_path)
    example = tmp_path / "python/exercises/practice/example/.meta/example.txt"
    example.unlink()
    with pytest.raises(ValueError, match="missing"):
        prep._selection(tmp_path)
    example.symlink_to(tmp_path / "go/exercises/practice/example/.meta/example.txt")
    with pytest.raises(ValueError, match="symlink"):
        prep._selection(tmp_path)


def test_oracle_uses_callback_and_exact_container_image_and_retains_failure(tmp_path, monkeypatch):
    source = tmp_path / "source"
    rows = selection(source)
    calls = []
    # This is only the proxy wiring test; actual pinned AST behavior is tested by the adapter.
    def callback(original, task, history, tests):
        return subprocess.run(["test-command"], cwd=task, timeout=180)
    monkeypatch.setattr(aider_adapter, "load_test_callback", lambda _: callback)
    image = "sha256:" + "1" * 64
    def contained(command, **kwargs):
        calls.append((command, kwargs))
        reference = "reference" in Path(kwargs["task_dir"]).parts
        return subprocess.CompletedProcess(command, 0 if reference else 1, stdout="output")
    monkeypatch.setattr(aider_containment, "run_contained_test", contained)
    run = tmp_path / "runs/owner/run"
    result = prep._oracle({"aider": tmp_path, "polyglot-benchmark": source}, rows,
                         str(run), "owner", "run", image, "docker", None, [])
    assert result["status"] == "passed" and len(calls) == 12
    assert all(kwargs["image_id"] == image for _, kwargs in calls)
    assert all(command == ["python3", "/aider/anvil-entrypoint.py", "test-command"] for command, _ in calls)
    assert all(kwargs["timeout"] == 180 for _, kwargs in calls)
    assert (run / "aider/preparation/reference" / rows[0]["task_id"] / "solution.txt").read_text() == "reference"

    monkeypatch.setattr(aider_containment, "run_contained_test", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, stdout="unexpected starter success"))
    failed = tmp_path / "runs/owner/failed"
    with pytest.raises(BenchmarkJobError, match="starter oracle"):
        prep._oracle({"aider": tmp_path, "polyglot-benchmark": source}, rows,
                     str(failed), "owner", "failed", image, "docker", None, [])
    assert json.loads((failed / "aider/preparation/oracle.json").read_text())["status"] == "incomplete"


def test_source_hash_mismatch_and_no_fallback(tmp_path, monkeypatch):
    manifest = assets()
    for name, value in manifest.items():
        (tmp_path / value["cache_key"]).mkdir(parents=True)
    def runner(command, cwd, timeout):
        name = "aider" if Path(cwd).parent.name == "aider" else "polyglot-benchmark"
        output = manifest[name]["revision"] if command[1] == "rev-parse" else "?? .anvil-harness-lock.json\n"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    with pytest.raises(BenchmarkJobError, match="hash mismatch"):
        prep.verify_aider_sources(str(tmp_path), manifest, runner=runner)
    manifest["aider"]["revision"] = "0" * 40
    with pytest.raises(BenchmarkJobError, match="immutable source"):
        prep.verify_aider_sources(str(tmp_path), manifest, runner=lambda *_: pytest.fail("fallback command"))


def test_python_offline_reuse_checks_bytes_and_inventory(tmp_path):
    identity = {"schema": prep.AIDER_PYTHON_ENVIRONMENT_SCHEMA, "source_version": prep.SOURCE_VERSION_METADATA, "python_minor": "3.12",
                "platform": prep.platform.system().lower(), "architecture": prep.platform.machine().lower(),
                "sources": {"aider": {"source": assets()["aider"]["source"], "revision": prep.AIDER_REVISION}}}
    target = tmp_path / "aider-python-environments" / prep._digest(identity)
    target.mkdir(parents=True)
    (target / "installed.py").write_text("original")
    lock = {"identity": identity, "python": {"version": "3.12.1"},
            "resolved_packages": ["aider-chat==1"], "resolved_packages_sha256": prep._digest(["aider-chat==1"]),
            "files_sha256": prep._environment_digest(target)}
    (target / "environment.json").write_text(json.dumps(lock))
    def runner(command, cwd, timeout):
        output = "aider-chat==1" if "freeze" in command else "3.12.1"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    result = prep._python_environment(assets(), str(tmp_path), True, 1024, runner, [])
    assert result["reused"] is True
    (target / "installed.py").write_text("changed")
    with pytest.raises(BenchmarkJobError, match="identity or inventory"):
        prep._python_environment(assets(), str(tmp_path), True, 1024, runner, [])


def test_build_resolves_base_digest_and_freezes_exact_inputs(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "benchmark").mkdir(parents=True)
    (source / "benchmark/Dockerfile").write_text("FROM buildpack-deps:jammy\nCOPY . /aider\nRUN uv pip install --system --no-cache-dir -e /aider[dev]\n")
    monkeypatch.setattr(prep, "_copy_source", lambda root, target, runner: prep.shutil.copytree(root, target))
    polyglot = tmp_path / "polyglot"
    rows = selection(polyglot)
    target = tmp_path / "environment"
    target.mkdir()
    calls = []
    digest = "buildpack-deps@sha256:" + "2" * 64
    image = "sha256:" + "1" * 64
    def runner(command, cwd, timeout):
        calls.append(command)
        output = ""
        if command[1:3] == ("image", "inspect"):
            output = json.dumps([{"RepoDigests": [digest]}] if command[-1] == "buildpack-deps:jammy" else [{"Id": image, "Size": 1024}])
        if command[1] == "build":
            Path(command[command.index("--iidfile") + 1]).write_text(image)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    observed, inputs = prep._build_image({"aider": source, "polyglot-benchmark": polyglot},
                                        rows, target, "docker", 1024**2, runner, [])
    assert observed == image
    assert (target / "build/Dockerfile").read_text().startswith("FROM " + digest + "\n")
    assert inputs["base_digest"] == digest and inputs["rebuild_bit_reproducible"] is False
    assert "anvil-entrypoint.py" in inputs["files"]
    assert json.loads((target / "build/anvil-source-version.json").read_text()) == prep.SOURCE_VERSION_METADATA
    recipe = (target / "build/Dockerfile").read_text()
    assert "RUN " + prep.SCM_VERSION_ASSIGNMENT + " uv pip install" in recipe
    assert "ENV " + prep.SCM_VERSION_VARIABLE not in recipe
    assert json.loads((target / "build/anvil-adapter-identity.json").read_text()) == prep._adapter_identity()
    assert "anvil-adapter-identity.json" in inputs["files"]
    assert any(command[1] == "build" and "--pull=false" in command for command in calls)


def test_harness_preparation_carries_aider_receipts(tmp_path, monkeypatch):
    from anvil_serving.benchmarking import harnesses
    from anvil_serving.benchmarking.profiles import load_profile
    calls = []
    def prepare(**kwargs):
        calls.append(kwargs)
        return {"schema": prep.AIDER_PYTHON_ENVIRONMENT_SCHEMA}, {"image_id": "sha256:" + "1" * 64}, []
    monkeypatch.setattr(prep, "prepare_aider_environment", prepare)
    monkeypatch.setattr(harnesses, "_prepare_repository", lambda **kwargs:
                        ({**kwargs["adapter"], "cache_key": kwargs["name"]}, []))
    result = harnesses.prepare_harness_assets(load_profile("pilot-v2"), suite="aider",
        run_root=str(tmp_path / "runs"), ownership_id="owner", run_id="run",
        cache_root=str(tmp_path / "cache"), offline=True)
    assert len(calls) == 1 and calls[0]["offline"] is True
    assert set(calls[0]["assets"]) == {"aider", "polyglot-benchmark"}
    assert result["aider_environment"]["image_id"] == "sha256:" + "1" * 64
    assert result["python_environment"]["schema"] == prep.AIDER_PYTHON_ENVIRONMENT_SCHEMA


def test_image_inventory_verifies_contained_lint_version(tmp_path, monkeypatch):
    calls = []
    def contained(command, **kwargs):
        calls.append((command, kwargs))
        output = "7.3.0 (mccabe: 0.7.0) CPython" if "--version" in command else "flake8==7.3.0\n"
        return subprocess.CompletedProcess(command, 0, stdout=output)
    monkeypatch.setattr(aider_containment, "run_contained_test", contained)
    image = "sha256:" + "1" * 64
    result = prep._image_inventory(image, str(tmp_path), "owner", "run", "docker", None)
    assert result.startswith("Aider contained lint: 7.3.0 ") and "flake8==7.3.0" in result
    assert calls[0][0] == ["python3", "/aider/anvil-entrypoint.py", "python3", "-I", "-m", "flake8", "--version"]
    assert all(kwargs["image_id"] == image for _, kwargs in calls)
    monkeypatch.setattr(aider_containment, "run_contained_test", lambda command, **kwargs:
                        subprocess.CompletedProcess(command, 0, stdout="7.2.0 wrong version"))
    with pytest.raises(BenchmarkJobError, match="flake8 dependency"):
        prep._image_inventory(image, str(tmp_path), "owner", "run", "docker", None)


def test_tracked_source_python_install_binds_scm_version(tmp_path, monkeypatch):
    monkeypatch.setattr(prep.shutil, "which", lambda _: "/usr/bin/python3.12")
    monkeypatch.setattr(prep, "_copy_source", lambda root, target, runner: target.mkdir())
    calls = []
    def runner(command, cwd, timeout):
        calls.append(command)
        if "freeze" in command:
            output = "aider-chat @ file:///source\nflake8==7.3.0"
        elif "-c" in command and "importlib.metadata" in command[-1]:
            output = prep.AIDER_SOURCE_VERSION
        elif "-c" in command:
            output = "3.12.1"
        else:
            output = ""
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    result = prep._python_environment(assets(), str(tmp_path), False, 1024**2, runner, [])
    install = next(command for command in calls if "install" in command)
    assert install[:2] == ("env", prep.SCM_VERSION_ASSIGNMENT)
    assert install[3:6] == ("-I", "-m", "pip")
    assert result["identity"]["source_version"] == prep.SOURCE_VERSION_METADATA
    assert prep.AIDER_SOURCE_VERSION == "0.86.3.dev0+g" + prep.AIDER_REVISION
    assert prep.SOURCE_VERSION_METADATA["upstream_release"] is False
