import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from anvil_serving import docker_disk_restore as restore


class Desktop:
    status = "running"
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[:2] == ["docker", "--context"]:
            return subprocess.CompletedProcess(argv, 1 if self.status == "stopped" else 0, "", "")
        if argv[2] == "stop":
            self.status = "stopped"
        return subprocess.CompletedProcess(argv, 0, json.dumps({"Status": self.status}), "")


@pytest.fixture
def disks(tmp_path, monkeypatch):
    source, target = tmp_path / "source.vhdx", tmp_path / "target.vhdx"
    source.write_bytes(b"vhdxfile" + b"saved" * 100)
    target.write_bytes(b"vhdxfilecurrent")
    monkeypatch.setattr(restore.docker_disk, "_is_windows", lambda: True)
    if os.name != "nt":
        monkeypatch.setattr(restore, "_open_locked", lambda path, **kw: path.open("rb"))
        def replace(target, staged, backup):
            os.link(target, backup)
            os.replace(staged, target)
        monkeypatch.setattr(restore, "_replace_with_backup", replace)
    return source, target


def test_preview_and_confirm_preserve_both_disks(disks):
    source, target = disks
    original, previous = source.read_bytes(), target.read_bytes()
    desktop = Desktop()
    result = restore.restore_docker_data_disk(source, target, confirm=True, dry_run=True, runner=desktop)
    assert result["outcome"] == "preview"
    assert all(call[2] != "stop" for call in desktop.calls)
    assert target.read_bytes() == previous
    result = restore.restore_docker_data_disk(source, target, confirm=True, runner=desktop)
    assert result["outcome"] == "restored", result
    assert desktop.status == "stopped"
    assert source.read_bytes() == target.read_bytes() == original
    assert Path(result["backup"]).read_bytes() == previous
    assert result["sha256"] == hashlib.sha256(original).hexdigest()


def test_refuses_same_file_and_insufficient_space_before_stop(disks, monkeypatch):
    source, target = disks
    desktop = Desktop()
    with pytest.raises(ValueError, match="different"):
        restore.restore_docker_data_disk(source, source, confirm=True, runner=desktop)
    monkeypatch.setattr(restore.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 1})())
    with pytest.raises(ValueError, match="insufficient"):
        restore.restore_docker_data_disk(source, target, confirm=True, runner=desktop)
    assert not desktop.calls


@pytest.mark.parametrize("failure", ["lock", "copy", "restart", "replace"])
def test_failure_preserves_source_and_target(disks, monkeypatch, failure):
    source, target = disks
    original, previous = source.read_bytes(), target.read_bytes()
    desktop = Desktop()
    def fail(*args, **kwargs):
        raise OSError("injected " + failure)
    if failure == "lock":
        monkeypatch.setattr(restore, "_open_locked", fail)
    elif failure == "copy":
        monkeypatch.setattr(restore, "_copy_verified", fail)
    elif failure == "replace":
        monkeypatch.setattr(restore, "_replace_with_backup", fail)
    else:
        original_copy = restore._copy_verified
        def restart(*args):
            digest = original_copy(*args)
            desktop.status = "running"
            return digest
        monkeypatch.setattr(restore, "_copy_verified", restart)
    result = restore.restore_docker_data_disk(source, target, confirm=True, runner=desktop)
    assert result["outcome"] == "failed"
    assert source.read_bytes() == original
    assert target.read_bytes() == previous
    assert "backup" in result and "recovery" in result


def test_checksum_mismatch_refuses_replacement(disks, monkeypatch):
    source, target = disks
    previous = target.read_bytes()
    monkeypatch.setattr(restore.hashlib, "file_digest", lambda *args: hashlib.sha256(b"wrong"))
    result = restore.restore_docker_data_disk(source, target, confirm=True, runner=Desktop())
    assert result["outcome"] == "failed"
    assert "SHA-256" in result["error"]
    assert target.read_bytes() == previous


