from __future__ import annotations

from pathlib import Path
import os
from subprocess import CompletedProcess
import sys
from types import SimpleNamespace

import pytest

from anvil_serving.workbench_app import pi_storage
from anvil_serving.workbench_app.pi_storage import (
    GIB,
    PiStorageError,
    PiStorageManager,
    storage_config,
    validate_pool,
)


def _config(root: Path) -> dict[str, object]:
    pool = root / "pool"
    return {
        "state_path": str(root / "server" / "workbench.sqlite"),
        "pi_storage": {
            "image_path": str(root / "pi-storage.ext4"),
            "pool_path": str(pool),
            "size_bytes": GIB,
        },
        "pi": {
            "uid": 12345,
            "gid": 12346,
            "state_root": str(root / "server" / "pi-journal"),
            "runner_storage_root": str(pool / "pi"),
        },
        "projects": [{"id": "one", "runner_root": str(pool / "tasks")}],
    }


def _mount_fixture(root: Path, config: dict[str, object]) -> tuple[Path, Path]:
    storage = config["pi_storage"]
    assert isinstance(storage, dict)
    image = Path(storage["image_path"])
    pool = Path(storage["pool_path"])
    image.write_bytes(b"")
    image.chmod(0o600)
    with image.open("r+b") as output:
        output.truncate(GIB)
    pool.mkdir()
    pool.chmod(0o700)
    mountinfo = root / "mountinfo"
    mountinfo.write_text(
        f"41 23 7:7 / {pool} rw,nosuid,nodev - ext4 /dev/loop7 rw,nosuid,nodev\n",
        encoding="utf-8",
    )
    sys_block = root / "sys" / "loop7" / "loop"
    sys_block.mkdir(parents=True)
    (sys_block / "backing_file").write_text(str(image) + "\n", encoding="utf-8")
    return mountinfo, root / "sys"


def _claim_expected_owners(monkeypatch, config: dict[str, object]) -> None:
    original = pi_storage.os.stat
    storage = config["pi_storage"]
    pi = config["pi"]
    assert isinstance(storage, dict) and isinstance(pi, dict)
    image, pool = Path(storage["image_path"]), Path(storage["pool_path"])

    def fake_stat(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if Path(path) == image:
            return SimpleNamespace(st_mode=result.st_mode, st_size=result.st_size, st_uid=0, st_gid=0)
        if Path(path) == pool:
            return SimpleNamespace(st_mode=result.st_mode, st_size=result.st_size, st_uid=pi["uid"], st_gid=pi["gid"], st_dev=os.makedev(7, 7))
        return result

    monkeypatch.setattr(pi_storage.os, "stat", fake_stat)


@pytest.mark.skipif(sys.platform != "linux", reason="Pi storage mount proof is Linux-only")
def test_validate_pool_rejects_symlinked_runner_path(tmp_path, monkeypatch):
    config = _config(tmp_path)
    mountinfo, sys_block = _mount_fixture(tmp_path, config)
    storage = config["pi_storage"]
    assert isinstance(storage, dict)
    outside = tmp_path / "outside"
    outside.mkdir()
    (Path(storage["pool_path"]) / "pi").symlink_to(outside, target_is_directory=True)
    _claim_expected_owners(monkeypatch, config)

    with pytest.raises(PiStorageError, match="symlink"):
        validate_pool(config, mountinfo_path=mountinfo, sys_block=sys_block)


@pytest.mark.skipif(sys.platform != "linux", reason="Pi storage image contract is Linux-only")
def test_existing_image_is_proven_and_never_reformatted(tmp_path, monkeypatch):
    config = _config(tmp_path)
    storage = config["pi_storage"]
    assert isinstance(storage, dict)
    image = Path(storage["image_path"])
    image.write_bytes(b"")
    image.chmod(0o600)
    with image.open("r+b") as output:
        output.truncate(GIB)
    calls: list[list[str]] = []
    original = pi_storage.os.stat

    def fake_stat(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if Path(path) == image:
            return SimpleNamespace(st_mode=result.st_mode, st_size=result.st_size, st_uid=0, st_gid=0)
        return result

    def run(argv, **_kwargs):
        calls.append(argv)
        return CompletedProcess(argv, 0, "ext4\n", "")

    monkeypatch.setattr(pi_storage.os, "stat", fake_stat)
    manager = PiStorageManager(config, run=run)
    assert manager._create_image() is False
    assert calls == [["blkid", "-p", "-o", "value", "-s", "TYPE", str(image)]]


def test_unconfirmed_provision_is_a_reviewable_no_mutation_plan(tmp_path):
    config = _config(tmp_path)
    calls: list[list[str]] = []
    manager = PiStorageManager(config, run=lambda argv, **_kwargs: calls.append(argv) or CompletedProcess(argv, 0, "", ""))

    result = manager.provision(confirm=False)

    assert result["dry_run"] is True
    assert result["filesystem"] == "ext4"
    assert result["mount_options"] == ["loop", "nodev", "nosuid"]
    assert calls == []


def test_storage_configuration_and_dry_run_remain_portable_but_windows_provisioning_is_refused(tmp_path):
    config = _config(tmp_path)

    parsed = storage_config(config)
    manager = PiStorageManager(config, platform="win32")

    assert parsed.pool_path == Path(config["pi_storage"]["pool_path"])
    assert manager.provision(confirm=False)["dry_run"] is True
    with pytest.raises(PiStorageError, match="only on Linux"):
        manager.provision(confirm=True)
    with pytest.raises(PiStorageError, match="only on Linux"):
        validate_pool(config, platform="win32")


@pytest.mark.skipif(sys.platform != "linux", reason="Pi storage systemd unit is Linux-only")
def test_systemd_unit_is_idempotent_and_refuses_foreign_content(tmp_path):
    config = _config(tmp_path)
    manager = PiStorageManager(config, systemd_root=tmp_path / "units")

    unit = manager._write_unit()
    again = manager._write_unit()

    assert unit == again
    rendered = unit.read_text(encoding="utf-8")
    assert "Type=ext4" in rendered
    assert "Options=loop,nodev,nosuid" in rendered
    unit.write_text("[Mount]\nWhat=/foreign\n", encoding="utf-8")
    with pytest.raises(PiStorageError, match="reviewed configuration"):
        manager._write_unit()


@pytest.mark.skipif(sys.platform != "linux", reason="Pi storage provisioning is Linux-only")
def test_free_space_failure_precedes_new_image_creation(tmp_path, monkeypatch):
    config = _config(tmp_path / "new-private-root")
    image = Path(config["pi_storage"]["image_path"])
    manager = PiStorageManager(config, geteuid=lambda: 0)
    monkeypatch.setattr(pi_storage.os, "statvfs", lambda _path: SimpleNamespace(f_bavail=0, f_frsize=1))

    with pytest.raises(PiStorageError, match="insufficient free space"):
        manager.provision(confirm=True)

    assert not image.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Pi storage image contract is Linux-only")
def test_rollback_never_deletes_a_replaced_image(tmp_path):
    config = _config(tmp_path)
    image = Path(config["pi_storage"]["image_path"])
    image.write_bytes(b"created")
    image.chmod(0o600)
    original = image.stat()
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"operator-owned")
    replacement.chmod(0o600)
    replacement.replace(image)
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("", encoding="utf-8")
    manager = PiStorageManager(config, mountinfo_path=mountinfo)
    manager._created_image_identity = (original.st_dev, original.st_ino)

    manager._rollback_new_image()

    assert image.read_bytes() == b"operator-owned"
