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


@pytest.mark.parametrize('sidecar,directory,links,unlinked,private,expected_cause', [
    (True, False, 0, True, True, True),
    (False, False, 0, True, True, False),
    (True, False, 0, False, True, False),
    (True, False, 0, True, False, False),
    (True, False, 2, True, True, False),
    (True, True, 0, True, True, False),
])
def test_zero_link_object_is_never_success_or_general_private_path_absence(tmp_path, monkeypatch,
        sidecar, directory, links, unlinked, private, expected_cause):
    leaf = tmp_path / 'synthetic-journal'
    leaf.write_bytes(b'synthetic')
    descriptor = os.open(leaf, os.O_RDONLY)
    monkeypatch.setattr(keys, '_windows_open_verification_file', lambda _: descriptor)
    monkeypatch.setattr(keys.bootstrap_shim, '_windows_handle_details_from_descriptor',
                        lambda _: (directory, (1, 2, 3, 4, 5), links))
    checks = []
    def absent(fd, path):
        checks.append(True)
        return unlinked
    monkeypatch.setattr(keys, '_windows_unlinked_sidecar', absent)
    def require_private(fd):
        if not private: raise keys.auth_file.AuthFileError('synthetic refusal')
    monkeypatch.setattr(keys.auth_file, '_require_windows_private_descriptor', require_private)
    with pytest.raises(keys.KeyStoreError) as raised:
        keys._windows_private_path(leaf, directory=False, _sidecar=sidecar)
    cause = raised.value.__cause__
    assert isinstance(cause, FileNotFoundError) is expected_cause
    if expected_cause: assert cause.filename == str(leaf)
    if not sidecar or directory or links > 1 or not private: assert not checks
    with pytest.raises(OSError): os.fstat(descriptor)


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


@pytest.mark.parametrize('change', ['unchanged', 'parent_changed', 'recreated', 'unknown_leaf', 'second_absence_unknown'])
def test_sidecar_absence_handler_revalidates_parent_and_repeated_native_absence(tmp_path, monkeypatch, change):
    parent = tmp_path / 'private'
    native_windows = keys._is_windows()
    original_directory = keys._secure_directory
    original_directory(parent, create=True)
    db = parent / 'keys.sqlite3'
    leaf = Path(str(db) + '-journal')
    leaf.write_bytes(b'synthetic journal')
    leaf.chmod(0o600)
    checks = []
    def private_parent(path, **kwargs):
        # Exercise the actual parent guard on this platform; native Windows
        # tests above separately prove its held descriptor/DACL contract.
        with monkeypatch.context() as local:
            local.setattr(keys, '_is_windows', lambda: native_windows)
            original_directory(path, **kwargs)
        checks.append(path)
    def disappear(path, **kwargs):
        assert path == leaf and kwargs['_sidecar'] is True
        leaf.unlink()
        if change == 'parent_changed':
            parent.rename(tmp_path / 'displaced')
            with monkeypatch.context() as local:
                local.setattr(keys, '_is_windows', lambda: native_windows)
                original_directory(parent, create=True)
        elif change == 'recreated':
            leaf.write_bytes(b'recreated synthetic')
            leaf.chmod(0o600)
        raise keys.KeyStoreError('synthetic vanished leaf') from FileNotFoundError(2, 'synthetic', str(leaf))
    calls = []
    def absent(path):
        calls.append(path)
        if path != leaf: return True
        if leaf.exists(): return False
        if change == 'unknown_leaf': return False
        if change == 'second_absence_unknown' and calls.count(leaf) == 3: return False
        return True
    monkeypatch.setattr(keys, '_is_windows', lambda: True)
    monkeypatch.setattr(keys, '_secure_directory', private_parent)
    monkeypatch.setattr(keys, '_secure_database', disappear)
    monkeypatch.setattr(keys, '_windows_leaf_absent', absent)
    # First lookup must see the retained leaf, so the disappearance is inside
    # validation and cannot be mistaken for an initially absent sidecar.
    if change == 'unchanged':
        assert keys._secure_sidecars(db) == 0
        assert calls.count(leaf) == 3 and len(checks) == 2
    else:
        with pytest.raises(keys.KeyStoreError): keys._secure_sidecars(db)


def test_native_reparse_or_unknown_object_never_reaches_disappearance_classification(tmp_path, monkeypatch):
    leaf = tmp_path / 'synthetic-journal'
    leaf.write_bytes(b'synthetic')
    descriptor = os.open(leaf, os.O_RDONLY)
    monkeypatch.setattr(keys, '_windows_open_verification_file', lambda _: descriptor)
    def unsafe(fd): raise keys.bootstrap_shim._UnsafeObject
    monkeypatch.setattr(keys.bootstrap_shim, '_windows_handle_details_from_descriptor', unsafe)
    monkeypatch.setattr(keys, '_windows_unlinked_sidecar', lambda *a: pytest.fail('unsafe held object was classified absent'))
    with pytest.raises(keys.KeyStoreError) as raised:
        keys._windows_private_path(leaf, directory=False, _sidecar=True)
    assert not isinstance(raised.value.__cause__, FileNotFoundError)
    with pytest.raises(OSError): os.fstat(descriptor)


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
