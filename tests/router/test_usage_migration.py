"""Synthetic protected-store migration, authority and loss-safe snapshot checks."""
from contextlib import contextmanager
import os
import sqlite3
import time

import pytest

from anvil_serving.router import connect_keys, keys, usage_store
from anvil_serving.router.keys import KeyStore, KeyStoreError
from anvil_serving.router.usage_store import UsageStore
from tests.router.key_fixtures import tmp_path as tmp_path
from tests.router.test_connect_keys import approve, issue, OWNER, FENCE, CHAT


def store_at(tmp_path):
    return KeyStore.initialize(tmp_path / "private" / "keys.sqlite3")


def rows(store):
    with store._connect() as db:
        names = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: sorted(db.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)
                for name in names}


def group(db, table, **counters):
    values = {
        "domain_id": "router-domain", "group_key": "synthetic-group",
        "actor_kind": "human", "actor_id": "human:synthetic", "binding_revision": 1,
        "grant_kind": "key_policy", "grant_reference": "key_fixture",
        "credential_id": "key_fixture", "model": "llm.primary", "outcome": "success",
        "input_applicability": "applicable", "output_applicability": "applicable",
        "input_source": "measured", "output_source": "unknown",
        "input_partial": 0, "output_partial": 1, "last_activity_at": "2026-01-01T00:00:01Z",
        **counters,
    }
    if table == "usage_daily":
        values["accepted_day"] = "2026-01-01"
    db.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
               tuple(values.values()))


def ledger(store):
    with store._connect() as db:
        db.execute("INSERT INTO key_owner_bindings VALUES ('key_fixture','human','human:synthetic',1)")
        db.execute("INSERT INTO usage_domains VALUES ('router-domain','2026-01-01T00:00:00Z','config-1',1,0)")
        db.execute("INSERT INTO usage_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "run-1", "router-domain", "2026-01-01T00:00:00Z", None, "live",
            "synthetic-host-domain", "synthetic-boot", 1, 2, 1, 2, 1000, 1, 3, 123, 456))
        db.execute("INSERT INTO usage_coverage_segments VALUES (?,?,?,?,?,?,?,?,?)", (
            "segment-1", "router-domain", "run-1", "config-1", 1,
            "2026-01-01T00:00:00Z", None, None, 0))
        db.execute("INSERT INTO usage_starts (request_id,run_id,domain_id,segment_id,configuration_revision,accepted_at,caller,kind,model,parent_request_id,attempt_id,usage_relation,start_payload,dispatched,route_association) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "request-1", "run-1", "router-domain", "segment-1", "config-1",
            "2026-01-01T00:00:00Z", '{"schema":"router-usage/v1"}', "chat", "llm.primary",
            None, "attempt-1", "exclusive", '{"request_id":"request-1"}', 1, None))
        db.execute("INSERT INTO usage_details VALUES (?,?,?,?)", (
            "request-1", "2026-01-01T00:00:01Z", "success", '{"request_id":"request-1"}'))
        for table in ("usage_daily", "usage_cumulative"):
            group(db, table, requests=1, attempts=1, measured_input=7,
                  unknown_output_requests=1, partial_output_requests=1,
                  latency_sum_ms=1000, latency_count=1, latency_le_1000_ms=1, latency_le_inf=1)


