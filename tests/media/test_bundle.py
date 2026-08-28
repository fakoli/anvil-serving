from __future__ import annotations

import json
import subprocess

import pytest

from anvil_serving.media.bundle import inventory, stage
from anvil_serving.media.errors import MediaError


def _completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _lock(tmp_path):
    value = {
        "schema": "anvil-serving.media-bundle-lock/v1",
        "staging": {"container": "example/stager@sha256:" + "f" * 64},
        "workflows": [
            {
                "id": "image.test",
                "version": "v1",
                "graph_sha256": "a" * 64,
                "models": [
                    {
                        "repository": "org/repo",
                        "revision": "b" * 40,
                        "path": "models/one.safetensors",
                        "target": "diffusion_models/one.safetensors",
                        "size": 123,
                        "sha256": "c" * 64,
                    }
                ],
            }
        ],
    }
    path = tmp_path / "bundle.lock.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_inventory_reports_missing_without_creating_volume(tmp_path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        return _completed(argv, 1, stderr="not found")

    result = inventory(
        "image.test", "v1", lock_path=_lock(tmp_path), models_volume="media-models",
        runner=runner,
    )
    assert result["volumeExists"] is False
    assert result["ready"] is False
    assert result["assets"][0]["state"] == "missing"
    assert calls == [["docker", "volume", "inspect", "media-models"]]


def test_inventory_hashes_existing_assets_and_reports_conflict(tmp_path):
    calls = 0

    def runner(argv, **_kwargs):
        nonlocal calls
        calls += 1
        if argv[:3] == ["docker", "volume", "inspect"]:
            return _completed(argv)
        return _completed(argv, stdout="122\n" + "d" * 64 + "\n")

    result = inventory(
        "image.test", "v1", lock_path=_lock(tmp_path), models_volume="media-models",
        runner=runner,
    )
    assert calls == 2
    assert result["ready"] is False
    assert result["assets"][0]["state"] == "mismatch"
    assert result["assets"][0]["observedSha256"] == "d" * 64


def test_stage_adds_only_missing_verified_assets_and_reinventories(tmp_path):
    probe_count = 0
    downloaded = False
    created_user = False

    def runner(argv, **_kwargs):
        nonlocal probe_count, downloaded, created_user
        if argv[:3] == ["docker", "volume", "inspect"]:
            volume = argv[3]
            return _completed(argv, 0 if volume == "media-models" or created_user else 1)
        if argv[:3] == ["docker", "volume", "create"]:
            created_user = True
            return _completed(argv, stdout=argv[3] + "\n")
        script = argv[-1]
        if "if test ! -f" in script:
            probe_count += 1
            if not downloaded:
                return _completed(argv, 44)
            return _completed(argv, stdout="123\n" + "c" * 64 + "\n")
        if "df -B1" in script:
            return _completed(argv, stdout=str(20 * 1024**3) + "\n")
        if "curl --fail" in script:
            assert ".anvil-part" in script
            assert "resolve/" + "b" * 40 in script
            downloaded = True
            return _completed(argv)
        if "mkdir -p /models" in script:
            assert "chown -R 1000:1000" in script
            return _completed(argv)
        raise AssertionError(argv)

    result = stage(
        "image.test",
        "v1",
        lock_path=_lock(tmp_path),
        models_volume="media-models",
        user_volume="media-user",
        dry_run=False,
        runner=runner,
    )
    assert probe_count == 2
    assert result["applied"] is True
    assert result["ready"] is True
    assert result["assets"][0]["state"] == "exact"


def test_stage_refuses_existing_mismatch_without_mutation(tmp_path):
    calls = []

    def runner(argv, **_kwargs):
        calls.append(argv)
        if argv[:3] == ["docker", "volume", "inspect"]:
            return _completed(argv)
        return _completed(argv, stdout="123\n" + "d" * 64 + "\n")

    with pytest.raises(MediaError, match="were preserved") as raised:
        stage(
            "image.test", "v1", lock_path=_lock(tmp_path), models_volume="media-models",
            user_volume="media-user", dry_run=False, runner=runner,
        )
    assert raised.value.code == "media_bundle_conflict"
    assert all(argv[:3] != ["docker", "volume", "create"] for argv in calls)


@pytest.mark.parametrize("volume", ["C:/models", "../models", "-models", "models/name"])
def test_volume_names_are_fail_closed(tmp_path, volume):
    with pytest.raises(MediaError, match="named Docker volume"):
        inventory(
            "image.test", "v1", lock_path=_lock(tmp_path), models_volume=volume,
            runner=lambda *_args, **_kwargs: pytest.fail("Docker was called"),
        )
