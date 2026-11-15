from __future__ import annotations

import json
import hashlib
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
            assert "--pull=never" in argv
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
        if "curl --silent --show-error --fail" in script:
            assert "sha256sum -c" in script
            assert "--strict" not in script
            assert ": >" in script
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


def test_host_inventory_hashes_only_safe_pinned_targets_without_docker_or_writes(tmp_path):
    lock = _lock(tmp_path)
    value = json.loads(lock.read_text())
    model = value["workflows"][0]["models"][0]
    data = b"pinned model fixture"
    model.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    lock.write_text(json.dumps(value))
    root = tmp_path / "models"; target = root / model["target"]
    target.parent.mkdir(parents=True)
    def check(**kwargs):
        return inventory("image.test", "v1", lock_path=lock,
                         runner=lambda *a, **k: pytest.fail("Docker called"), **kwargs)
    assert check(models_root=root)["assets"][0]["state"] == "missing"
    target.write_bytes(data)
    assert check(models_root=root)["ready"]
    target.write_bytes(b"wrong")
    assert check(models_root=root)["assets"][0]["state"] == "mismatch"
    assert target.read_bytes() == b"wrong"
    for kwargs in ({}, {"models_root": root, "models_volume": "models"}):
        with pytest.raises(MediaError, match="exactly one"):
            check(**kwargs)
    model["target"] = "../outside.bin"; lock.write_text(json.dumps(value))
    with pytest.raises(MediaError, match="unsafe"):
        check(models_root=root)


def test_host_inventory_rejects_symlink_escape(tmp_path):
    lock = _lock(tmp_path); root = tmp_path / "models"; root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    try:
        (root / "diffusion_models").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("test host cannot create directory symlinks")
    with pytest.raises(MediaError, match="escapes"):
        inventory("image.test", "v1", lock_path=lock, models_root=root)
