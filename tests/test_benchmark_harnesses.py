from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking import harnesses
from anvil_serving.benchmarking.harnesses import (
    harness_asset_status,
    prepare_harness_assets,
)
from anvil_serving.benchmarking.profiles import load_profile


@pytest.fixture(autouse=True)
def supported_worker(monkeypatch):
    monkeypatch.setattr(harnesses.platform, "system", lambda: "Linux")


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
                    b"httpx==0.28.1\nlitellm==1.102.0\nopenai==2.54.0\ntenacity==9.1.4\n"
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
    assert any(call[0] == ("git", "-c", "core.autocrlf=false", "checkout", "--detach", "FETCH_HEAD")
               for call in runner.calls)
    assert first["python_environment"]["schema"] == (
        "anvil-serving.swe-python-environment/v1"
    )
    assert first["python_environment"]["resolved_packages"] == [
        "httpx==0.28.1",
        "litellm==1.102.0",
        "mini-swe-agent==2.4.6",
        "openai==2.54.0",
        "swebench==4.2.0",
        "tenacity==9.1.4",
        "typer==0.21.0",
    ]
    assert first["python_environment"]["dependency_versions"] == harnesses.SUPPORTED_DEPENDENCIES
    install = next(call[0] for call in runner.calls if call[0][1:4] == ("-m", "pip", "install"))
    assert {"litellm==1.102.0", "openai==2.54.0", "httpx==0.28.1", "tenacity==9.1.4"} <= set(install)
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