@pytest.mark.parametrize("version", [1, 2])
def test_atomic_migration_preserves_legacy_authority_expiry_revocation_and_audit(tmp_path, version):
    store = store_at(tmp_path)
    valid, token = store.create("device", ["llm.primary"], [CHAT], rpm=2)
    expired, expired_token = store.create("expired", ["llm.primary"], [CHAT], expires_days=1)
    revoked, revoked_token = store.create("revoked", ["llm.primary"], [CHAT])
    store.revoke(revoked["key_id"])
    with store._connect() as db:
        db.execute("UPDATE keys SET expires_at=0 WHERE key_id=?", (expired["key_id"],))
    store.record(valid["key_id"], "synthetic-request", "POST", CHAT, 200, 7)
    if version == 2:
        store.owner_check = lambda *actor: True
        portal = connect_keys.ConnectKeys(store, ["llm.primary"])
        approve(portal, rpm=1)
        owned = issue(portal)
    before = rows(store)
    usage = UsageStore(store)
    assert usage.migrate() == {"schema_version": 3, "migrated": True}
    after = rows(store)
    assert all(after[table] == values for table, values in before.items())
    assert store.authenticate(token).key_id == valid["key_id"]
    assert store.authenticate(expired_token) is None
    assert store.authenticate(revoked_token) is None
    assert store.admit(valid["key_id"]) == 0
    assert store.admit(valid["key_id"]) == 0
    assert store.admit(valid["key_id"]) > 0
    if version == 2:
        assert store.authenticate(owned["secret"]).owner == (OWNER, "1", FENCE)
        assert store.admit(owned["key"]["key_id"]) == 0
        assert store.admit(owned["key"]["key_id"]) > 0
        assert KeyStore(store.path).authenticate(owned["secret"]) is None
        connect_keys.ConnectKeys(store, ["llm.primary"])
        assert store.version == 3
        approve(portal)
        assert store.authenticate(owned["secret"]) is None
    assert usage.migrate() == {"schema_version": 3, "migrated": False}
    with store._connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


@pytest.mark.parametrize("version", [1, 2])
def test_failed_ddl_rolls_back_entire_migration_and_version(tmp_path, monkeypatch, version):
    store = store_at(tmp_path)
    if version == 2:
        connect_keys.migrate(store)
    store.create("device", ["llm.primary"], [CHAT])
    before = rows(store)
    monkeypatch.setattr(usage_store, "_DDL", (*usage_store._DDL[:4], ("fail", "INVALID SQL")))
    with pytest.raises(KeyStoreError):
        UsageStore(store).migrate()
    assert rows(store) == before
    with store._connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == version
    assert store.version == version


def test_schema_three_marker_with_missing_table_refuses(tmp_path):
    store = store_at(tmp_path)
    UsageStore(store).migrate()
    with store._connect() as db:
        db.execute("DROP TABLE usage_details")
    with pytest.raises(KeyStoreError, match="unsupported"):
        UsageStore(store).migrate()


def test_unknown_format_refuses_fresh_and_previously_open_store(tmp_path):
    store = store_at(tmp_path)
    _, token = store.create("device", ["llm.primary"], [CHAT])
    with store._connect() as db:
        db.execute("PRAGMA user_version=4")
    for operation in (lambda: KeyStore(store.path), lambda: store.authenticate(token),
                      store.list_keys, lambda: UsageStore(store).migrate()):
        with pytest.raises(KeyStoreError, match="unsupported"):
            operation()


def test_new_schema_constraints_and_history_have_no_key_account_cascade(tmp_path):
    store = store_at(tmp_path)
    UsageStore(store).migrate()
    ledger(store)
    before = rows(store)
    with store._connect() as db:
        for table, identifier in (("key_owner_bindings", "key_id"), ("usage_domains", "domain_id"),
                                  ("usage_runs", "run_id"), ("usage_coverage_segments", "segment_id"),
                                  ("usage_starts", "request_id"), ("usage_details", "request_id")):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(f"UPDATE {table} SET {identifier}=NULL")
        for kind, owner, revision in (("admin", "owner-1", 1), ("human", "https://example.test", 1),
                                      ("service", "owner-1", 0), ("human", "owner-1", 2**53)):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("INSERT INTO key_owner_bindings VALUES ('key_bad',?,?,?)", (kind, owner, revision))
        for value in (-1, 1.5, "unknown"):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE usage_cumulative SET measured_input=?", (value,))
        db.execute("DELETE FROM keys")
        db.execute("DELETE FROM connect_accounts")
    after = rows(store)
    for name in before:
        if name.startswith("usage_") or name == "key_owner_bindings":
            assert after[name] == before[name]


