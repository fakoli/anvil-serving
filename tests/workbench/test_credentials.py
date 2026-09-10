import os

import pytest

from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.workbench_app.credentials import resolve_secret


def test_protected_file_and_environment_are_resolved_without_exposure(tmp_path):
    key = tmp_path / "key"
    key.write_text("file-secret\n")
    key.chmod(0o600)
    assert resolve_secret("file:" + str(key), {}) == "file-secret"
    assert resolve_secret("DECLARED_KEY", {"DECLARED_KEY": "env-secret"}) == "env-secret"
    key.chmod(0o644)
    with pytest.raises(ObservatoryError) as caught:
        resolve_secret("file:" + str(key), {})
    assert caught.value.status == 503 and "file-secret" not in str(caught.value)


def test_symlink_fifo_oversize_and_multiline_fail_closed(tmp_path):
    key = tmp_path / "key"
    key.write_text("secret")
    key.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(key)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    for path in (link, fifo):
        with pytest.raises(ObservatoryError):
            resolve_secret("file:" + str(path), {})
    for value in ("a" * 8193, "first\nsecond"):
        key.write_text(value)
        with pytest.raises(ObservatoryError):
            resolve_secret("file:" + str(key), {})
