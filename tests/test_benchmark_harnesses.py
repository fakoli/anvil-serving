from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import threading
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking import harnesses
from anvil_serving.benchmarking.harnesses import (
    cleanup_harness_work,
    harness_asset_status,
    prepare_harness_assets,
)
from anvil_serving.benchmarking.jobs import BenchmarkJobError
from anvil_serving.benchmarking.profiles import load_profile


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, cwd, timeout):
        self.calls.append((argv, cwd, timeout))
        if argv[1:3] == ("-m", "venv"):
            executable = Path(argv[-1]) / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.touch()
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if argv[1:4] == ("-m", "pip", "freeze"):
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    b"mini-swe-agent==2.4.6\n"
                    b"swebench==4.2.0\n"
                    b"typer==0.21.0\n"
                ),
                stderr=b"",
            )
        if argv[:2] == ("git", "rev-parse"):
            revision = next(
                call[0][-1]
                for call in reversed(self.calls)
                if call[0][:2] == ("git", "fetch")
            )
            return SimpleNamespace(returncode=0, stdout=(revision + "\n").encode(), stderr=b"")
        if argv[:2] == ("git", "status"):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if argv[1:3] == ("image", "inspect"):
            return SimpleNamespace(returncode=0, stdout=b"[]", stderr=b"")
        return SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")


def test_preparation_records_exact_assets_and_reuses_cache(tmp_path):
    runner = Runner()
    profile = load_profile("smoke")
    first = prepare_harness_assets(
        profile,
        suite="swe",
        run_root=str(tmp_path / "runs"),
        ownership_id="campaign",
        run_id="run-one",
        cache_root=str(tmp_path / "cache"),
        runner=runner,
    )
    assert first["assets"]["mini-swe-agent"]["revision"] == profile["adapters"][
        "mini-swe-agent"
    ]["revision"]
    assert first["assets"]["worker-base"]["image"].count("@sha256:") == 1
    assert first["python_environment"]["schema"] == (
        "anvil-serving.swe-python-environment/v1"
    )
    assert first["python_environment"]["resolved_packages"] == [
        "mini-swe-agent==2.4.6",
        "swebench==4.2.0",
        "typer==0.21.0",
    ]
    assert all(len(item["stdout"].encode()) <= 64 * 1024 for item in first["command_logs"])

    second = prepare_harness_assets(
        profile,
        suite="swe",
        run_root=str(tmp_path / "runs"),
        ownership_id="campaign",
        run_id="run-two",
        cache_root=str(tmp_path / "cache"),
        runner=runner,
    )
    assert second["assets"]["mini-swe-agent"]["reused"] is True
    assert second["python_environment"]["reused"] is True
    assert harness_asset_status(
        run_root=str(tmp_path / "runs"), ownership_id="campaign", run_id="run-two"
    )["status"] == "ready"


def test_offline_missing_asset_fails_closed(tmp_path):
    with pytest.raises(BenchmarkJobError) as exc:
        prepare_harness_assets(
            load_profile("smoke"),
            suite="swe",
            run_root=str(tmp_path / "runs"),
            ownership_id="campaign",
            run_id="offline",
            cache_root=str(tmp_path / "cache"),
            offline=True,
            runner=Runner(),
        )
    assert exc.value.code == "harness_assets_offline"


def test_concurrent_swe_environment_publishers_reuse_the_valid_winner(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(harnesses, "resolve_container_binary", lambda: "docker")
    profile = load_profile("smoke")
    seed = prepare_harness_assets(
        profile,
        suite="swe",
        run_root=str(tmp_path / "runs"),
        ownership_id="campaign",
        run_id="seed",
        cache_root=str(tmp_path / "cache"),
        runner=Runner(),
    )
    environment_root = tmp_path / "cache" / seed["python_environment"]["cache_key"]
    shutil.rmtree(environment_root)
    barrier = threading.Barrier(2)

    class ConcurrentRunner(Runner):
        def __call__(self, argv, cwd, timeout):
            result = super().__call__(argv, cwd, timeout)
            if (
                argv[1:4] == ("-m", "pip", "freeze")
                and cwd
                and Path(cwd).name.startswith(".swe-python-")
            ):
                barrier.wait(timeout=5)
            return result

    runner = ConcurrentRunner()

    def prepare(run_id):
        return prepare_harness_assets(
            profile,
            suite="swe",
            run_root=str(tmp_path / "runs"),
            ownership_id="campaign",
            run_id=run_id,
            cache_root=str(tmp_path / "cache"),
            runner=runner,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(prepare, ("concurrent-one", "concurrent-two")))

    assert sorted(result["python_environment"]["reused"] for result in results) == [
        False,
        True,
    ]
    assert all(result["python_environment"]["resolved_packages"] for result in results)


def test_cleanup_removes_only_owned_work_and_preserves_cache_and_evidence(tmp_path):
    run_root = tmp_path / "runs"
    work = run_root / "campaign" / "run" / "work"
    work.mkdir(parents=True)
    (work / "scratch").write_text("x", encoding="utf-8")
    evidence = work.parent / "assets.json"
    evidence.write_text(json.dumps({"evidence": True}), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "shared").write_text("keep", encoding="utf-8")

    result = cleanup_harness_work(
        run_root=str(run_root), ownership_id="campaign", run_id="run"
    )
    assert result == {"cleaned": True, "scope": "owned-work", "shared_cache_cleaned": False}
    assert not work.exists()
    assert evidence.exists()
    assert (cache / "shared").exists()


def test_mutable_profile_is_rejected_before_commands(tmp_path):
    profile = load_profile("smoke")
    profile["adapters"]["worker-base"]["image"] = "python:3.12-slim"
    with pytest.raises(BenchmarkJobError) as exc:
        prepare_harness_assets(
            profile,
            suite="swe",
            run_root=str(tmp_path / "runs"),
            ownership_id="campaign",
            run_id="mutable",
            cache_root=str(tmp_path / "cache"),
            runner=lambda *_args: pytest.fail("command ran"),
        )
    assert exc.value.code in {"profile_digest_mismatch", "mutable_image"}
