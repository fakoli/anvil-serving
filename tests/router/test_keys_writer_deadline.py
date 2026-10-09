"""Real SQLite writer frontiers; no serving or account databases."""
from contextlib import closing
import os
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


def test_inside_transaction_busy_never_replays_body(tmp_path, monkeypatch):
    key = store(tmp_path)
    with key._write() as db, key._connect() as other:
        db.execute("BEGIN")
        assert db.execute("SELECT value FROM writer_fixture").fetchone() == (1,)
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE writer_fixture SET value=2")
        refuse_sleep(monkeypatch)
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute("UPDATE writer_fixture SET value=3")
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY and db.in_transaction
        db.execute("ROLLBACK")
        other.execute("ROLLBACK")
    with key._connect() as db:
        assert db.execute("SELECT value FROM writer_fixture").fetchone() == (1,)


def test_extended_busy_snapshot_propagates_without_body_replay(tmp_path, monkeypatch):
    # Sequential integer-only WAL fixture: no concurrent writer/checkpoint or
    # close/reset. Exercise the actual shared native wrapper/extended error;
    # no unapproved KeyStore mode change or WAL runtime qualification is implied.
    path=tmp_path/'snapshot.sqlite3'
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_RDWR,0o600);os.close(descriptor)
    with closing(sqlite3.connect(path,isolation_level=None,factory=keys._WriterConnection)) as db, \
            closing(sqlite3.connect(path,isolation_level=None)) as other:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        db.execute("CREATE TABLE writer_fixture(value INTEGER)")
        db.execute("INSERT INTO writer_fixture VALUES(1)")
        db.writer_deadline=time.monotonic()+1
        db.execute("BEGIN")
        assert db.execute("SELECT value FROM writer_fixture").fetchone() == (1,)
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE writer_fixture SET value=2")
        other.execute("COMMIT")
        refuse_sleep(monkeypatch)
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute("UPDATE writer_fixture SET value=3")
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY_SNAPSHOT and db.in_transaction
        db.execute("ROLLBACK")
        assert db.execute("SELECT value FROM writer_fixture").fetchone() == (2,)


def test_non_busy_native_error_propagates_without_retry(tmp_path, monkeypatch):
    key = store(tmp_path)
    with key._write() as db:
        refuse_sleep(monkeypatch)
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute("SELECT FROM writer_fixture")
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_ERROR
        assert not db.in_transaction


def test_post_sleep_expiry_does_not_retry_a_freed_writer(tmp_path, monkeypatch):
    key = store(tmp_path)
    with key._write() as db, key._connect() as other:
        other.execute("BEGIN EXCLUSIVE")
        sleeps = []
        original_sleep = time.sleep
        def release_after_expiry(delay):
            sleeps.append(delay)
            original_sleep(max(delay, db.writer_deadline - time.monotonic()) + .03)
            other.execute("ROLLBACK")
        monkeypatch.setattr(keys.time, "sleep", release_after_expiry)
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute("BEGIN IMMEDIATE")
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_BUSY
        assert len(sleeps) == 1 and 0 < sleeps[0] <= .01
        assert not db.in_transaction and not other.in_transaction


def test_read_connection_keeps_native_default_timeout(tmp_path):
    key = store(tmp_path)
    with key._connect() as db:
        assert db.writer_deadline is None
        assert db.execute("PRAGMA busy_timeout").fetchone() == (1000,)
        assert db.execute("PRAGMA synchronous").fetchone() == (2,)
