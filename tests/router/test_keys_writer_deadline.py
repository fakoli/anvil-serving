"""Real SQLite writer frontiers; no serving or account databases."""
from contextlib import closing
import sqlite3
import time

import pytest

from anvil_serving.router import keys
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_usage_identity import wide_key


def store(tmp_path):
    key = keys.KeyStore.initialize(tmp_path / "private" / "keys.sqlite3")
    with key._write() as db:
        db.execute("CREATE TABLE writer_fixture(value INTEGER)")
        db.execute("INSERT INTO writer_fixture VALUES(1)")
    return key


def refuse_sleep(monkeypatch):
    def unexpected(_):
        pytest.fail("this native error must propagate without retry")
    monkeypatch.setattr(keys.time, "sleep", unexpected)


def expire(db):
    time.sleep(max(0, db.writer_deadline - time.monotonic()) + .03)
    assert time.monotonic() > db.writer_deadline


@pytest.mark.parametrize("terminal", ["COMMIT", "ROLLBACK"])
def test_expired_available_terminal_preserves_full_grants(tmp_path, terminal):
    key, metadata, credential, models = wide_key(tmp_path, False, True)
    with key._write() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE keys SET name=? WHERE key_id=?", ("changed", metadata["key_id"]))
        expire(db)
        db.execute(terminal)
        assert not db.in_transaction
    principal = key.authenticate(credential)
    assert principal is not None and principal.models == tuple(models)
    assert key.list_keys()[0]["name"] == ("changed" if terminal == "COMMIT" else metadata["name"])


def test_expired_busy_commit_preserves_transaction_for_rollback(tmp_path, monkeypatch):
    key = store(tmp_path)
    with key._write() as db, key._connect() as reader:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE writer_fixture SET value=2")
        reader.execute("BEGIN")
        assert reader.execute("SELECT value FROM writer_fixture").fetchone() == (1,)
        expire(db)
        refuse_sleep(monkeypatch)
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute("COMMIT")
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY and db.in_transaction
        db.execute("ROLLBACK")
        reader.execute("ROLLBACK")
    with key._connect() as db:
        assert db.execute("SELECT value FROM writer_fixture").fetchone() == (1,)


def test_read_connection_keeps_native_default_timeout(tmp_path):
    key = store(tmp_path)
    with key._connect() as db:
        assert db.writer_deadline is None
        assert db.execute("PRAGMA busy_timeout").fetchone() == (1000,)
        assert db.execute("PRAGMA synchronous").fetchone() == (2,)


def fault_connection(path, code, statement, *, errors=1, rollback=False):
    """Return native transactions with a controlled SQLite exception frontier."""
    class NativeFault(sqlite3.Connection):
        attempts = 0
        def execute(self, sql, parameters=(), /):
            if sql == statement:
                self.attempts += 1
                if self.attempts <= errors:
                    if rollback and self.in_transaction:
                        sqlite3.Connection.execute(self, 'ROLLBACK')
                    exc = sqlite3.OperationalError('synthetic native lock frontier')
                    exc.sqlite_errorcode = code
                    raise exc
            return super().execute(sql, parameters)
    class Controlled(keys._WriterConnection, NativeFault):
        pass
    return sqlite3.connect(path, isolation_level=None, factory=Controlled)


@pytest.mark.parametrize('code,active,terminal,rollback', [
    (sqlite3.SQLITE_BUSY_RECOVERY, True, False, False),
    (sqlite3.SQLITE_BUSY_RECOVERY, True, True, False),
    (sqlite3.SQLITE_BUSY_RECOVERY, True, False, True),
    (sqlite3.SQLITE_BUSY_SNAPSHOT, False, False, False),
    (sqlite3.SQLITE_BUSY_SNAPSHOT, True, False, False),
    (sqlite3.SQLITE_BUSY_SNAPSHOT, True, True, False),
    (773, False, False, False),
    (sqlite3.SQLITE_LOCKED, False, False, False),
    (sqlite3.SQLITE_IOERR, False, False, False),
    (sqlite3.SQLITE_BUSY, True, False, True),
])
def test_extended_frontiers_and_auto_rollback_never_replay(tmp_path, monkeypatch, code, active, terminal, rollback):
    statement = 'COMMIT' if terminal else 'INSERT INTO items VALUES(2)'
    with closing(fault_connection(tmp_path/'frontier.sqlite3', code, statement, rollback=rollback)) as db:
        db.execute('CREATE TABLE items(value INTEGER)')
        if active:
            db.execute('BEGIN IMMEDIATE')
        db.writer_deadline = time.monotonic()+1
        refuse_sleep(monkeypatch)
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute(statement)
        assert caught.value.sqlite_errorcode == code and db.attempts == 1
        if db.in_transaction:
            db.execute('ROLLBACK')
        assert db.execute('SELECT count(*) FROM items').fetchone() == (0,)


@pytest.mark.parametrize('code,pause', [(sqlite3.SQLITE_BUSY_RECOVERY, .001), (sqlite3.SQLITE_BUSY, .01)])
def test_recovery_busy_setup_retries_then_full_begin_and_body_once(tmp_path, monkeypatch, code, pause):
    statement = 'PRAGMA synchronous=FULL'
    sleeps = []
    original_sleep = time.sleep
    def actual_sleep(seconds):
        sleeps.append(seconds)
        original_sleep(seconds)
    monkeypatch.setattr(keys.time, 'sleep', actual_sleep)
    with closing(fault_connection(tmp_path/'recovery.sqlite3', code, statement)) as db:
        db.execute('CREATE TABLE items(value INTEGER)')
        db.writer_deadline = time.monotonic()+1
        db.execute(statement)
        assert sleeps == [pause]
        assert db.attempts == 2 and db.execute('PRAGMA synchronous').fetchone() == (2,)
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT INTO items VALUES(1)')
        db.execute('COMMIT')
        assert db.execute('SELECT value FROM items').fetchall() == [(1,)]


def test_repeated_recovery_busy_stays_in_original_deadline(tmp_path):
    statement = 'PRAGMA synchronous=FULL'
    with closing(fault_connection(tmp_path/'recovery-bound.sqlite3', sqlite3.SQLITE_BUSY_RECOVERY,
                                  statement, errors=1000)) as db:
        begin = time.monotonic()
        db.writer_deadline = begin+.06
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute(statement)
        elapsed = time.monotonic()-begin
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY_RECOVERY
        assert .055 <= elapsed < .35 and 1 < db.attempts < 100
        assert not db.in_transaction
