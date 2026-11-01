from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from anvil_serving.benchmarking import aider_prepare as prep
from anvil_serving.benchmarking import aider_containment
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


def python_environment(cache):
    identity = prep._python_identity()
    key = "aider-python-environments/" + prep._digest(identity)
    root = cache / key
    (root / "bin").mkdir(parents=True)
    (root / "bin/python").write_text("isolated interpreter fixture")
    (root / "bin/python").chmod(0o755)
    (root / "installed.py").write_text("original")
    packages = ["aider-chat==" + prep.AIDER_SOURCE_VERSION]
    lock = {**identity, "identity": identity, "python": {"version": "3.12.1"},
            "resolved_packages": packages, "resolved_packages_sha256": prep._digest(packages),
            "files_sha256": prep._environment_digest(root)}
    (root / "environment.json").write_text(json.dumps(lock))
    return {**lock, "cache_key": key, "executable": "bin/python", "reused": False}


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
    monkeypatch.setattr(aider_containment, "load_test_callback", lambda _: callback)
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
    retained = json.loads((failed / "aider/preparation/oracle.json").read_text())
    assert retained["status"] == "incomplete"
    assert len(retained["results"]) == 6


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


def test_build_resolves_base_digest_and_freezes_exact_inputs(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "benchmark").mkdir(parents=True)
    (source / "benchmark/Dockerfile").write_text("FROM buildpack-deps:jammy\nRUN npm install \\\n    jest \\\n    babel-jest@29.6.4\nCOPY . /aider\nRUN uv pip install --system --no-cache-dir -e /aider[dev]\n")
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
        if command[1:3] == ("buildx", "build") and "--help" in command:
            output = "--resource"
        elif command[1:3] == ("buildx", "build"):
            Path(command[command.index("--iidfile") + 1]).write_text(image)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
    previous_umask = os.umask(0o077)
    try:
        observed, inputs = prep._build_image({"aider": source, "polyglot-benchmark": polyglot},
                                            rows, target, "docker", 1024**2, runner, [])
    finally:
        os.umask(previous_umask)
    assert observed == image
    assert (target / "build/Dockerfile").read_text().startswith("FROM " + digest + "\n")
    assert inputs["base_digest"] == digest and inputs["rebuild_bit_reproducible"] is False
    assert "anvil-entrypoint.py" in inputs["files"]
    assert json.loads((target / "build/anvil-source-version.json").read_text()) == prep.SOURCE_VERSION_METADATA
    recipe = (target / "build/Dockerfile").read_text()
    if os.name == "posix":
        assert (target / "build/anvil-entrypoint.py").stat().st_mode & 0o777 == 0o600
    permission_step = "RUN chmod -R a+rX /aider && mv /root/.cargo"
    assert recipe.count(permission_step) == 1
    assert recipe.index("COPY . /aider") < recipe.index(permission_step) < recipe.index("ENTRYPOINT")
    assert "RUN " + prep.SCM_VERSION_ASSIGNMENT + " uv pip install" in recipe
    assert "ENV " + prep.SCM_VERSION_VARIABLE not in recipe
    assert "    jest@29.7.0 " in recipe and "    jest " not in recipe
    assert "babel-jest@29.6.4" in recipe
    assert inputs["dependency_pins"] == [prep.JEST_DEPENDENCY_PIN]
    assert json.loads((target / "build/anvil-dependency-pins.json").read_text()) == [prep.JEST_DEPENDENCY_PIN]
    assert json.loads((target / "build/anvil-adapter-identity.json").read_text()) == prep._adapter_identity()
    assert "anvil-adapter-identity.json" in inputs["files"]
    build = next(command for command in calls if "--iidfile" in command)
    assert "--pull=false" in build and "cpu-quota=400000" in build
    assert "memory=8192m" in build and "memory-swap=8192m" in build
    assert inputs["build_limits"]["cpus"] == 4


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


@pytest.mark.parametrize("link_kind", ["tracked", "outside", "untracked", "directory", "loop"])
def test_source_copy_materializes_only_links_to_tracked_regular_files(tmp_path, link_kind):
    source = tmp_path / "source"
    source.mkdir()
    (source / "tracked.md").write_text("pinned documentation")
    (source / "untracked.md").write_text("untracked")
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    targets = {"tracked": "tracked.md", "outside": str(outside),
               "untracked": "untracked.md", "directory": ".", "loop": "link.md"}
    (source / "link.md").symlink_to(targets[link_kind])

    def runner(command, cwd, timeout):
        return subprocess.CompletedProcess(command, 0, stdout="link.md\0tracked.md\0", stderr="")

    target = tmp_path / "copy"
    if link_kind == "tracked":
        prep._copy_source(source, target, runner)
        assert (target / "link.md").read_text() == "pinned documentation"
        assert not (target / "link.md").is_symlink()
    else:
        with pytest.raises(BenchmarkJobError, match="unsupported Aider source"):
            prep._copy_source(source, target, runner)


@pytest.mark.parametrize("failed_probe", ["lint", "packages"])
def test_failed_image_inventory_retains_bounded_combined_output_in_receipt(tmp_path, monkeypatch, failed_probe):
    cache, run = tmp_path / "cache", tmp_path / "run"
    image = "sha256:" + "1" * 64
    monkeypatch.setattr(prep, "verify_aider_sources", lambda *args, **kwargs:
                        {"aider": tmp_path, "polyglot-benchmark": tmp_path})
    monkeypatch.setattr(prep, "_selection", lambda _: [])
    monkeypatch.setattr(prep, "_python_environment", lambda *args: {})
    monkeypatch.setattr(prep.runtime, "resolve_container_binary", lambda: "docker")
    monkeypatch.setattr(prep, "_build_image", lambda *args: (image, {}))
    failure = b"probe start\n" + b"progress\n" * 20000 + b"Permission denied\n"
    def contained(command, **kwargs):
        lint = "--version" in command
        if lint and failed_probe == "packages":
            return subprocess.CompletedProcess(command, 0, stdout="7.3.0 (mccabe: 0.7.0) CPython")
        return subprocess.CompletedProcess(command, 2, stdout=failure)
    monkeypatch.setattr(aider_containment, "run_contained_test", contained)
    with pytest.raises(BenchmarkJobError, match="flake8 dependency|package inventory"):
        prep.prepare_aider_environment(assets=assets(), cache_root=str(cache), offline=False,
            max_download_bytes=1024**2, run_path=str(run), ownership_id="owner", run_id="run")
    receipt = json.loads((run / "aider/preparation/receipt.json").read_text())
    assert receipt["status"] == "failed"
    logs = receipt["command_logs"]
    assert len(logs) == (1 if failed_probe == "lint" else 2)
    result = logs[-1]
    assert result["returncode"] == 2 and result["output_streams"] == "combined"
    assert result["stderr"] == "" and result["output_truncated"] is True
    assert result["stdout"].startswith("probe start\n")
    assert result["stdout"].endswith("Permission denied\n")
    assert len(result["stdout"].encode()) == prep.runtime.MAX_HARNESS_OUTPUT_BYTES
    assert not list(cache.glob("aider-environments/*/environment.json"))
