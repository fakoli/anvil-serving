"""Real process lock checks on synthetic private databases only."""
import os
import subprocess
import sys

import pytest

from anvil_serving.router import keys
from tests.router.key_fixtures import tmp_path as tmp_path


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
