"""Protected accounting schema and loss-safe snapshots in the existing key store.

Migration is explicit. Lifecycle, typed payload writers and retained queries are
separate owners; schema creation neither enables accounting nor admits traffic.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .keys import (KeyStore, KeyStoreError, _publish_database, _secure_database,
                   _staged_database, _unlink_created)

_VERSION = 3

# Observed combinations only. The canonical group key retains null dimensions;
# columns support later bounded filters without joining mutable key/account rows.
_DIMENSIONS = """
    domain_id TEXT NOT NULL, group_key TEXT NOT NULL CHECK(length(group_key)<=8192),
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('human','service','unattributed')),
    actor_id TEXT, binding_revision INTEGER,
    end_user_instance TEXT, end_user_issuer TEXT, end_user_subject TEXT,
    grant_kind TEXT NOT NULL CHECK(grant_kind IN ('connect','key_policy','configured_scope','legacy')),
    grant_reference TEXT, grant_revision INTEGER, grant_policy_digest TEXT,
    grant_generation TEXT, grant_epoch TEXT,
    credential_id TEXT NOT NULL, model TEXT,
    outcome TEXT NOT NULL,
    input_applicability TEXT NOT NULL CHECK(input_applicability IN ('applicable','not_applicable')),
    output_applicability TEXT NOT NULL CHECK(output_applicability IN ('applicable','not_applicable')),
    input_source TEXT NOT NULL CHECK(input_source IN ('measured','estimated','unknown')),
    output_source TEXT NOT NULL CHECK(output_source IN ('measured','estimated','unknown')),
    input_partial INTEGER NOT NULL CHECK(input_partial IN (0,1)),
    output_partial INTEGER NOT NULL CHECK(output_partial IN (0,1))