@pytest.mark.parametrize("version", [1, 2, 3])
def test_consistent_protected_backup_and_absent_target_restore(tmp_path, version):
    store = store_at(tmp_path)
    _, token = store.create("device", ["llm.primary"], [CHAT])
    if version >= 2:
        connect_keys.migrate(store)
    if version == 3:
        UsageStore(store).migrate()
        ledger(store)
    snapshot = tmp_path / "backup" / "snapshot.sqlite3"
    assert UsageStore(store).backup(snapshot) == {"schema_version": version, "copied": True}
    restored = tmp_path / "restore" / "restored.sqlite3"
    assert UsageStore.restore(snapshot, restored) == {"schema_version": version, "restored": True}
    assert rows(KeyStore(snapshot)) == rows(store) == rows(KeyStore(restored))
    assert KeyStore(restored).authenticate(token).key_id == store.authenticate(token).key_id
    assert token.encode() not in snapshot.read_bytes()
    if os.name != "nt":
        for path in (snapshot, restored):
            assert path.stat().st_mode & 0o077 == path.parent.stat().st_mode & 0o077 == 0


def test_backup_excludes_uncommitted_cross_table_writes(tmp_path):
    store = store_at(tmp_path)
    UsageStore(store).migrate()
    ledger(store)
    before = rows(store)
    with store._connect() as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE usage_cumulative SET measured_input=100")
        writer.execute("UPDATE usage_daily SET measured_input=100")
        target = tmp_path / "backup" / "snapshot.sqlite3"
        UsageStore(store).backup(target)
        writer.execute("ROLLBACK")
    assert rows(KeyStore(target)) == before


def test_old_snapshot_cannot_overwrite_new_ledger_or_revocation(tmp_path):
    store = store_at(tmp_path)
    meta, token = store.create("device", ["llm.primary"], [CHAT])
    UsageStore(store).migrate()
    ledger(store)
    snapshot = tmp_path / "backup" / "snapshot.sqlite3"
    UsageStore(store).backup(snapshot)
    store.revoke(meta["key_id"])
    with store._connect() as db:
        db.execute("UPDATE usage_cumulative SET requests=2,measured_input=12")
    before = store.path.read_bytes()
    with pytest.raises(KeyStoreError):
        UsageStore.restore(snapshot, store.path)
    assert store.path.read_bytes() == before
    assert store.authenticate(token) is None
    with store._connect() as db:
        assert db.execute("SELECT requests,measured_input FROM usage_cumulative").fetchone() == (2, 12)


@pytest.mark.parametrize("mode", ["existing", "symlink", "hardlink", "public-parent"])
def test_backup_refuses_unsafe_or_existing_destination(tmp_path, mode):
    if os.name == "nt" and mode != "existing":
        pytest.skip("POSIX protection contract")
    store = store_at(tmp_path)
    target = tmp_path / "backup" / "snapshot.sqlite3"
    target.parent.mkdir(mode=0o700)
    if mode == "existing":
        target.write_bytes(b"preserved")
        target.chmod(0o600)
    elif mode == "symlink":
        target.symlink_to(store.path)
    elif mode == "hardlink":
        os.link(store.path, target)
    else:
        target.parent.chmod(0o755)
    source_before = store.path.read_bytes()
    with pytest.raises(KeyStoreError):
        UsageStore(store).backup(target)
    assert store.path.read_bytes() == source_before
    if mode == "existing":
        assert target.read_bytes() == b"preserved"


