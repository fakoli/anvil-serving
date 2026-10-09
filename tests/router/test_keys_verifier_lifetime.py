"""Real process lock checks on synthetic private databases only."""
from concurrent.futures import ThreadPoolExecutor
import os
import subprocess
import sys

import pytest

from anvil_serving.router import keys
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_sqlite_wal import configured, native


def independent_writer_is_blocked(path, *, blocked=True):
    child = subprocess.run([sys.executable, '-c', '''
import sqlite3, sys
connection = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
try:
    connection.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError as error:
    assert error.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
    print('blocked')
else:
    connection.execute('ROLLBACK')
    print('acquired')
finally:
    connection.close()
''', str(path)], capture_output=True, text=True, timeout=5)
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == ('blocked' if blocked else 'acquired')


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-wide inode locks')
@pytest.mark.parametrize('guard', ['unretained', 'nested', 'other-instance', 'other-thread'])
def test_verifier_close_never_releases_another_connection_writer_lock(tmp_path, guard):
    store = keys.KeyStore.initialize(tmp_path / 'keys.sqlite3')
    with store._write() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if guard == 'unretained':
            keys._secure_database(store.path, exists=True)
        elif guard == 'nested':
            with store._connect() as reader:
                assert reader.execute('SELECT count(*) FROM keys').fetchone() == (0,)
        else:
            def inspect():
                return keys.KeyStore(store.path).list_keys()
            if guard == 'other-instance':
                assert inspect() == []
            else:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    assert pool.submit(inspect).result(timeout=5) == []
        independent_writer_is_blocked(store.path)
        connection.execute('ROLLBACK')
    assert not keys._verifier_fds and not keys._verifier_inodes
    independent_writer_is_blocked(store.path, blocked=False)


@native
def test_wal_sidecar_guard_preserves_real_shared_memory_writer_lock(tmp_path):
    store, config = configured(tmp_path)
    keys._migrate_offline(str(config), str(tmp_path / 'snapshot.sqlite3'), journal_mode='WAL')
    with store._write() as connection:
        connection.execute('BEGIN IMMEDIATE')
        keys._secure_sidecars(store.path)
        with store._connect() as reader:
            assert reader.execute('SELECT count(*) FROM keys').fetchone() == (0,)
        independent_writer_is_blocked(store.path)
        connection.execute('ROLLBACK')
    assert not keys._verifier_fds
    independent_writer_is_blocked(store.path, blocked=False)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX retained verifier resource bound')
def test_verifier_handles_are_bounded_and_revalidate_permissions(tmp_path, monkeypatch):
    store = keys.KeyStore.initialize(tmp_path / 'keys.sqlite3')
    with store._connect():
        count = len(keys._verifier_fds)
        for _ in range(20):
            keys._secure_database(store.path, exists=True)
        assert len(keys._verifier_fds) == count
        store.path.chmod(0o644)
        with pytest.raises(keys.KeyStoreError):
            keys._secure_database(store.path, exists=True)
        store.path.chmod(0o600)
        monkeypatch.setattr(keys, '_MAX_VERIFIER_FDS', count)
        extra = tmp_path / 'extra'; extra.write_bytes(b'synthetic'); extra.chmod(0o600)
        with pytest.raises(keys.KeyStoreError, match='capacity'):
            keys._secure_database(extra, exists=True)
        descriptors = tuple(keys._verifier_fds)
    assert not keys._verifier_fds
    for descriptor in descriptors:
        with pytest.raises(OSError): os.fstat(descriptor)


def test_transaction_authentication_uses_current_grants_without_nested_connection(tmp_path, monkeypatch):
    store = keys.KeyStore.initialize(tmp_path / 'keys.sqlite3')
    metadata, secret = store.create('synthetic', ['llm.primary'], ['/v1/chat/completions'])
    with store._write() as connection:
        connection.execute('BEGIN IMMEDIATE')
        monkeypatch.setattr(store, '_connect', lambda: pytest.fail('nested SQLite connection'))
        connection.execute('UPDATE keys SET models=? WHERE key_id=?', ('["llm.secondary"]', metadata['key_id']))
        assert store._authenticate_in_transaction(connection, secret).models == ('llm.secondary',)
        assert store._authenticate_in_transaction(connection, 'x' * 48) is None
        connection.execute('UPDATE keys SET expires_at=0 WHERE key_id=?', (metadata['key_id'],))
        assert store._authenticate_in_transaction(connection, secret) is None
        connection.execute('ROLLBACK')
        with pytest.raises(keys.KeyStoreError, match='authentication'):
            store._authenticate_in_transaction(connection, secret)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX verifier failed-open lifetime')