"""
_COUNTERS = ",\n".join(
    f"{name} INTEGER NOT NULL DEFAULT 0 CHECK(typeof({name})='integer' AND {name}>=0)"
    for name in (
        "requests", "attempts", "measured_input", "measured_output",
        "estimated_input", "estimated_output", "unknown_input_requests",
        "unknown_output_requests", "partial_input_requests", "partial_output_requests",
        "not_applicable_input_requests", "not_applicable_output_requests",
        "cache_read_input", "cache_creation_input", "reasoning_output",
        "unknown_cache_read_requests", "unknown_cache_creation_requests",
        "unknown_reasoning_requests", "latency_sum_ms", "latency_count",
        "latency_le_100_ms", "latency_le_1000_ms", "latency_le_5000_ms",
        "latency_le_30000_ms", "latency_le_120000_ms", "latency_le_900000_ms",
        "latency_le_inf",
    )
)
_DDL = (
    ("key_owner_bindings", """CREATE TABLE key_owner_bindings (
        key_id TEXT NOT NULL PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('human','service')),
        owner_id TEXT NOT NULL CHECK(length(owner_id) BETWEEN 1 AND 128
            AND owner_id NOT GLOB '*[^A-Za-z0-9_:.\u002d]*'),
        revision INTEGER NOT NULL CHECK(typeof(revision)='integer'
            AND revision BETWEEN 1 AND 9007199254740991))"""),
    ("usage_domains", """CREATE TABLE usage_domains (
        domain_id TEXT NOT NULL PRIMARY KEY, coverage_epoch TEXT NOT NULL,
        configuration_revision TEXT NOT NULL,
        snapshot_revision INTEGER NOT NULL DEFAULT 0 CHECK(typeof(snapshot_revision)='integer' AND snapshot_revision>=0),
        accounting_failures INTEGER NOT NULL DEFAULT 0 CHECK(typeof(accounting_failures)='integer' AND accounting_failures>=0))"""),
    ("usage_runs", """CREATE TABLE usage_runs (
        run_id TEXT NOT NULL PRIMARY KEY, domain_id TEXT NOT NULL, started_at TEXT NOT NULL,
        ended_at TEXT, state TEXT NOT NULL CHECK(state IN ('live','dead','unknown')),
        host_domain_id TEXT NOT NULL, boot_id TEXT NOT NULL,
        pid_namespace_device INTEGER NOT NULL, pid_namespace_inode INTEGER NOT NULL,
        procfs_pid_namespace_device INTEGER NOT NULL, procfs_pid_namespace_inode INTEGER NOT NULL,
        uid INTEGER NOT NULL, user_namespace_device INTEGER NOT NULL,
        user_namespace_inode INTEGER NOT NULL, pid INTEGER NOT NULL,
        start_ticks INTEGER NOT NULL)"""),
    ("usage_coverage_segments", """CREATE TABLE usage_coverage_segments (
        segment_id TEXT NOT NULL PRIMARY KEY, domain_id TEXT NOT NULL, run_id TEXT NOT NULL,
        configuration_revision TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        started_at TEXT NOT NULL, ended_at TEXT, closure_reason TEXT,
        end_uncertain INTEGER NOT NULL DEFAULT 0 CHECK(end_uncertain IN (0,1)))"""),
    ("usage_starts", """CREATE TABLE usage_starts (
        request_id TEXT NOT NULL PRIMARY KEY, run_id TEXT NOT NULL, domain_id TEXT NOT NULL,
        segment_id TEXT NOT NULL, configuration_revision TEXT NOT NULL,
        accepted_at TEXT NOT NULL, caller TEXT NOT NULL CHECK(length(CAST(caller AS BLOB))<=16384),
        kind TEXT NOT NULL, model TEXT, parent_request_id TEXT, attempt_id TEXT,
        usage_relation TEXT NOT NULL CHECK(usage_relation IN ('exclusive','inclusive_parent','unobserved')),
        start_payload TEXT NOT NULL CHECK(length(CAST(start_payload AS BLOB))<=32768),
        dispatched INTEGER CHECK(dispatched IN (0,1)), route_association TEXT,
        observation_payload TEXT CHECK(observation_payload IS NULL OR
            (typeof(observation_payload)='text' AND length(CAST(observation_payload AS BLOB))<=4096)))"""),
    ("usage_details", """CREATE TABLE usage_details (
        request_id TEXT NOT NULL PRIMARY KEY, ended_at TEXT NOT NULL, outcome TEXT NOT NULL,
        terminal_payload TEXT NOT NULL CHECK(length(CAST(terminal_payload AS BLOB))<=32768))"""),
    ("usage_daily", f"""CREATE TABLE usage_daily (
        accepted_day TEXT NOT NULL, {_DIMENSIONS}, {_COUNTERS},
        last_activity_at TEXT NOT NULL, PRIMARY KEY(domain_id,accepted_day,group_key))"""),
    ("usage_cumulative", f"""CREATE TABLE usage_cumulative (
        {_DIMENSIONS}, {_COUNTERS}, last_activity_at TEXT NOT NULL,
        PRIMARY KEY(domain_id,group_key))"""),
    ("usage_starts_time", "CREATE INDEX usage_starts_time ON usage_starts(domain_id,accepted_at,request_id)"),
    ("usage_starts_run", "CREATE INDEX usage_starts_run ON usage_starts(run_id,request_id)"),
    ("usage_coverage_time", "CREATE INDEX usage_coverage_time ON usage_coverage_segments(domain_id,started_at,segment_id)"),
)


def _validate_schema(db: sqlite3.Connection) -> None:
    """A version marker alone cannot turn an incomplete migration into success."""
    for name, expected in _DDL:
        row = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if row is None or " ".join(row[0].split()) != " ".join(expected.split()):
            raise KeyStoreError("accounting store format is unsupported")


class UsageStore:
    """Accounting uses KeyStore's existing private per-operation connection."""

    def __init__(self, key_store: KeyStore) -> None:
        if not isinstance(key_store, KeyStore):
            raise TypeError("accounting requires a protected key store")
        self.key_store = key_store

    def migrate(self) -> dict:
        """Add schema atomically; preserve all credential, Connect and audit rows."""
        try:
            with self.key_store._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version == _VERSION:
                    _validate_schema(db)
                else:
                    if version == 1:
                        from .connect_keys import _create_schema
                        _create_schema(db)
                    for _name, statement in _DDL:
                        db.execute(statement)
                    _validate_schema(db)
                    db.execute(f"PRAGMA user_version={_VERSION}")
                db.execute("COMMIT")
            self.key_store.version = _VERSION
            return {"schema_version": _VERSION, "migrated": version != _VERSION}
        except sqlite3.Error:
            raise KeyStoreError("accounting migration is unavailable") from None

    def backup(self, target: str | os.PathLike[str]) -> dict:
        """Copy a committed snapshot to an exclusive protected absent target.

        This contains protected authority state as well as accounting metadata;
        callers must keep the snapshot private. Never replace an existing file.
        """
        target = Path(target).expanduser().absolute()
        try:
            with self.key_store._connect() as source, _staged_database(target) as staged:
                destination = None
                try:
                    version = source.execute("PRAGMA user_version").fetchone()[0]
                    if version == _VERSION:
                        _validate_schema(source)
                    destination = KeyStore.initialize(staged)
                    created = destination._created_identity
                    busy_deadline = time.monotonic() + 1.0
    
                    def progress(status, _remaining, _total):
                        nonlocal busy_deadline
                        _secure_database(destination.path, exists=True, identity=created)
                        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                            if time.monotonic() >= busy_deadline:
                                raise KeyStoreError("accounting snapshot is busy")
                        else:
                            busy_deadline = time.monotonic() + 1.0
    
                    with destination._connect() as copied:
                        source.backup(copied, pages=128, progress=progress, sleep=0.01)
                        copied_version = copied.execute("PRAGMA user_version").fetchone()[0]
                        if copied_version not in (1, 2, _VERSION):
                            raise KeyStoreError("accounting snapshot format is unsupported")
                        if copied_version == _VERSION:
                            _validate_schema(copied)
                        if copied.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                            raise KeyStoreError("accounting snapshot is invalid")
                    _publish_database(destination.path, target, created)
                finally:
                    if destination is not None:
                        _unlink_created(destination.path, destination._created_identity)
            return {"schema_version": copied_version, "copied": True}
        except (OSError, sqlite3.Error):
            raise KeyStoreError("accounting snapshot is unavailable") from None

    @classmethod
    def restore(cls, snapshot: str | os.PathLike[str], target: str | os.PathLike[str]) -> dict:
        """Restore only into an absent protected destination; never erase newer state.

        This does not install, activate or re-admit a router. Its management owner
        must separately establish compatible binaries/configuration and drain.
        """
        result = cls(KeyStore(snapshot)).backup(target)
        return {"schema_version": result["schema_version"], "restored": True}
