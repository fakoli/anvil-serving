"""Explicit native WAL conversion, guarded recovery and standalone rollback."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

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
def test_offline_conversion_all_openers_and_newest_standalone_backup(tmp_path):
    store, config = configured(tmp_path)
    metadata, token = store.create('before conversion', ['llm.primary'], ['/v1/chat/completions'])
    first = tmp_path / 'before.sqlite3'
    result = keys._migrate_offline(str(config), str(first), journal_mode='WAL')
    assert result['journal_mode'] == 'wal' and result['offline'] is True
    store = keys.KeyStore(store.path)
    assert store.authenticate(token).key_id == metadata['key_id']
    newer, newer_token = store.create('latest committed', ['llm.primary'], ['/v1/chat/completions'])
    for _ in range(3):
        with store._connect() as connection:
            assert connection.getconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE) is True
            assert connection.execute('PRAGMA journal_mode').fetchone() == ('wal',)
            assert connection.execute('PRAGMA synchronous').fetchone() == (2,)
            assert connection.execute('PRAGMA wal_autocheckpoint').fetchone() == (1000,)
    snapshot = tmp_path / 'latest.sqlite3'
    UsageStore(store).backup(snapshot)
    copied = keys.KeyStore(snapshot)
    assert copied.authenticate(newer_token).key_id == newer['key_id']
    with copied._connect() as connection:
        assert connection.execute('PRAGMA journal_mode').fetchone() == ('delete',)
    assert not any(Path(str(snapshot) + s).exists() for s in ('-wal', '-shm', '.sqlite-policy'))
    assert keys.KeyStore(first).authenticate(newer_token) is None
    before = snapshot.read_bytes()
    with pytest.raises(keys.KeyStoreError): UsageStore(store).backup(snapshot)
    assert snapshot.read_bytes() == before


@native
def test_actual_committed_child_crash_recovers_without_last_close_checkpoint(tmp_path):
    store, config = configured(tmp_path)
    keys._migrate_offline(str(config), str(tmp_path / 'before.sqlite3'), journal_mode='WAL')
    child = subprocess.run([sys.executable, '-c',
        'import os; from anvil_serving.router.keys import KeyStore; '
        f's=KeyStore({str(store.path)!r}); '
        's.create("committed child",["llm.primary"],["/v1/chat/completions"]); os._exit(0)'],
        timeout=10, capture_output=True)
    assert child.returncode == 0
    wal = Path(str(store.path) + '-wal')
    assert wal.stat().st_size > 32
    assert keys.KeyStore(store.path).list_keys()[0]['name'] == 'committed child'
    snapshot = tmp_path / 'recovered.sqlite3'
    UsageStore(keys.KeyStore(store.path)).backup(snapshot)
    assert keys.KeyStore(snapshot).list_keys()[0]['name'] == 'committed child'


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


@pytest.mark.skipif(os.name != 'posix', reason='precise ENOENT lstat leaf race')
def test_transient_leaf_disappears_at_native_ancestor_guard_but_parent_error_refuses(tmp_path, monkeypatch):
    store, _ = configured(tmp_path)
    sidecar = Path(str(store.path) + '-shm'); sidecar.write_bytes(b'owned transient'); sidecar.chmod(0o600)
    original = keys._safe_ancestors
    def disappear(path, **kwargs):
        if path == sidecar: sidecar.unlink()
        return original(path, **kwargs)
    monkeypatch.setattr(keys, '_safe_ancestors', disappear)
    keys._secure_sidecars(store.path)
    sidecar.write_bytes(b'owned transient'); sidecar.chmod(0o600)
    def parent_error(path, **kwargs):
        if path == sidecar:
            try: raise FileNotFoundError(2, 'synthetic parent absent', str(sidecar.parent))
            except FileNotFoundError as exc: raise keys.KeyStoreError('unavailable') from exc
        return original(path, **kwargs)
    monkeypatch.setattr(keys, '_safe_ancestors', parent_error)
    with pytest.raises(keys.KeyStoreError): keys._secure_sidecars(store.path)
    assert sidecar.exists()


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


@pytest.mark.skipif(os.name != 'posix', reason='held POSIX verifier descriptors')
def test_per_open_proof_rechecks_new_sidecar_after_setup(tmp_path, monkeypatch):
    store, _ = configured(tmp_path)
    foreign = tmp_path / 'foreign'; foreign.write_bytes(b'owned foreign'); foreign.chmod(0o600)
    sidecar = Path(str(store.path) + '-shm')
    original = keys._WriterConnection.execute
    def insert(self, sql, *args):
        result = original(self, sql, *args)
        if sql == 'PRAGMA user_version': sidecar.symlink_to(foreign)
        return result
    monkeypatch.setattr(keys._WriterConnection, 'execute', insert)
    with pytest.raises(keys.KeyStoreError):
        with store._connect():
            pytest.fail('post-setup sidecar checks must remain active')
    assert sidecar.is_symlink() and foreign.read_bytes() == b'owned foreign'


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


@pytest.mark.skipif(os.name != 'posix', reason='native dir_fd no-follow leaf race')
@pytest.mark.parametrize('replacement', [False, True])
def test_anchored_sidecar_open_tolerates_only_actual_leaf_disappearance(tmp_path, monkeypatch, replacement):
    store, _ = configured(tmp_path)
    sidecar = Path(str(store.path) + '-shm'); sidecar.write_bytes(b'owned leaf'); sidecar.chmod(0o600)
    foreign = tmp_path / 'foreign'; foreign.write_bytes(b'foreign'); foreign.chmod(0o600)
    original = os.open
    opened = []
    def race(path, flags, *args, **kwargs):
        if path == sidecar.name and kwargs.get('dir_fd') is not None and not opened:
            opened.append(True); sidecar.unlink()
            if replacement: sidecar.symlink_to(foreign)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', race)
    if replacement:
        with pytest.raises(keys.KeyStoreError):
            with store._connect(): pass
        assert sidecar.is_symlink()
    else:
        with store._connect() as connection:
            assert connection.execute('SELECT count(*) FROM keys').fetchone() == (0,)
        assert not sidecar.exists()
    assert opened == [True] and foreign.read_bytes() == b'foreign'


@native
def test_default_checkpoint_threshold_and_long_reader_preserve_latest_rows(tmp_path):
    store, config = configured(tmp_path)
    keys._migrate_offline(str(config), str(tmp_path / 'before.sqlite3'), journal_mode='WAL')
    with store._write() as connection:
        connection.execute('CREATE TABLE synthetic_threshold(id INTEGER PRIMARY KEY, payload BLOB)')
    with store._connect() as reader:
        reader.execute('BEGIN')
        assert reader.execute('SELECT count(*) FROM synthetic_threshold').fetchone() == (0,)
        for i in range(1030):
            with store._write() as writer:
                writer.execute('INSERT INTO synthetic_threshold VALUES(?,?)', (i, b'x' * 4096))
        with store._write() as writer:
            busy, log, copied = writer.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
            assert busy == 0 and log > 1000 and copied < log
        reader.execute('COMMIT')
    with store._write() as writer:
        busy, log, copied = writer.execute('PRAGMA wal_checkpoint(FULL)').fetchone()
        assert busy == 0 and copied == log
        assert writer.execute('SELECT count(*) FROM synthetic_threshold').fetchone() == (1030,)
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


@native
def test_runtime_policy_and_capacity_refusal_preserve_latest_backup(tmp_path, monkeypatch):
    store, config = configured(tmp_path)
    keys._migrate_offline(str(config), str(tmp_path / 'before.sqlite3'), journal_mode='WAL')
    metadata, token = store.create('newest after conversion', ['llm.primary'], ['/v1/chat/completions'])
    assert Path(str(store.path) + '-wal').stat().st_size > 32
    monkeypatch.setattr(keys, '_MAX_WAL_BYTES', 32)
    with pytest.raises(keys.KeyStoreError): store.create('refused overflow', ['llm.primary'], ['/v1/chat/completions'])
    newest = tmp_path / 'newest.sqlite3'
    UsageStore(store).backup(newest)
    assert keys.KeyStore(newest).authenticate(token).key_id == metadata['key_id']
    # Explicit fenced maintenance remains possible while new writers refuse.
    result = keys._migrate_offline(str(config), str(tmp_path / 'maintenance.sqlite3'), journal_mode='WAL')
    assert result['checkpoint_frames'] >= 0
    monkeypatch.setattr(sqlite3, 'sqlite_version_info', (3, 51, 2))
    def no_sql(*args, **kwargs): raise AssertionError('unsafe runtime must refuse before file SQL')
    monkeypatch.setattr(sqlite3, 'connect', no_sql)
    with pytest.raises(keys.KeyStoreError): keys.KeyStore(store.path)


@pytest.mark.skipif(os.name != 'posix', reason='actual POSIX shared gate custody')
def test_external_writer_borrows_one_gate_and_keeps_exclusion_through_sql_close(tmp_path, monkeypatch):
    import fcntl
    store, _ = configured(tmp_path)
    gate = Path(str(store.path) + '.router-writers.lock')
    original_open, original_flock = os.open, fcntl.flock
    original_close = keys._WriterConnection.close
    acquisition = []
    shared = []
    outer = []
    def opened(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if path == gate and flags & os.O_RDWR:
            acquisition.append(fd)
        return fd
    def locked(fd, operation):
        if operation == fcntl.LOCK_SH | fcntl.LOCK_NB:
            shared.append(fd)
        return original_flock(fd, operation)
    contender = original_open(gate, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    def excluded():
        with pytest.raises(BlockingIOError):
            original_flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def closing(connection):
        excluded()
        assert store._writer_context.external_reader_custody[0] == outer[0]
        result = original_close(connection)
        excluded()
        os.fstat(outer[0])  # SQLite cleanup cannot close the borrowed gate.
        return result
    monkeypatch.setattr(os, 'open', opened)
    monkeypatch.setattr(fcntl, 'flock', locked)
    monkeypatch.setattr(keys._WriterConnection, 'close', closing)
    try:
        with store._write() as db:
            outer.append(store._writer_context.external_reader_custody[0])
            excluded()
            db.execute('BEGIN IMMEDIATE')
            assert db.execute('SELECT count(*) FROM keys').fetchone() == (0,)
            db.execute('COMMIT')
        assert acquisition == outer and shared == outer
        assert not hasattr(store._writer_context, 'external_reader_custody')
        with pytest.raises(OSError): os.fstat(outer[0])
        original_flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(contender)


@pytest.mark.skipif(os.name != 'posix', reason='actual postqueue POSIX custody tampering')
@pytest.mark.parametrize('changed', ['gate', 'missing', 'link', 'permissions', 'parent', 'ancestor', 'marker', 'store'])
def test_borrowed_postqueue_tamper_refuses_before_sqlite_open(tmp_path, monkeypatch, changed):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    root = tmp_path / 'ancestor'; root.mkdir(mode=0o700)
    store, _ = configured(root)
    state = root / 'admission.json'
    state.write_text(json.dumps({'schema':'router-admission/v1', 'owner_id':'synthetic-owner', 'closure':None}))
    state.chmod(0o600)
    keys._bind_router_store(store.path, str(state), 'synthetic-owner')
    gate = Path(str(store.path) + '.router-writers.lock')
    marker = Path(str(store.path) + '.router-owner')
    queued = Event()
    wait = store._writer_condition.wait
    def waiting(*args, **kwargs):
        queued.set()
        return wait(*args, **kwargs)
    monkeypatch.setattr(store._writer_condition, 'wait', waiting)
    def no_sql(*args, **kwargs):
        pytest.fail('postqueue borrowed custody must refuse before SQLite connect')
    monkeypatch.setattr(sqlite3, 'connect', no_sql)
    ticket = object()
    with store._writer_condition: store._writer_queue.append(ticket)
    def writing():
        try:
            with pytest.raises(keys.KeyStoreError):
                with store._write(): pytest.fail('tampered writer must refuse')
        finally:
            assert not hasattr(store._writer_context, 'external_reader_custody')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writing)
        try:
            assert queued.wait(2)
            if changed == 'permissions': gate.chmod(0o644)
            elif changed in ('parent', 'ancestor'):
                target = store.path.parent if changed == 'parent' else root
                displaced = tmp_path / 'displaced'
                target.rename(displaced); target.mkdir(mode=0o700)
                if changed == 'parent':
                    for leaf in displaced.iterdir(): leaf.rename(target / leaf.name)
                else:
                    for leaf in displaced.iterdir(): leaf.rename(root / leaf.name)
            elif changed == 'marker':
                binding = json.loads(marker.read_text()); binding['owner_id'] = 'changed-owner'
                binding['gate_identity'] = [0, 0]
                marker.write_text(json.dumps(binding))
            elif changed == 'store':
                before = store.path.read_bytes(); store.path.rename(root / 'original.sqlite3')
                store.path.write_bytes(before); store.path.chmod(0o600)
            else:
                gate.unlink()
                if changed == 'gate': gate.write_bytes(b''); gate.chmod(0o600)
                elif changed == 'link': gate.symlink_to(state)
        finally:
            with store._writer_condition:
                store._writer_queue.remove(ticket); store._writer_condition.notify_all()
        future.result(timeout=3)
    assert not store._writer_queue


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


@pytest.mark.skipif(os.name != 'posix', reason='borrowed POSIX exit identity')
def test_borrowed_gate_exit_replacement_refuses_and_outer_cleans_up(tmp_path):
    store, _ = configured(tmp_path)
    gate = Path(str(store.path) + '.router-writers.lock')
    with pytest.raises(keys.KeyStoreError):
        with store._write() as db:
            held = store._writer_context.external_reader_custody[0]
            db.execute('BEGIN IMMEDIATE'); db.execute('COMMIT')
            gate.unlink(); gate.write_bytes(b''); gate.chmod(0o600)
    assert not hasattr(store._writer_context, 'external_reader_custody') and not store._writer_queue
    with pytest.raises(OSError): os.fstat(held)