def test_backup_failure_removes_only_its_own_partial_target(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    UsageStore(store).migrate()
    original = usage_store._validate_schema
    calls = 0

    def failed_copy(db):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyStoreError("synthetic snapshot validation failure")
        original(db)

    monkeypatch.setattr(usage_store, "_validate_schema", failed_copy)
    target = tmp_path / "backup" / "snapshot.sqlite3"
    with pytest.raises(KeyStoreError):
        UsageStore(store).backup(target)
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX replacement of an open database")
def test_backup_failure_preserves_competing_replacement(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    UsageStore(store).migrate()
    original = usage_store._validate_schema
    target = tmp_path / "backup" / "snapshot.sqlite3"
    calls = 0

    def replace_target(db):
        nonlocal calls
        calls += 1
        if calls == 2:
            if target.exists():
                target.unlink()
            target.write_bytes(b"competing target")
            target.chmod(0o600)
            raise KeyStoreError("synthetic replacement failure")
        original(db)

    monkeypatch.setattr(usage_store, "_validate_schema", replace_target)
    with pytest.raises(KeyStoreError):
        UsageStore(store).backup(target)
    assert target.read_bytes() == b"competing target"


@pytest.mark.parametrize("mode", ["corrupt", "future"])
def test_restore_rejects_unusable_snapshot_without_creating_target(tmp_path, mode):
    source = store_at(tmp_path)
    if mode == "corrupt":
        source.path.write_bytes(b"not SQLite")
    else:
        with source._connect() as db:
            db.execute("PRAGMA user_version=4")
    target = tmp_path / "restore" / "restored.sqlite3"
    with pytest.raises(KeyStoreError):
        UsageStore.restore(source.path, target)
    assert not target.exists()


def test_migration_contention_is_bounded_and_preserves_version(tmp_path):
    store = store_at(tmp_path)
    with store._connect() as other:
        other.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        with pytest.raises(KeyStoreError):
            UsageStore(store).migrate()
        assert time.monotonic() - started < 2
        other.execute("ROLLBACK")
    assert store.version == 1


def test_native_backup_busy_retry_is_bounded_and_cleans_partial_copy(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    original = store._connect

    class BusySource:
        def __init__(self, db):
            self.db = db

        def execute(self, *args):
            return self.db.execute(*args)

        def backup(self, destination, **options):
            # Lock after the initial read checks so the native backup retry
            # callback, rather than connection setup, observes SQLITE_BUSY.
            lock = sqlite3.connect(store.path, isolation_level=None)
            try:
                lock.execute("BEGIN EXCLUSIVE")
                self.db.backup(destination, **options)
            finally:
                lock.execute("ROLLBACK")
                lock.close()

    @contextmanager
    def connection():
        with original() as db:
            yield BusySource(db)

    monkeypatch.setattr(store, "_connect", connection)
    target = tmp_path / "backup" / "snapshot.sqlite3"
    started = time.monotonic()
    with pytest.raises(KeyStoreError, match="busy"):
        UsageStore(store).backup(target)
    assert time.monotonic() - started < 2
    assert not target.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX replacement of an open created file")
@pytest.mark.parametrize("phase", ["descriptor", "path-check", "path-substitution", "sqlite"])
def test_initialization_failure_preserves_substituted_target(tmp_path, monkeypatch, phase):
    target = tmp_path / "private" / "keys.sqlite3"

    def replace():
        if target.exists():
            target.unlink()
        target.write_bytes(b"competing target")
        target.chmod(0o600)

    if phase == "descriptor":
        def descriptor_check(_descriptor):
            replace()
            raise KeyStoreError("synthetic descriptor validation failure")
        monkeypatch.setattr(keys, "_private_created_descriptor", descriptor_check)
    elif phase in {"path-check", "path-substitution"}:
        original = keys._secure_database

        def path_check(path, *, exists, **options):
            if exists:
                replace()
                if phase == "path-check":
                    raise KeyStoreError("synthetic path validation failure")
            return original(path, exists=exists, **options)
        monkeypatch.setattr(keys, "_secure_database", path_check)
    else:
        def connect(*_args, **_options):
            replace()
            raise sqlite3.OperationalError("synthetic SQLite initialization failure")
        monkeypatch.setattr(keys.sqlite3, "connect", connect)
    with pytest.raises(KeyStoreError):
        KeyStore.initialize(target)
    assert target.read_bytes() == b"competing target"
    assert target.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("operation", ["initialize", "backup", "restore"])
@pytest.mark.parametrize("phase", ["destination-open", "publication"])
def test_creation_race_preserves_newer_database_bytes_authority_and_ledger(
        tmp_path, monkeypatch, operation, phase):
    source = store_at(tmp_path)
    UsageStore(source).migrate()
    ledger(source)
    newer = KeyStore.initialize(tmp_path / "newer" / "keys.sqlite3")
    revoked, token = newer.create("newer-device", ["llm.primary"], [CHAT])
    newer.revoke(revoked["key_id"])
    newer.owner_check = lambda *actor: True
    portal = connect_keys.ConnectKeys(newer, ["llm.primary"])
    approve(portal)
    UsageStore(newer).migrate()
    ledger(newer)
    with newer._connect() as db:
        db.execute("UPDATE usage_cumulative SET requests=9,measured_input=99")
    expected_rows = rows(newer)
    expected_bytes = newer.path.read_bytes()
    target = tmp_path / "destination" / "keys.sqlite3"
    injected = False

    def install_newer():
        nonlocal injected
        if target.exists():
            target.unlink()
        target.write_bytes(expected_bytes)
        target.chmod(0o600)
        injected = True

    if phase == "publication":
        link = keys.os.link

        def publish(staged, destination, *args, **options):
            if destination == target and not injected:
                install_newer()
            return link(staged, destination, *args, **options)
        monkeypatch.setattr(keys.os, "link", publish)
    elif operation == "initialize":
        connect = keys.sqlite3.connect

        def open_destination(path, *args, **options):
            if not injected:
                install_newer()
            return connect(path, *args, **options)
        monkeypatch.setattr(keys.sqlite3, "connect", open_destination)
    else:
        connect = KeyStore._connect
        initialize = KeyStore.initialize
        destination_ready = False

        def initialized_destination(cls, path):
            nonlocal destination_ready
            store = initialize(path)
            destination_ready = True
            return store

        @contextmanager
        def open_destination(store):
            # Trigger after the exclusive initializer returned, immediately
            # before the snapshot opens its destination connection for copying.
            if destination_ready and store.path != source.path and not injected:
                install_newer()
            with connect(store) as db:
                yield db
        monkeypatch.setattr(KeyStore, "initialize", classmethod(initialized_destination))
        monkeypatch.setattr(KeyStore, "_connect", open_destination)

    with pytest.raises(KeyStoreError):
        if operation == "initialize":
            KeyStore.initialize(target)
        elif operation == "backup":
            UsageStore(source).backup(target)
        else:
            UsageStore.restore(source.path, target)
    assert injected
    assert target.read_bytes() == expected_bytes
    preserved = KeyStore(target)
    assert rows(preserved) == expected_rows
    assert preserved.authenticate(token) is None
    assert target.stat().st_mode & 0o077 == 0
    assert list(target.parent.iterdir()) == [target]


def test_observation_checkpoint_is_nullable_bounded_text_and_preserved_in_snapshot(tmp_path):
    store = store_at(tmp_path)
    UsageStore(store).migrate()
    ledger(store)
    with store._connect() as db:
        assert db.execute("SELECT observation_payload FROM usage_starts").fetchone() == (None,)
        for invalid in ("x" * 4097, "é" * 2049, sqlite3.Binary(b"not text")):
            with pytest.raises(sqlite3.IntegrityError):
                db.execute("UPDATE usage_starts SET observation_payload=?", (invalid,))
        checkpoint = '{"schema":"router-observation/v1","sequence":1,"tokens":{}}'
        db.execute("UPDATE usage_starts SET observation_payload=?", (checkpoint,))
    target = tmp_path / "snapshot" / "keys.sqlite3"
    UsageStore(store).backup(target)
    with KeyStore(target)._connect() as db:
        assert db.execute("SELECT observation_payload FROM usage_starts").fetchone() == (checkpoint,)
