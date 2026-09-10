import os
import stat
from types import SimpleNamespace

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app import credentials
from anvil_serving.workbench_app.credentials import resolve_secret


def test_environment_secret_is_resolved_without_exposure():
    assert resolve_secret("DECLARED_KEY", {"DECLARED_KEY": "env-secret"}) == "env-secret"


@pytest.mark.skipif(os.name != "posix", reason="private file mode proof requires POSIX permissions")
def test_protected_file_secret_is_resolved_only_with_private_mode(tmp_path):
    key = tmp_path / "key"
    key.write_text("file-secret\n")
    key.chmod(0o600)
    assert resolve_secret("file:" + str(key), {}) == "file-secret"
    key.chmod(0o644)
    with pytest.raises(ObservatoryError) as caught:
        resolve_secret("file:" + str(key), {})
    assert caught.value.status == 503 and "file-secret" not in str(caught.value)


@pytest.mark.skipif(os.name == "posix", reason="POSIX private-mode acceptance is covered separately")
def test_protected_file_secret_is_refused_when_mode_cannot_be_proven(tmp_path):
    key = tmp_path / "key"
    key.write_text("file-secret\n")

    with pytest.raises(ObservatoryError):
        resolve_secret("file:" + str(key), {})


def _prove_secure_file_mode(monkeypatch):
    original_fstat = credentials.os.fstat

    def secure_mode(fd):
        info = original_fstat(fd)
        return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=info.st_size)

    monkeypatch.setattr(credentials.os, "fstat", secure_mode)


def test_symlinked_secret_file_fails_closed(tmp_path):
    key = tmp_path / "key"
    key.write_text("secret")
    key.chmod(0o600)
    link = tmp_path / "link"
    try:
        link.symlink_to(key)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ObservatoryError):
        resolve_secret("file:" + str(link), {})


def test_fifo_secret_file_fails_closed(tmp_path):
    mkfifo = getattr(os, "mkfifo", None)
    if mkfifo is None:
        pytest.skip("mkfifo is unavailable")

    fifo = tmp_path / "fifo"
    mkfifo(fifo, 0o600)
    with pytest.raises(ObservatoryError):
        resolve_secret("file:" + str(fifo), {})


def test_oversize_and_multiline_secret_files_fail_closed(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("secret")
    _prove_secure_file_mode(monkeypatch)
    for value in ("a" * 8193, "first\nsecond"):
        key.write_text(value)
        with pytest.raises(ObservatoryError):
            resolve_secret("file:" + str(key), {})
