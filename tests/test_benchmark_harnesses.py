from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

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
        if argv[:2] == ("git", "rev-parse"):
            revision = next(
                call[0][-1]
                for call in reversed(self.calls)
                if call[0][:2] == ("git", "fetch")
            )
            return SimpleNamespace(returncode=0, stdout=(revision + "\n").encode(), stderr=b"")
        if argv[:2] == ("git", "status"):
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        if argv[:3] == ("docker", "image", "inspect"):
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
