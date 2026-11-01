"""Explicit native WAL conversion, guarded recovery and standalone rollback."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from anvil_serving.router import keys
from anvil_serving.router.usage_store import UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path

native = pytest.mark.skipif(os.name != 'posix' or not hasattr(sqlite3.Connection, 'setconfig')
                           or not hasattr(sqlite3, 'SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE')
                           or (sqlite3.sqlite_version_info < (3, 51, 3)
                               and sqlite3.sqlite_version_info not in {(3, 44, 6), (3, 50, 7)}),
                           reason='native offline WAL requires POSIX custody, fixed SQLite and setconfig')


def configured(tmp_path):
    store = keys.KeyStore.initialize(tmp_path / 'store' / 'keys.sqlite3')
    UsageStore(store).migrate()
    config = tmp_path / 'router.toml'
    config.write_text('[server]\nauth_env="SYNTHETIC_MASTER"\napi_keys_path=' + json.dumps(str(store.path)) +
        '\nadmission_state_path=' + json.dumps(str(store.path.parent / 'admission.json')) +
        '\nrouter_owner_id="synthetic-owner"\nrouter_owner_roster=["synthetic-owner"]\nusage_domain_id="synthetic-domain"\n')
    config.chmod(0o600)
    return store, config


@native
def test_pending_permission_and_unapproved_wal_refuse_before_file_sql(tmp_path, monkeypatch):
    store, config = configured(tmp_path)
    # A conversion interruption after permission publication stays closed to
    # normal SQL, and the same fenced operation can finish without deletion.
    original = keys._WriterConnection.execute
    def fail_mode(self, sql, *args):
        if sql == 'PRAGMA journal_mode=WAL':
            raise sqlite3.OperationalError('synthetic conversion interruption')
        return original(self, sql, *args)
    monkeypatch.setattr(keys._WriterConnection, 'execute', fail_mode)
    with pytest.raises(sqlite3.OperationalError):
        keys._migrate_offline(str(config), str(tmp_path / 'before.sqlite3'), journal_mode='WAL')
    marker = Path(str(store.path) + '.sqlite-policy')
    assert marker.exists()
    monkeypatch.setattr(keys._WriterConnection, 'execute', original)
    connect = sqlite3.connect
    def no_file_sql(*args, **kwargs):
        raise AssertionError('normal pending mode must refuse before SQLite opens')
    monkeypatch.setattr(sqlite3, 'connect', no_file_sql)
    with pytest.raises(keys.KeyStoreError): keys.KeyStore(store.path)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    keys._migrate_offline(str(config), str(tmp_path / 'pending.sqlite3'), journal_mode='WAL')
    protected = marker.read_bytes()
    marker.unlink()  # Only this owned synthetic permission, never a sidecar.
    monkeypatch.setattr(sqlite3, 'connect', no_file_sql)
    with pytest.raises(keys.KeyStoreError): keys.KeyStore(store.path)
    marker.write_bytes(protected); marker.chmod(0o600)


@pytest.mark.parametrize('suffix', ['-journal', '-wal', '-shm'])
@pytest.mark.parametrize('defect', ['link', 'hardlink', 'public'])
def test_retained_recovery_sidecar_refuses_all_file_opens(tmp_path, monkeypatch, suffix, defect):
    store, _ = configured(tmp_path)
    sidecar = Path(str(store.path) + suffix)
    original = tmp_path / 'foreign'; original.write_bytes(b'foreign synthetic sidecar'); original.chmod(0o600)
    if defect == 'link': sidecar.symlink_to(original)
    elif defect == 'hardlink': os.link(original, sidecar)
    elif os.name == 'nt':
        sidecar.write_bytes(b'unsafe')
        from tests.bootstrap_windows_fixtures import WindowsFixtureTree
        WindowsFixtureTree(tmp_path).owner_readonly_with_everyone_write(sidecar)
    else:
        sidecar.write_bytes(b'unsafe'); sidecar.chmod(0o644)
    def no_file_sql(*args, **kwargs): raise AssertionError('unsafe sidecar must refuse before SQLite opens')
    monkeypatch.setattr(sqlite3, 'connect', no_file_sql)
    try:
        with pytest.raises(keys.KeyStoreError): keys.KeyStore(store.path)
    finally:
        if defect == 'public' and os.name == 'nt': WindowsFixtureTree(tmp_path).restore_full_control(sidecar)
    assert original.read_bytes() == b'foreign synthetic sidecar'


@pytest.mark.skipif(os.name != 'posix', reason='held POSIX verifier descriptors')
@pytest.mark.parametrize('changed', ['database', 'parent', 'ancestor', 'permissions', 'hardlink'])
@pytest.mark.parametrize('external', [False, True])
def test_per_open_held_proof_refuses_setup_substitution_and_closes_descriptors(tmp_path, monkeypatch, changed, external):
    from contextlib import nullcontext
    root = tmp_path / 'ancestor'; root.mkdir(mode=0o700)
    store, _ = configured(root)
    original = keys._WriterConnection.execute
    captured = []
    reader = store._reader_custody
    def custody(**kwargs):
        captured.append(kwargs['_proof'])
        return reader(**kwargs)
    monkeypatch.setattr(store, '_reader_custody', custody)
    def replace(self, sql, *args):
        result = original(self, sql, *args)
        if sql == 'PRAGMA user_version':
            if changed == 'permissions': store.path.chmod(0o644)
            elif changed == 'hardlink': os.link(store.path, tmp_path / 'extra-link')
            elif changed == 'database':
                before = store.path.read_bytes()
                store.path.unlink(); store.path.write_bytes(before); store.path.chmod(0o600)
            else:
                target = store.path.parent if changed == 'parent' else root
                target.rename(tmp_path / 'displaced')
                target.mkdir(mode=0o700)
        return result
    monkeypatch.setattr(keys._WriterConnection, 'execute', replace)
    with pytest.raises(keys.KeyStoreError):
        with store._external_ownership() if external else nullcontext():
            with store._connect():
                pytest.fail('a changed path must refuse before a connection escapes')
    assert captured and captured[0]['objects']
    for descriptor in captured[0]['objects'].values():
        with pytest.raises(OSError): os.fstat(descriptor)


@pytest.mark.skipif(os.name != 'posix', reason='held POSIX parent descriptors')
@pytest.mark.parametrize('changed', ['parent', 'ancestor', 'database'])
def test_anchored_old_parent_never_authorizes_sql_through_replaced_named_tree(tmp_path, monkeypatch, changed):
    root = tmp_path / 'ancestor'; root.mkdir(mode=0o700)
    store, _ = configured(root)
    sidecar = Path(str(store.path) + '-shm'); sidecar.write_bytes(b'owned leaf'); sidecar.chmod(0o600)
    original = keys._posix_private_path
    swapped = []
    def replace(path, **kwargs):
        result = original(path, **kwargs)
        if path == sidecar and kwargs.get('_parent_fd') is not None:
            if changed == 'database':
                before = store.path.read_bytes()
                store.path.unlink(); store.path.write_bytes(before); store.path.chmod(0o600)
            else:
                target = store.path.parent if changed == 'parent' else root
                target.rename(tmp_path / 'displaced'); target.mkdir(mode=0o700)
                if changed == 'ancestor':
                    # The immediate held parent is still the SAME inode. Only
                    # the higher named ancestor changed and must still veto SQL.
                    (tmp_path / 'displaced' / 'store').rename(root / 'store')
            swapped.append(changed)
        return result
    def no_file_sql(*args, **kwargs):
        pytest.fail('anchored old tree must not authorize the replaced SQL path')
    monkeypatch.setattr(keys, '_posix_private_path', replace)
    monkeypatch.setattr(sqlite3, 'connect', no_file_sql)
    with pytest.raises(keys.KeyStoreError):
        with store._connect(): pass
    assert swapped == [changed]


@native
def test_default_checkpoint_threshold_and_long_reader_preserve_latest_rows(tmp_path):
    store, config = configured(tmp_path)
    keys._migrate_offline(str(config), str(tmp_path / 'before.sqlite3'), journal_mode='WAL')
    with store._write() as connection:
        connection.execute('CREATE TABLE synthetic_threshold(id INTEGER PRIMARY KEY, payload BLOB)')
    with store._connect() as reader:
        reader.execute('BEGIN')
        assert reader.execute('SELECT count(*) FROM synthetic_threshold').fetchone() == (0,)
        with store._write() as writer:
            for i in range(1001):
                writer.execute('INSERT INTO synthetic_threshold VALUES(?,?)', (i, b'x' * 4096))
        with store._write() as writer:
            busy, log, copied = writer.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
            assert busy == 0 and log > 1000 and copied < log
        reader.execute('COMMIT')
    with store._write() as writer:
        busy, log, copied = writer.execute('PRAGMA wal_checkpoint(FULL)').fetchone()
        assert busy == 0 and copied == log
        assert writer.execute('SELECT count(*) FROM synthetic_threshold').fetchone() == (1001,)
    assert hashlib.sha256(Path(str(store.path) + '-wal').read_bytes()).digest()


@native
def test_existing_read_lease_blocks_conversion_without_pending_permission(tmp_path):
    store, config = configured(tmp_path)
    with store._connect() as reader:
        assert reader.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        with pytest.raises(keys.KeyStoreError):
            keys._migrate_offline(str(config), str(tmp_path / 'before.sqlite3'), journal_mode='WAL')
        assert not Path(str(store.path) + '.sqlite-policy').exists()
        assert not (tmp_path / 'before.sqlite3').exists()
    keys._migrate_offline(str(config), str(tmp_path / 'before.sqlite3'), journal_mode='WAL')


@pytest.mark.skipif(os.name != 'posix', reason='actual thread-local POSIX gate lifetime')
def test_borrowed_gate_cancellation_and_inconsistent_nesting_never_escape_scope(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store, _ = configured(tmp_path)
    gate = Path(str(store.path) + '.router-writers.lock')
    with pytest.raises(KeyboardInterrupt):
        with store._write() as db:
            db.execute('BEGIN IMMEDIATE')
            raise KeyboardInterrupt
    assert not hasattr(store._writer_context, 'external_reader_custody') and not store._writer_queue
    with store._external_ownership():
        custody = store._writer_context.external_reader_custody
        with store._external_ownership():
            nested = store._writer_context.external_reader_custody
            assert nested is not custody and nested[0] != custody[0]
            os.fstat(custody[0]); os.fstat(nested[0])
        assert store._writer_context.external_reader_custody is custody
        os.fstat(custody[0])
        with pytest.raises(OSError): os.fstat(nested[0])
        def independent():
            assert not hasattr(store._writer_context, 'external_reader_custody')
            with store._reader_custody(): pass
        with ThreadPoolExecutor(max_workers=1) as pool: pool.submit(independent).result(timeout=3)
        other = keys.KeyStore(store.path)
        assert not hasattr(other._writer_context, 'external_reader_custody')
        with other._reader_custody(): pass
    assert not hasattr(store._writer_context, 'external_reader_custody')
    for name, value in [('native_reader_custody', object()), ('offline_custody', True)]:
        setattr(store._writer_context, name, value)
        try:
            with pytest.raises(keys.KeyStoreError):
                with store._external_ownership(): pass
            assert getattr(store._writer_context, name) is value
        finally:
            delattr(store._writer_context, name)
    metadata, token = store.create('after cancellation', ['llm.primary'], ['/v1/chat/completions'])
    assert store.authenticate(token).key_id == metadata['key_id'] and gate.exists()