def test_move_uses_real_windows_rename_and_preserves_backup(disks):
    source, target = disks
    original, previous = source.read_bytes(), target.read_bytes()
    result = restore.restore_docker_data_disk(source, target, confirm=True, move=True, runner=Desktop())
    assert result["outcome"] == "restored", result
    assert not source.exists()
    assert target.read_bytes() == original
    assert Path(result["backup"]).read_bytes() == previous
    assert result["copy_bytes"] == 0 and result["source_preserved"] is False


def test_rename_failure_rolls_back_original_target(disks, monkeypatch):
    source, target = disks
    backup = target.with_name("backup.vhdx")
    original_rename = Path.rename
    def fail_second(path, destination):
        if path == source:
            raise OSError("injected rename failure")
        return original_rename(path, destination)
    monkeypatch.setattr(Path, "rename", fail_second)
    # Test the real helper on every platform, independent of the fixture mock.
    monkeypatch.undo()
    monkeypatch.setattr(Path, "rename", fail_second)
    with pytest.raises(OSError, match="injected"):
        restore._replace_with_backup(target, source, backup)
    assert source.read_bytes().startswith(b"vhdxfilesaved")
    assert target.read_bytes() == b"vhdxfilecurrent"
    assert not backup.exists()


def test_engine_answering_despite_stopped_status_is_refused(disks):
    source, target = disks
    desktop = Desktop()
    def still_answering(argv, **kwargs):
        if argv[:2] == ["docker", "--context"]:
            return subprocess.CompletedProcess(argv, 0, "engine", "")
        return desktop(argv, **kwargs)
    result = restore.restore_docker_data_disk(source, target, confirm=True, move=True, runner=still_answering)
    assert result["outcome"] == "failed"
    assert "still answers" in result["error"]
    assert source.exists() and target.read_bytes() == b"vhdxfilecurrent"


def test_reparse_ancestor_refused(disks, monkeypatch):
    source, target = disks
    original = Path.lstat
    def reparse(path):
        if path == source.parent:
            return type("Reparse", (), {"st_mode": 0, "st_file_attributes": 0x400})()
        return original(path)
    monkeypatch.setattr(Path, "lstat", reparse)
    with pytest.raises(ValueError, match="reparse"):
        restore.restore_docker_data_disk(source, target, runner=Desktop())


@pytest.mark.skipif(os.name != "nt", reason="real Windows sharing contract")
def test_windows_handle_denies_writer_and_occupied_file_lock(disks):
    source, _ = disks
    with restore._open_locked(source, allow_replace=True):
        with pytest.raises(OSError):
            source.open("r+b")
    with source.open("r+b"):
        with pytest.raises(OSError):
            restore._open_locked(source)


def test_rollback_failure_keeps_original_at_reported_backup(disks, monkeypatch):
    source, target = disks
    backup = target.with_name("backup.vhdx")
    original_rename = Path.rename
    monkeypatch.undo()
    def fail_after_first(path, destination):
        if path in {source, backup}:
            raise OSError("injected source and rollback failure")
        return original_rename(path, destination)
    monkeypatch.setattr(Path, "rename", fail_after_first)
    with pytest.raises(OSError):
        restore._replace_with_backup(target, source, backup)
    assert source.exists()
    assert backup.read_bytes() == b"vhdxfilecurrent"


def test_source_path_substitution_during_move_is_refused(disks):
    source, target = disks
    desktop = Desktop()
    checks = 0
    def substitute(argv, **kwargs):
        nonlocal checks
        if argv[:2] == ["docker", "--context"]:
            checks += 1
            if checks == 2:
                source.rename(source.with_name("moved.vhdx"))
                source.write_bytes(b"vhdxfileSUBSTITUTED")
        return desktop(argv, **kwargs)
    result = restore.restore_docker_data_disk(source, target, confirm=True, move=True, runner=substitute)
    assert result["outcome"] == "failed"
    assert "source identity" in result["error"]
    assert target.read_bytes() == b"vhdxfilecurrent"