def test_unsafe_regular_verifier_is_retained_until_final_epoch(tmp_path):
    store = keys.KeyStore.initialize(tmp_path / 'keys.sqlite3')
    unsafe = tmp_path / 'unsafe'; unsafe.write_bytes(b'synthetic'); unsafe.chmod(0o644)
    with store._connect():
        before = set(keys._verifier_fds)
        with pytest.raises(keys.KeyStoreError):
            keys._secure_database(unsafe, exists=True)
        failed = keys._verifier_fds - before
        assert len(failed) == 1
        for descriptor in failed: os.fstat(descriptor)
    assert not keys._verifier_fds
    for descriptor in failed:
        with pytest.raises(OSError): os.fstat(descriptor)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX verifier close failure cleanup')
@pytest.mark.parametrize('primary', [False, True])
def test_retirement_attempts_all_closes_and_preserves_primary_error(tmp_path, monkeypatch, primary):
    first = tmp_path / 'first'; first.write_bytes(b'first'); first.chmod(0o600)
    second = tmp_path / 'second'; second.write_bytes(b'second'); second.chmod(0o600)
    closed = []
    original = os.close
    def close(fd):
        original(fd)
        closed.append(fd)
        if len(closed) == 1: raise OSError('synthetic close outcome unknown')
    try:
        with pytest.raises(RuntimeError if primary else keys.KeyStoreError, match='primary' if primary else 'cleanup'):
            with keys._verifier_epoch():
                keys._secure_database(first, exists=True)
                keys._secure_database(second, exists=True)
                monkeypatch.setattr(keys.os, 'close', close)
                if primary: raise RuntimeError('primary failure')
        assert len(closed) == 2 and not keys._verifier_fds and keys._verifier_unavailable
        with pytest.raises(keys.KeyStoreError):
            with keys._verifier_epoch(): pass
    finally:
        monkeypatch.setattr(keys.os, 'close', original)
        keys._verifier_unavailable = False


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='POSIX fork boundary')
def test_forked_live_epoch_refuses_inherited_store_without_parent_lock_loss(tmp_path):
    import signal
    import time
    import warnings
    from threading import Event, Thread
    store = keys.KeyStore.initialize(tmp_path / 'keys.sqlite3')
    with store._write() as connection:
        connection.execute('BEGIN IMMEDIATE')
        descriptors = tuple(keys._verifier_fds)
        held, release = Event(), Event()
        def hold_lock():
            with keys._verifier_lock:
                held.set()
                release.wait(5)
        thread = Thread(target=hold_lock)
        thread.start()
        assert held.wait(5)
        # Deliberately fork a threaded fixture only to verify refusal, never SQL
        # in the child; its inherited mutex must not deadlock that refusal.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', DeprecationWarning)
            child = os.fork()
        if child == 0:
            try:
                assert not keys._verifier_fds and keys._verifier_connections == 0
                assert keys._verifier_lock.acquire(blocking=False)
                keys._verifier_lock.release()
                for fd in descriptors:
                    try: os.fstat(fd)
                    except OSError: pass
                    else: raise AssertionError('inherited verifier retained')
                for operation in (store.list_keys, lambda: keys.KeyStore(store.path)):
                    try: operation()
                    except keys.KeyStoreError: pass
                    else: raise AssertionError('inherited SQLite lifetime accepted')
                os._exit(0)
            except BaseException:
                os._exit(1)
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        try:
            deadline = time.monotonic() + 5
            while True:
                pid, status = os.waitpid(child, os.WNOHANG)
                if pid:
                    assert os.waitstatus_to_exitcode(status) == 0
                    break
                if time.monotonic() >= deadline: pytest.fail('child verifier fork boundary blocked')
                time.sleep(.01)
        finally:
            if not pid:
                os.kill(child, signal.SIGKILL); os.waitpid(child, 0)
        independent_writer_is_blocked(store.path)
        connection.execute('ROLLBACK')
    independent_writer_is_blocked(store.path, blocked=False)
