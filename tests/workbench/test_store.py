import os
import stat
from types import SimpleNamespace

import pytest

from anvil_serving.workbench_app.store import PrivateStore
from anvil_serving.workbench_app import store as store_module


def test_store_without_nofollow_still_rejects_reparse_files(tmp_path, monkeypatch):
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    path = tmp_path / "state.sqlite"
    store = PrivateStore(path)
    store.close()
    original = store_module.Path.lstat

    def linked(candidate, *args, **kwargs):
        if candidate == path:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_file_attributes=0x400)
        return original(candidate, *args, **kwargs)

    monkeypatch.setattr(store_module.Path, "lstat", linked)
    with pytest.raises(ValueError, match="reparse"):
        PrivateStore(path)


def test_store_refuses_directory_instead_of_opening_special_state(tmp_path):
    with pytest.raises(ValueError, match="regular file"):
        PrivateStore(tmp_path)
