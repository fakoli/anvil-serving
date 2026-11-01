"""Vanished Windows journals are absence, never accepted zero-link objects."""
import ctypes
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from anvil_serving.router import keys
from tests.router.key_fixtures import tmp_path as tmp_path


@pytest.mark.parametrize('links,pending,directory,ok,flags,error,expected', [
    (0, 1, 0, True, 0xffffffff, 2, True),
    (0, 0, 0, True, 0xffffffff, 2, False),
    (1, 1, 0, True, 0xffffffff, 2, False),
    (2, 1, 0, True, 0xffffffff, 2, False),
    (0, 1, 1, True, 0xffffffff, 2, False),
    (0, 1, 0, False, 0xffffffff, 2, False),
    (0, 1, 0, True, 0xffffffff, 3, False),
    (0, 1, 0, True, 0xffffffff, 5, False),
    (0, 1, 0, True, 0xffffffff, 0, False),
    (0, 1, 0, True, 32, 2, False),
])
def test_only_native_delete_pending_and_exact_leaf_absence(links, pending, directory, ok, flags, error, expected, monkeypatch):
    class NativeCall:
        def __init__(self, function): self.function = function
        def __call__(self, *args): return self.function(*args)
    def information(handle, kind, pointer, size):
        assert handle == 123 and kind == 1
        value = pointer._obj
        value.links, value.delete_pending, value.directory = links, pending, directory
        return ok
    kernel = SimpleNamespace(GetFileInformationByHandleEx=NativeCall(information),
                             GetFileAttributesW=NativeCall(lambda path: flags))
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: error, raising=False)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(get_osfhandle=lambda fd: 123))
    assert keys._windows_unlinked_sidecar(7, Path('synthetic-journal')) is expected


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows shared-delete held journal contract')
def test_actual_windows_held_unlink_becomes_revalidated_sidecar_absence(tmp_path, monkeypatch):
    store = keys.KeyStore.initialize(tmp_path / 'private' / 'keys.sqlite3')
    leaf = Path(str(store.path) + '-journal')
    leaf.write_bytes(b'synthetic journal')
    original = keys._windows_open_verification_file
    raced = []
    def unlink_after_open(path):
        fd = original(path)
        if path == leaf and not raced:
            leaf.unlink()
            raced.append(True)
        return fd
    monkeypatch.setattr(keys, '_windows_open_verification_file', unlink_after_open)
    with store._connect() as connection:
        assert connection.execute('SELECT count(*) FROM keys').fetchone() == (0,)
    assert raced == [True] and not leaf.exists()


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows retained/recreated native object guards')
@pytest.mark.parametrize('replacement', ['recreated', 'recreated_hardlink', 'hardlink', 'directory', 'parent_missing', 'parent_changed'])
def test_actual_windows_sidecar_replacement_refuses_before_sqlite(tmp_path, monkeypatch, replacement):
    store = keys.KeyStore.initialize(tmp_path / 'private' / 'keys.sqlite3')
    leaf = Path(str(store.path) + '-journal')
    if replacement == 'directory':
        leaf.mkdir(mode=0o777)
    else:
        leaf.write_bytes(b'synthetic journal')
        if replacement == 'hardlink': os.link(leaf, tmp_path / 'retained-hardlink')
    original = keys._windows_open_verification_file
    def race(path):
        fd = original(path)
        if path == leaf and replacement in {'recreated', 'recreated_hardlink', 'parent_missing', 'parent_changed'}:
            leaf.unlink()
            if replacement == 'recreated': leaf.write_bytes(b'recreated synthetic journal')
            elif replacement == 'recreated_hardlink': os.link(store.path, leaf)
            else:
                leaf.parent.rename(tmp_path / 'displaced')
                if replacement == 'parent_changed': leaf.parent.mkdir(mode=0o777)
        return fd
    monkeypatch.setattr(keys, '_windows_open_verification_file', race)
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('unsafe recovery input reached SQLite'))
    with pytest.raises(keys.KeyStoreError):
        with store._connect(): pass


@pytest.mark.skipif(os.name != 'nt', reason='actual Windows main database zero-link refusal')
def test_actual_windows_main_database_unlink_is_never_sidecar_absence(tmp_path, monkeypatch):
    store = keys.KeyStore.initialize(tmp_path / 'private' / 'keys.sqlite3')
    original = keys._windows_open_verification_file
    def unlink_after_open(path):
        fd = original(path)
        if path == store.path: path.unlink()
        return fd
    monkeypatch.setattr(keys, '_windows_open_verification_file', unlink_after_open)
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('vanished main database reached SQLite'))
    with pytest.raises(keys.KeyStoreError) as raised:
        with store._connect(): pass
    assert not isinstance(raised.value.__cause__, FileNotFoundError)


def test_windows_per_open_handle_reuse_never_caches_private_checks(tmp_path, monkeypatch):
    leaf = tmp_path / 'synthetic-database'
    leaf.write_bytes(b'synthetic database header')
    opened, checked = [], []
    def open_file(path):
        fd = os.open(path, os.O_RDONLY)
        opened.append(fd)
        return fd
    monkeypatch.setattr(keys, '_windows_open_verification_file', open_file)
    monkeypatch.setattr(keys.bootstrap_shim, '_windows_handle_details_from_descriptor',
                        lambda _: (False, (1, 2, 3, 4, 5), 1))
    def require_private(fd):
        checked.append(fd)
        if len(checked) == 3: raise keys.auth_file.AuthFileError('changed private permissions')
    monkeypatch.setattr(keys.auth_file, '_require_windows_private_descriptor', require_private)
    proof = {'objects': {}, 'ancestors': {}}
    try:
        for _ in range(2):
            assert keys._windows_private_path(leaf, directory=False, header=True, _proof=proof) == leaf.read_bytes()
        assert len(opened) == 1 and len(checked) == 2
        with pytest.raises(keys.KeyStoreError):
            keys._windows_private_path(leaf, directory=False, _proof=proof)
    finally:
        for fd in proof['objects'].values(): os.close(fd)
    assert keys._windows_private_path(leaf, directory=False) is None
    assert len(opened) == 2 and len(checked) == 4
