"""Independent review regressions for acquisition custody and concurrency."""
import json
import os
import sys

import pytest

from anvil_serving import model_pull_native as native


def test_different_repositories_share_disk_admission_lock(tmp_path):
    with native._writer(tmp_path, "models--one--first"):
        with pytest.raises(native.NativePullError, match="already in progress"):
            with native._writer(tmp_path, "models--two--second"):
                pytest.fail("concurrent cache admission")
    with native._writer(tmp_path, "models--two--second"):
        pass


def test_receipt_creation_is_exclusive_and_never_replaces_another_file(tmp_path):
    target = tmp_path / "result.json"
    first = native._reserve_receipt(target)
    try:
        with pytest.raises(native.NativePullError, match="new file"):
            native._reserve_receipt(target)
        native._write_receipt(target, {"status": "downloading"}, first)
        if os.name == "nt":
            # The Windows handle denies deletion/rename while reserved. The
            # POSIX replacement attempt below is prevented at the OS boundary.
            with pytest.raises(PermissionError):
                target.rename(tmp_path / "original.json")
            assert not (tmp_path / "original.json").exists()
            assert json.loads(target.read_text())["status"] == "downloading"
            native._write_receipt(target, {"status": "verified"}, first)
            assert json.loads(target.read_text())["status"] == "verified"
            return
        target.rename(tmp_path / "original.json")
        target.write_text('prior evidence')
        with pytest.raises(native.NativePullError, match="path changed"):
            native._write_receipt(target, {"status": "verified"}, first)
        assert target.read_text() == 'prior evidence'
        assert json.loads((tmp_path / "original.json").read_text())["status"] == "downloading"
    finally:
        os.close(first)


def test_filtered_snapshot_refuses_prior_excluded_files_without_deletion(tmp_path):
    (tmp_path / "config.json").write_text('{}')
    (tmp_path / "excluded.py").write_text('unexpected runtime code')
    with pytest.raises(native.NativePullError, match="outside the declared selection"):
        native._exact_snapshot_entries(tmp_path, {"files": [{"path": "config.json"}]})
    assert (tmp_path / "excluded.py").read_text() == 'unexpected runtime code'


def test_downloader_version_rejects_python_interpreter():
    with pytest.raises(native.NativePullError, match="bounded hf version"):
        native._downloader_version(sys.executable, dict(os.environ))


def test_real_process_records_bounded_hf_version(tmp_path):
    executable = tmp_path / ("hf.cmd" if os.name == "nt" else "hf")

    def write_version(version):
        if os.name == "nt":
            executable.write_bytes(f"@echo off\r\n@echo {version}\r\n".encode())
        else:
            executable.write_text(f'#!/bin/sh\nprintf "{version}\\n"\n')

    write_version("1.33.0")
    executable.chmod(0o700)
    assert native._downloader_version(str(executable), dict(os.environ)) == "1.33.0"
    before = native._executable_identity(str(executable))
    write_version("1.34.0")
    assert before != native._executable_identity(str(executable))
