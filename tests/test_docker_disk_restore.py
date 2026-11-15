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
