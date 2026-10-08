"""Protected native rollback journals, real recovery and writer cooperation."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import threading
import time

import pytest

from anvil_serving.router import keys, usage_store
from anvil_serving.router.keys import KeyStore, KeyStoreError
from anvil_serving.router.usage_store import UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_usage_lifecycle import store as store, start_at, terminal, rows


def journal(store):
    return Path(str(store.path) + '-journal')


def create(store, name='synthetic device'):
    return store.create(name, ['llm.primary'], ['/v1/chat/completions'])


def test_writer_mode_verified_and_journal_reused_without_changing_readers(tmp_path):
    store = KeyStore.initialize(tmp_path / 'keys.sqlite3')
    assert not journal(store).exists()
    metadata, token = create(store)
    first = journal(store).stat()
    if os.name == 'posix':
        assert first.st_mode & 0o077 == 0 and first.st_nlink == 1
    create(store, 'second device')
    assert os.path.samestat(first, journal(store).stat())
    with store._write() as db:
        assert db.execute('PRAGMA journal_mode').fetchone() == ('persist',)
        assert db.execute('PRAGMA synchronous').fetchone() == (3,)
    with KeyStore(store.path)._connect() as db:
        assert db.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        assert db.execute('PRAGMA synchronous').fetchone() == (2,)
    assert store.authenticate(token).key_id == metadata['key_id']
    snapshot = tmp_path / 'snapshot' / 'keys.sqlite3'
    UsageStore(store).backup(snapshot)
    assert not Path(str(snapshot) + '-journal').exists()
    with KeyStore(snapshot)._connect() as db:
        assert db.execute('PRAGMA journal_mode').fetchone() == ('delete',)
    assert journal(store).exists()


@pytest.mark.parametrize('failure', ['mode', 'sync', 'error'])
def test_writer_mode_failure_refuses_before_transaction_and_releases_ticket(tmp_path, monkeypatch, failure):
    store = KeyStore.initialize(tmp_path / 'keys.sqlite3')
    original = keys._WriterConnection.execute
    class Result:
        def fetchone(self):
            return ('delete',) if failure == 'mode' else (2,)
    def execute(self, sql, parameters=(), /):
        if sql == 'PRAGMA journal_mode=PERSIST':
            if failure == 'error':
                raise sqlite3.OperationalError('synthetic mode failure')
            if failure == 'mode':
                return Result()
        if sql == 'PRAGMA synchronous' and failure == 'sync':
            return Result()
        if sql.startswith('BEGIN'):
            raise AssertionError('unsafe mode admitted a transaction')
        return original(self, sql, parameters)
    with monkeypatch.context() as patch:
        patch.setattr(keys._WriterConnection, 'execute', execute)
        with pytest.raises(KeyStoreError, match='durability'):
            create(store)
    assert store.list_keys() == [] and not store._writer_queue
    create(store)


@pytest.mark.skipif(os.name != 'posix', reason='native POSIX file identities, modes and links')
@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'public', 'directory', 'fifo'])
@pytest.mark.parametrize('opener', ['construct', 'auth', 'backup', 'write'])
def test_unsafe_journal_refused_before_any_sqlite_opener(tmp_path, monkeypatch, kind, opener):
    store = KeyStore.initialize(tmp_path / 'keys.sqlite3')
    _, token = create(store)
    sidecar = journal(store)
    # This is our completed zero-header test journal, never a live/hot journal.
    sidecar.unlink()
    if kind == 'directory':
        sidecar.mkdir(mode=0o700)
    elif kind == 'fifo':
        os.mkfifo(sidecar, 0o600)
    else:
        target = tmp_path / 'synthetic-sidecar'
        target.write_bytes(b'not a journal')
        target.chmod(0o600)
        if kind == 'symlink':
            sidecar.symlink_to(target)
        elif kind == 'hardlink':
            os.link(target, sidecar)
        else:
            target.replace(sidecar)
            sidecar.chmod(0o644)
    def forbidden(*args, **kwargs):
        raise AssertionError('SQLite opened an unsafe recovery sidecar')
    monkeypatch.setattr(keys.sqlite3, 'connect', forbidden)
    with pytest.raises(KeyStoreError):
        if opener == 'construct':
            KeyStore(store.path)
        elif opener == 'auth':
            store.authenticate(token)
        elif opener == 'backup':
            UsageStore(store).backup(tmp_path / 'snapshot' / 'keys.sqlite3')
        else:
            create(store)


@pytest.mark.skipif(sys.platform != 'win32', reason='native Windows retained-journal DACL')
def test_windows_journal_public_dacl_refuses_before_sqlite(monkeypatch):
    from tests.bootstrap_windows_fixtures import windows_fixture_tree
    with windows_fixture_tree() as tree:
        store = KeyStore.initialize(tree.root / 'keys.sqlite3')
        _, token = create(store)
        sidecar = journal(store)
        tree.owner_readonly_with_everyone_write(sidecar)
        try:
            monkeypatch.setattr(keys.sqlite3, 'connect', lambda *a, **k: pytest.fail('unsafe SQLite open'))
            with pytest.raises(KeyStoreError):
                store.authenticate(token)
        finally:
            tree.restore_full_control(sidecar)


def test_orphan_journal_never_adopted_by_initialize_or_snapshot(tmp_path):
    target = tmp_path / 'keys.sqlite3'
    sidecar = Path(str(target) + '-journal')
    sidecar.write_bytes(b'synthetic orphan'); sidecar.chmod(0o600)
    with pytest.raises(KeyStoreError, match='journal already exists'):
        KeyStore.initialize(target)
    assert not target.exists() and sidecar.read_bytes() == b'synthetic orphan'


def test_snapshot_publication_refuses_competing_journal_without_erasing_it(tmp_path, monkeypatch):
    store = KeyStore.initialize(tmp_path / 'keys.sqlite3')
    create(store)
    target = tmp_path / 'snapshot' / 'keys.sqlite3'
    sidecar = Path(str(target) + '-journal')
    original = keys._publish_database
    def publish(staged, destination, identity):
        if destination == target:
            sidecar.write_bytes(b'synthetic competing orphan'); sidecar.chmod(0o600)
        return original(staged, destination, identity)
    monkeypatch.setattr(keys, '_publish_database', publish)
    monkeypatch.setattr(usage_store, '_publish_database', publish)
    with pytest.raises(KeyStoreError, match='snapshot journal'):
        UsageStore(store).backup(target)
    assert not target.exists() and sidecar.read_bytes() == b'synthetic competing orphan'
    assert len(store.list_keys()) == 1


@pytest.mark.skipif(os.name != 'posix', reason='native POSIX descriptor identity and ownership')
@pytest.mark.parametrize('change', ['replace', 'foreign'])
def test_journal_descriptor_change_refuses_before_sqlite(tmp_path, monkeypatch, change):
    store = KeyStore.initialize(tmp_path / 'keys.sqlite3')
    _, token = create(store)
    sidecar = journal(store)
    original_open, original_fstat = keys.os.open, keys.os.fstat
    selected = set()
    def opened(path, flags, *args, **kwargs):
        if Path(path) == sidecar and change == 'replace':
            replacement = tmp_path / 'replacement'
            replacement.write_bytes(b'synthetic replacement'); replacement.chmod(0o600)
            replacement.replace(sidecar)
        fd = original_open(path, flags, *args, **kwargs)
        if Path(path) == sidecar:
            selected.add(fd)
        return fd
    def details(fd):
        actual = original_fstat(fd)
        if fd in selected and change == 'foreign':
            values = list(actual); values[stat.ST_UID] = os.geteuid() + 1
            return os.stat_result(values)
        return actual
    monkeypatch.setattr(keys.os, 'open', opened)
    monkeypatch.setattr(keys.os, 'fstat', details)
    monkeypatch.setattr(keys.sqlite3, 'connect', lambda *a, **k: pytest.fail('unsafe SQLite open'))
    with pytest.raises(KeyStoreError):
        store.authenticate(token)


@pytest.mark.skipif(os.name != 'posix', reason='native POSIX write permission')
def test_readonly_journal_writer_refuses_without_losing_committed_key(tmp_path):
    store = KeyStore.initialize(tmp_path / 'keys.sqlite3')
    metadata, token = create(store)
    journal(store).chmod(0o400)
    try:
        with pytest.raises(KeyStoreError):
            create(store, 'refused')
        assert store.authenticate(token).key_id == metadata['key_id']
        assert len(store.list_keys()) == 1
    finally:
        journal(store).chmod(0o600)


def test_mode_setup_time_consumes_original_fifo_and_sqlite_budget(tmp_path, monkeypatch):
    store = KeyStore.initialize(tmp_path / 'keys.sqlite3')
    original = keys._WriterConnection.execute
    def execute(self, sql, parameters=(), /):
        if sql == 'PRAGMA journal_mode=PERSIST':
            time.sleep(.65)
        return original(self, sql, parameters)
    entered, release = threading.Event(), threading.Event()
    def prior():
        with store._write():
            entered.set(); assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        previous = pool.submit(prior)
        assert entered.wait(2)
        with monkeypatch.context() as patch:
            patch.setattr(keys._WriterConnection, 'execute', execute)
            started = time.monotonic()
            waiting = pool.submit(create, store)
            time.sleep(.45); release.set(); previous.result(timeout=2)
            with pytest.raises(KeyStoreError, match='wait expired'):
                waiting.result(timeout=2)
            assert 1 <= time.monotonic()-started < 1.6
    assert not store._writer_queue and store.list_keys() == []
    create(store)


@pytest.mark.parametrize('opener', ['construct', 'authenticate', 'backup'])
def test_real_interrupted_journal_recovers_prior_keys_and_committed_ledger(store, tmp_path, opener):
    usage, _, run, scope = store
    metadata, token = create(usage.key_store)
    completed = start_at(run); usage.start(completed, authority_scope=scope); usage.finalize(terminal(completed))
    incomplete = start_at(run); usage.start(incomplete, authority_scope=scope)
    script = """import sys
from anvil_serving.router.keys import KeyStore
store = KeyStore(sys.argv[1])
with store._write() as db:
    db.execute('PRAGMA cache_size=5')
    db.execute('BEGIN IMMEDIATE')
    db.execute("UPDATE keys SET name='uncommitted'")
    db.execute('DELETE FROM usage_details')
    db.execute('DELETE FROM usage_starts')
    for number in range(256):
        db.execute('INSERT INTO audit(recorded_at,request_id,method,path,status,elapsed_ms) VALUES(0,?,?,?,200,0)',
                   ('synthetic-spill-'+str(number)+'x'*1024,'GET','/v1/models'))
    print('uncommitted', flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, '-c', script, str(usage.key_store.path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'uncommitted'
        assert journal(usage.key_store).stat().st_size > 0
        child.kill(); child.communicate(timeout=5)
        if opener == 'construct':
            KeyStore(usage.key_store.path)
        elif opener == 'authenticate':
            assert usage.key_store.authenticate(token).key_id == metadata['key_id']
        else:
            snapshot = tmp_path / 'snapshot' / 'keys.sqlite3'
            UsageStore(usage.key_store).backup(snapshot)
            assert len(rows(UsageStore(KeyStore(snapshot)), 'usage_details')) == 1
        assert usage.key_store.list_keys()[0]['name'] == 'synthetic device'
        assert len(rows(usage, 'usage_details')) == 1
        assert {r['request_id'] for r in rows(usage, 'usage_starts')} == {completed.request_id, incomplete.request_id}
        assert rows(usage, 'audit') == []
        assert usage.key_store.authenticate(token).key_id == metadata['key_id']
        assert create(usage.key_store, 'after recovery')
    finally:
        if child.poll() is None:
            child.kill(); child.communicate(timeout=5)


def test_distinct_store_readers_and_process_writers_share_native_journal(tmp_path):
    first = KeyStore.initialize(tmp_path / 'keys.sqlite3')
    metadata, token = create(first)
    second = KeyStore(first.path)
    entered, release = threading.Event(), threading.Event()
    def writer():
        with first._write() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE keys SET name='committed writer'")
            entered.set(); assert release.wait(5)
            db.execute('COMMIT')
    script = """import sys
from anvil_serving.router.keys import KeyStore
store=KeyStore(sys.argv[1])
store.create('process writer',['llm.primary'],['/v1/chat/completions'])
print('committed',flush=True)
"""
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(writer)
        assert entered.wait(2)
        assert second.authenticate(token).key_id == metadata['key_id']
        child = subprocess.Popen([sys.executable, '-c', script, str(first.path)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            waiting = pool.submit(create, second, 'local writer')
            time.sleep(.1)
            assert not waiting.done()
            release.set(); active.result(timeout=2)
            assert waiting.result(timeout=2)
            stdout, stderr = child.communicate(timeout=5)
            assert child.returncode == 0, stderr
            assert stdout.strip() == 'committed'
            assert {row['name'] for row in KeyStore(first.path).list_keys()} == {
                'committed writer', 'local writer', 'process writer'}
        finally:
            release.set()
            if child.poll() is None:
                child.kill(); child.communicate(timeout=5)
