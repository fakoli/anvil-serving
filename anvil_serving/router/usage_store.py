"""Protected accounting schema and loss-safe snapshots in the existing key store.

Migration is explicit. Lifecycle, typed payload writers and retained queries are
separate owners; schema creation neither enables accounting nor admits traffic.
"""
from __future__ import annotations

import os
import json
import re
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .decision_log import TokenDirection, TokenUsage
from .identity import CallerSnapshot, opaque_id
from ..observability.dashboard.contracts import strict_json
from .keys import (KeyStore, KeyStoreError, _publish_database, _secure_database,
                   _staged_database, _unlink_created)

_VERSION = 3
_MAX_INT = 2**63 - 1


class UsageError(KeyStoreError):
    """Fixed accounting failure; never include a database/transport exception."""

    def __init__(self, code="accounting_invalid"):
        self.code = code
        self.status = 503 if code == "accounting_unavailable" else 409 if code in {
            "accounting_conflict", "accounting_start_missing", "accounting_configuration_unsupported",
        } else 400
        super().__init__(code)


def _require(ok):
    if not ok:
        raise UsageError()


def _utc(value):
    _require(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is not None)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        _require(parsed.utcoffset() == timedelta(0))
        return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise UsageError() from None


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _id(value):
    _require(type(value) is str)
    try:
        opaque_id(value)
    except KeyStoreError:
        raise UsageError() from None
    return value


def _uuid(value):
    _require(type(value) is str)
    try:
        _require(str(uuid.UUID(value)) == value)
    except ValueError:
        raise UsageError() from None
    return value


def _json(value, limit=32768):
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        _require(len(result.encode("utf-8")) <= limit)
        return result
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise UsageError() from None


def _closed(cls, value):
    _require(type(value) is dict and set(value) == {f.name for f in fields(cls)})
    return dict(value)


class _Payload:
    def to_json(self):
        return _json(self.to_dict())

    @classmethod
    def from_json(cls, raw):
        _require(type(raw) is str)
        try:
            _require(len(raw.encode("utf-8")) <= 32768)
            return cls.from_dict(strict_json(raw.encode("utf-8")))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise UsageError() from None


@dataclass(frozen=True, slots=True)
class RequestStart(_Payload):
    request_id: str
    run_id: str
    accepted_at: str
    caller: CallerSnapshot
    kind: str
    model: str | None = None
    parent_request_id: str | None = None
    attempt_id: str | None = None
    usage_relation: str = "exclusive"
    input_applicability: str = "applicable"
    output_applicability: str = "applicable"

    def __post_init__(self):
        _uuid(self.request_id); _uuid(self.run_id)
        object.__setattr__(self, "accepted_at", _utc(self.accepted_at))
        _require(type(self.caller) is CallerSnapshot)
        _require(type(self.kind) is str and self.kind in {"chat", "embedding", "rerank", "stt", "tts", "audio", "memory", "internal"})
        if self.model is not None:
            _require(type(self.model) is str and 1 <= len(self.model) <= 128 and "://" not in self.model
                     and not any(ord(c) < 32 or ord(c) == 127 for c in self.model))
        for value in (self.parent_request_id, self.attempt_id):
            if value is not None:
                _uuid(value)
        _require(self.parent_request_id != self.request_id and type(self.usage_relation) is str and self.usage_relation in {
            "exclusive", "inclusive_parent", "unobserved"})
        _require(type(self.input_applicability) is str and type(self.output_applicability) is str
                 and self.input_applicability in {"applicable", "not_applicable"}
                 and self.output_applicability in {"applicable", "not_applicable"})
        self.to_json()

    def to_dict(self):
        return {f.name: self.caller.to_dict() if f.name == "caller" else getattr(self, f.name)
                for f in fields(self)}

    @classmethod
    def from_dict(cls, value):
        data = _closed(cls, value)
        data["caller"] = CallerSnapshot.from_dict(data["caller"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class RouteAssociation(_Payload):
    route_id: str | None = None
    backend_id: str | None = None
    serve_id: str | None = None
    resource_owner_id: str | None = None
    member_id: str | None = None

    def __post_init__(self):
        for f in fields(self):
            if getattr(self, f.name) is not None:
                _id(getattr(self, f.name))

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, value):
        return cls(**_closed(cls, value))


_OUTCOMES = {"success", "error", "cancelled", "timeout", "rejected", "disconnected", "interrupted", "unknown"}
_ERROR_CODES = {"accounting_unavailable", "backend_error", "unavailable", "unknown_model",
                "request_cancelled", "request_timeout", "client_disconnected", "interrupted"}
_COVERAGE = {"dispatch_uncertain", "usage_incomplete", "usage_relation_unknown", "recovered_partial"}


@dataclass(frozen=True, slots=True)
class Terminal(_Payload):
    request_id: str
    ended_at: str
    dispatched: bool | None
    generation_outcome: str
    delivery_outcome: str
    outcome: str
    route: RouteAssociation
    tokens: TokenUsage
    latency_ms: int | None = None
    admission_wait_ms: int | None = None
    upstream_duration_ms: int | None = None
    time_to_first_content_ms: int | None = None
    error_code: str | None = None
    coverage: tuple[str, ...] = ()

    def __post_init__(self):
        _uuid(self.request_id)
        object.__setattr__(self, "ended_at", _utc(self.ended_at))
        _require(self.dispatched is None or type(self.dispatched) is bool)
        _require(type(self.generation_outcome) is str and type(self.outcome) is str and type(self.delivery_outcome) is str
                 and self.generation_outcome in _OUTCOMES and self.outcome in _OUTCOMES
                 and self.delivery_outcome in _OUTCOMES | {"not_applicable"})
        _require(type(self.route) is RouteAssociation and type(self.tokens) is TokenUsage)
        for name in ("latency_ms", "admission_wait_ms", "upstream_duration_ms", "time_to_first_content_ms"):
            value = getattr(self, name)
            _require(value is None or type(value) is int and 0 <= value <= 10**15)
        _require(self.error_code is None or type(self.error_code) is str and self.error_code in _ERROR_CODES)
        _require(type(self.coverage) is tuple and len(set(self.coverage)) == len(self.coverage)
                 and all(type(v) is str and v in _COVERAGE for v in self.coverage))
        _require(self.dispatched is not None or "dispatch_uncertain" in self.coverage)
        self.to_json()

    def to_dict(self):
        return {f.name: self.route.to_dict() if f.name == "route" else
                self.tokens.to_dict() if f.name == "tokens" else
                list(self.coverage) if f.name == "coverage" else getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, value):
        data = _closed(cls, value)
        data["route"] = RouteAssociation.from_dict(data["route"])
        data["tokens"] = TokenUsage.from_dict(data["tokens"])
        _require(type(data["coverage"]) is list)
        data["coverage"] = tuple(data["coverage"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class Observation(_Payload):
    sequence: int
    observed_at: str
    tokens: TokenUsage
    schema: str = "router-observation/v1"

    def __post_init__(self):
        _require(type(self.sequence) is int and 1 <= self.sequence < 2**53)
        object.__setattr__(self, "observed_at", _utc(self.observed_at))
        _require(self.schema == "router-observation/v1" and type(self.tokens) is TokenUsage)
        _json(self.to_dict(), 4096)

    def to_dict(self):
        return {"schema": self.schema, "sequence": self.sequence, "observed_at": self.observed_at,
                "tokens": self.tokens.to_dict()}

    @classmethod
    def from_dict(cls, value):
        data = _closed(cls, value)
        data["tokens"] = TokenUsage.from_dict(data["tokens"])
        return cls(**data)


@dataclass(frozen=True, slots=True, repr=False)
class RunOwner:
    """Private actual process instance; never an administrator response field."""

    host_domain_id: str
    boot_id: str
    pid_namespace_device: int
    pid_namespace_inode: int
    procfs_pid_namespace_device: int
    procfs_pid_namespace_inode: int
    uid: int
    user_namespace_device: int
    user_namespace_inode: int
    pid: int
    start_ticks: int

    def __post_init__(self):
        _id(self.host_domain_id); _uuid(self.boot_id)
        for f in fields(self)[2:]:
            value = getattr(self, f.name)
            _require(type(value) is int and 0 <= value <= _MAX_INT)
        _require(self.pid > 0 and self.start_ticks > 0)

    @classmethod
    def observe(cls, host_domain_id):
        return _linux_owner(host_domain_id)[0]


def _stat_ticks(raw, pid):
    tail = raw[raw.rfind(")") + 2:].split()
    _require(raw.startswith(f"{pid} (") and len(tail) >= 20 and tail[19].isdigit())
    return int(tail[19])


def _linux_owner(host_domain_id, target_pid=None):
    """Establish comparable unrestricted procfs before interpreting missing PIDs."""
    _require(sys.platform.startswith("linux"))
    pid = os.getpid()
    _require(os.readlink("/proc/self") == str(pid))
    raw_mounts = Path("/proc/self/mountinfo").read_text(encoding="ascii")
    mounts = []
    for line in raw_mounts.splitlines():
        left, right = line.split(" - ", 1)
        a, b = left.split(), right.split()
        mounts.append((a[4], a[3], b[0], a[5] + "," + b[2]))
    proc = [m for m in mounts if m[0] == "/proc"]
    _require(len(proc) == 1 and proc[0][1:3] == ("/", "proc"))
    options = proc[0][3].split(",")
    _require(not any(o.startswith("hidepid=") and o != "hidepid=0" or o == "subset=pid" for o in options))
    sensitive = (f"/proc/{pid}", f"/proc/{target_pid}" if target_pid else f"/proc/{pid}",
                 "/proc/1", "/proc/self", "/proc/sys/kernel/random/boot_id")
    _require(not any(m[0] != "/proc" and any(p == m[0] or p.startswith(m[0] + "/") for p in sensitive)
                     for m in mounts))
    ns = os.stat("/proc/self/ns/pid")
    view = os.stat("/proc/1/ns/pid")
    user = os.stat("/proc/self/ns/user")
    _require((ns.st_dev, ns.st_ino) == (view.st_dev, view.st_ino))
    owner = RunOwner(host_domain_id, Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip(),
                     ns.st_dev, ns.st_ino, view.st_dev, view.st_ino, os.geteuid(),
                     user.st_dev, user.st_ino, pid, _stat_ticks(Path("/proc/self/stat").read_text(encoding="ascii"), pid))
    return owner, tuple(mounts)


def observe_run(owner):
    """LIVE/DEAD only within a stable full owner domain; other boots stay UNKNOWN."""
    try:
        before, view = _linux_owner(owner.host_domain_id, owner.pid)
        names = tuple(f.name for f in fields(RunOwner) if f.name not in {"pid", "start_ticks"})
        if any(getattr(before, n) != getattr(owner, n) for n in names):
            return "unknown"
        try:
            raw = Path(f"/proc/{owner.pid}/stat").read_text(encoding="ascii")
        except FileNotFoundError:
            raw = None
        if raw is not None:
            ticks = _stat_ticks(raw, owner.pid)
            target_ns = os.stat(f"/proc/{owner.pid}/ns/pid")
            target_user = os.stat(f"/proc/{owner.pid}/ns/user")
            if ((target_ns.st_dev, target_ns.st_ino) != (owner.pid_namespace_device, owner.pid_namespace_inode)
                    or (target_user.st_dev, target_user.st_ino) != (owner.user_namespace_device, owner.user_namespace_inode)
                    or os.stat(f"/proc/{owner.pid}").st_uid != owner.uid):
                return "unknown"
            if _stat_ticks(Path(f"/proc/{owner.pid}/stat").read_text(encoding="ascii"), owner.pid) != ticks:
                return "unknown"
            state = "live" if ticks == owner.start_ticks else "dead"
        else:
            # ENOENT counts only after _linux_owner established unrestricted view.
            state = "dead"
        after, after_view = _linux_owner(owner.host_domain_id, owner.pid)
        return state if before == after and view == after_view else "unknown"
    except (OSError, ValueError, UnicodeError, IndexError):
        return "unknown"


@dataclass(frozen=True, slots=True)
class AuthorityScope:
    """Trusted managed-owner input, never parsed from a query/client request.

    Integration owns reviewed whole-router HOLD/closed roster proof. Merely
    constructing this object or enumerating run rows supplies no such proof.
    """

    domain_id: str
    configuration_revision: str
    run_ids: tuple[str, ...]
    from_utc: str
    to_utc: str
    quiesced_run_ids: tuple[str, ...] = ()

    def __post_init__(self):
        _id(self.domain_id); _id(self.configuration_revision)
        object.__setattr__(self, "from_utc", _utc(self.from_utc))
        object.__setattr__(self, "to_utc", _utc(self.to_utc))
        _require(self.from_utc < self.to_utc and type(self.run_ids) is tuple
                 and 0 < len(self.run_ids) <= 1024 and len(set(self.run_ids)) == len(self.run_ids)
                 and type(self.quiesced_run_ids) is tuple
                 and set(self.quiesced_run_ids) <= set(self.run_ids))
        for value in self.run_ids:
            _uuid(value)


@dataclass(frozen=True, slots=True)
class CoverageState:
    domain_id: str
    configuration_revision: str
    coverage_epoch: str
    snapshot_revision: int
    segments: tuple[tuple, ...]
    gaps: tuple[str, ...]
    roster_complete: bool
    authoritative_from: str | None
    authoritative_to: str | None

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
_COUNTER_NAMES = tuple(part.split()[0] for part in _COUNTERS.split(",\n"))
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
        accounting_failures INTEGER NOT NULL DEFAULT 0 CHECK(typeof(accounting_failures)='integer' AND accounting_failures>=0),
        detail_floor_utc TEXT CHECK(detail_floor_utc IS NULL OR
            (typeof(detail_floor_utc)='text' AND length(CAST(detail_floor_utc AS BLOB))<=32)),
        daily_floor_utc TEXT CHECK(daily_floor_utc IS NULL OR
            (typeof(daily_floor_utc)='text' AND length(CAST(daily_floor_utc AS BLOB))<=32)))"""),
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
        self._pending_failure = False

    @contextmanager
    def _write(self):
        try:
            with self.key_store._connect() as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN IMMEDIATE")
                if db.execute("PRAGMA user_version").fetchone()[0] != _VERSION:
                    raise UsageError("accounting_unavailable")
                try:
                    yield db
                    db.execute("COMMIT")
                except BaseException:
                    if db.in_transaction:
                        db.execute("ROLLBACK")
                    raise
        except UsageError as exc:
            if exc.code == "accounting_unavailable":
                self._pending_failure = True
            raise
        except (sqlite3.Error, KeyStoreError, OSError):
            self._pending_failure = True
            raise UsageError("accounting_unavailable") from None

    @staticmethod
    def _revision(db, domain_id):
        row = db.execute("SELECT snapshot_revision FROM usage_domains WHERE domain_id=?", (domain_id,)).fetchone()
        if row is None or type(row[0]) is not int or not 0 <= row[0] < _MAX_INT:
            raise UsageError("accounting_unavailable")
        db.execute("UPDATE usage_domains SET snapshot_revision=? WHERE domain_id=?", (row[0] + 1, domain_id))

    @staticmethod
    def _run_owner(row):
        return RunOwner(**{f.name: row[f.name] for f in fields(RunOwner)})

    def register_run(self, owner, *, domain_id, configuration_revision, enabled=False, started_at=None):
        """Register actual local ownership; this alone never proves closed roster."""
        _require(type(owner) is RunOwner and type(enabled) is bool)
        _id(domain_id); _id(configuration_revision)
        at = _utc(started_at or _now())
        try:
            first = _linux_owner(owner.host_domain_id)
            _require(first[0] == owner and first == _linux_owner(owner.host_domain_id))
        except (OSError, ValueError, UnicodeError, IndexError):
            raise UsageError("accounting_configuration_unsupported") from None
        run_id, segment_id = str(uuid.uuid4()), str(uuid.uuid4())
        with self._write() as db:
            domain = db.execute("SELECT * FROM usage_domains WHERE domain_id=?", (domain_id,)).fetchone()
            if domain is None:
                db.execute("INSERT INTO usage_domains(domain_id,coverage_epoch,configuration_revision) VALUES(?,?,?)",
                           (domain_id, str(uuid.uuid4()), configuration_revision))
            elif domain["configuration_revision"] != configuration_revision:
                raise UsageError("accounting_configuration_unsupported")
            db.execute("INSERT INTO usage_runs VALUES(" + ",".join("?" for _ in range(16)) + ")",
                       (run_id, domain_id, at, None, "live", *(getattr(owner, f.name) for f in fields(owner))))
            db.execute("INSERT INTO usage_coverage_segments VALUES(?,?,?,?,?,?,?,?,?)",
                       (segment_id, domain_id, run_id, configuration_revision, int(enabled), at, None, None, 0))
            self._revision(db, domain_id)
        return run_id

    def _coverage_state(self, db, authority_scope=None, *, domain_id=None):
        """T007 consumes this in its counter read snapshot; no owner RPC here."""
        if authority_scope is not None:
            _require(type(authority_scope) is AuthorityScope)
            _require(domain_id is None or domain_id == authority_scope.domain_id)
            domain_id = authority_scope.domain_id
        _id(domain_id)
        domain = db.execute("SELECT * FROM usage_domains WHERE domain_id=?", (domain_id,)).fetchone()
        if domain is None:
            raise UsageError("accounting_configuration_unsupported")
        # Row tuples make the projection immutable; column order is explicit.
        columns = ("segment_id", "domain_id", "run_id", "configuration_revision", "enabled",
                   "started_at", "ended_at", "closure_reason", "end_uncertain")
        bounds = " AND started_at<? AND (ended_at IS NULL OR ended_at>?)" if authority_scope else ""
        parameters = (domain_id, authority_scope.to_utc, authority_scope.from_utc) if authority_scope else (domain_id,)
        segment_rows = db.execute("SELECT * FROM usage_coverage_segments WHERE domain_id=?" + bounds +
                                  " ORDER BY started_at,segment_id LIMIT 4097", parameters).fetchall()
        segments = tuple(tuple(r[n] for n in columns) for r in segment_rows[:4096])
        gaps = set()
        if len(segment_rows) > 4096:
            gaps.add("coverage_projection_limit")
        if authority_scope is None:
            gaps.add("owner_roster_unknown")
        else:
            scope = authority_scope
            if domain["configuration_revision"] != scope.configuration_revision:
                gaps.add("configuration_mismatch")
            run_rows = db.execute("SELECT * FROM usage_runs WHERE domain_id=?" + bounds +
                                  " ORDER BY run_id LIMIT 1025", parameters).fetchall()
            if len(run_rows) > 1024:
                gaps.add("coverage_projection_limit")
            runs = {r["run_id"]: r for r in run_rows[:1024]}
            potential = {rid for rid, r in runs.items() if r["started_at"] < scope.to_utc
                         and (r["ended_at"] is None or r["ended_at"] > scope.from_utc)}
            if potential != set(scope.run_ids):
                gaps.add("owner_roster_unknown")
            for rid in scope.run_ids:
                run = runs.get(rid)
                if run is None:
                    gaps.add("owner_unknown")
                    continue
                if rid in scope.quiesced_run_ids:
                    continue
                parts = [s for s in segments if s[2] == rid and s[5] < scope.to_utc
                         and (s[6] is None or s[6] > scope.from_utc)]
                lower = max(scope.from_utc, run["started_at"])
                upper = min(scope.to_utc, run["ended_at"] or scope.to_utc)
                cursor = lower
                for s in parts:
                    if s[3] != scope.configuration_revision:
                        gaps.add("configuration_mismatch")
                    if not s[4]:
                        gaps.add("accounting_disabled")
                    if s[8] or s[6] is None and run["state"] != "live":
                        gaps.add("owner_unknown")
                    if s[5] > cursor:
                        gaps.add("segment_missing")
                    cursor = max(cursor, min(upper, s[6] or upper))
                if cursor < upper or not parts:
                    gaps.add("segment_missing")
        return CoverageState(domain_id, domain["configuration_revision"], domain["coverage_epoch"],
                             domain["snapshot_revision"], segments, tuple(sorted(gaps)), not gaps,
                             authority_scope.from_utc if authority_scope else None,
                             authority_scope.to_utc if authority_scope else None)

    def coverage_transition(self, run_id, enabled, configuration_revision, *, authority_scope=None, at=None):
        _uuid(run_id); _id(configuration_revision); _require(type(enabled) is bool)
        at = _utc(at or _now())
        with self._write() as db:
            run = db.execute("SELECT * FROM usage_runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None or run["state"] != "live" or self._run_owner(run) != RunOwner.observe(run["host_domain_id"]):
                raise UsageError("accounting_configuration_unsupported")
            parts = db.execute("SELECT * FROM usage_coverage_segments WHERE run_id=? AND ended_at IS NULL", (run_id,)).fetchall()
            if len(parts) != 1 or at < parts[0]["started_at"]:
                raise UsageError("accounting_configuration_unsupported")
            current = parts[0]
            if current["enabled"] == int(enabled) and current["configuration_revision"] == configuration_revision:
                return current["segment_id"]
            if configuration_revision != current["configuration_revision"]:
                if db.execute("SELECT 1 FROM usage_runs WHERE domain_id=? AND run_id<>? AND ended_at IS NULL LIMIT 1",
                              (run["domain_id"], run_id)).fetchone():
                    raise UsageError("accounting_configuration_unsupported")
                db.execute("UPDATE usage_domains SET configuration_revision=? WHERE domain_id=?",
                           (configuration_revision, run["domain_id"]))
            if enabled:
                if (authority_scope is None or authority_scope.configuration_revision != configuration_revision
                        or authority_scope.domain_id != run["domain_id"]):
                    raise UsageError("accounting_configuration_unsupported")
                _require(authority_scope.from_utc <= at < authority_scope.to_utc)
                # Disabled segment legitimately precedes this transition; it is
                # historical gap, not permission to ignore another admitting run.
                other = db.execute("SELECT run_id,state FROM usage_runs WHERE domain_id=? AND ended_at IS NULL",
                                   (run["domain_id"],)).fetchall()
                if ({r[0] for r in other} != set(authority_scope.run_ids)
                        or any(r[1] != "live" and r[0] not in authority_scope.quiesced_run_ids for r in other)):
                    raise UsageError("accounting_configuration_unsupported")
                for r in other:
                    if r[0] == run_id or r[0] in authority_scope.quiesced_run_ids:
                        continue
                    active = db.execute("SELECT enabled,configuration_revision FROM usage_coverage_segments "
                                        "WHERE run_id=? AND ended_at IS NULL", (r[0],)).fetchall()
                    if len(active) != 1 or not active[0][0] or active[0][1] != configuration_revision:
                        raise UsageError("accounting_configuration_unsupported")
            db.execute("UPDATE usage_coverage_segments SET ended_at=?,closure_reason=? WHERE segment_id=?",
                       (at, "mode_change", current["segment_id"]))
            segment_id = str(uuid.uuid4())
            db.execute("INSERT INTO usage_coverage_segments VALUES(?,?,?,?,?,?,?,?,?)",
                       (segment_id, run["domain_id"], run_id, configuration_revision, int(enabled), at, None, None, 0))
            self._revision(db, run["domain_id"])
        return segment_id

    def start(self, start, *, authority_scope=None):
        _require(type(start) is RequestStart)
        payload = start.to_json()
        with self._write() as db:
            now = _now()  # sample after writer wait, not before acquiring it
            cutoff = (datetime.fromisoformat(now[:-1] + "+00:00") - timedelta(days=30)).isoformat(
                timespec="microseconds").replace("+00:00", "Z")
            if start.accepted_at < cutoff:
                raise UsageError("accounting_conflict")
            prior = db.execute("SELECT start_payload FROM usage_starts WHERE request_id=?", (start.request_id,)).fetchone()
            if prior:
                if prior[0] != payload:
                    raise UsageError("accounting_conflict")
                return "same"
            run = db.execute("SELECT * FROM usage_runs WHERE run_id=?", (start.run_id,)).fetchone()
            if run is None or run["state"] != "live" or self._run_owner(run) != RunOwner.observe(run["host_domain_id"]):
                raise UsageError("accounting_configuration_unsupported")
            domain = db.execute("SELECT * FROM usage_domains WHERE domain_id=?", (run["domain_id"],)).fetchone()
            if domain["detail_floor_utc"] is not None and start.accepted_at < domain["detail_floor_utc"]:
                raise UsageError("accounting_conflict")
            if (authority_scope is None or authority_scope.domain_id != run["domain_id"]
                    or start.run_id in authority_scope.quiesced_run_ids
                    or not authority_scope.from_utc <= start.accepted_at <= now < authority_scope.to_utc
                    or not self._coverage_state(db, authority_scope).roster_complete):
                raise UsageError("accounting_configuration_unsupported")
            segment = db.execute("SELECT * FROM usage_coverage_segments WHERE run_id=? AND ended_at IS NULL", (start.run_id,)).fetchall()
            if (len(segment) != 1 or not segment[0]["enabled"] or segment[0]["started_at"] > start.accepted_at
                    or segment[0]["configuration_revision"] != domain["configuration_revision"]):
                raise UsageError("accounting_configuration_unsupported")
            if start.parent_request_id is not None:
                parent = db.execute("SELECT * FROM usage_starts WHERE request_id=?", (start.parent_request_id,)).fetchone()
                if parent is None:
                    raise UsageError("accounting_start_missing")
                saved = RequestStart.from_json(parent["start_payload"])
                if (saved.caller != start.caller or saved.run_id != start.run_id
                        or saved.accepted_at > start.accepted_at
                        or saved.usage_relation == "inclusive_parent" and start.usage_relation == "exclusive"):
                    raise UsageError("accounting_conflict")
            names = ("request_id", "run_id", "domain_id", "segment_id", "configuration_revision", "accepted_at",
                     "caller", "kind", "model", "parent_request_id", "attempt_id", "usage_relation", "start_payload")
            values = (start.request_id, start.run_id, run["domain_id"], segment[0]["segment_id"], domain["configuration_revision"],
                      start.accepted_at, start.caller.to_json(), start.kind, start.model, start.parent_request_id,
                      start.attempt_id, start.usage_relation, payload)
            db.execute(f"INSERT INTO usage_starts({','.join(names)}) VALUES({','.join('?' for _ in names)})", values)
            self._revision(db, run["domain_id"])
        return "started"

    @staticmethod
    def _unresolved(db, request_id):
        row = db.execute("SELECT * FROM usage_starts WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise UsageError("accounting_start_missing")
        if db.execute("SELECT 1 FROM usage_details WHERE request_id=?", (request_id,)).fetchone():
            raise UsageError("accounting_conflict")
        return row

    def note_dispatch(self, request_id, attempt_route):
        _uuid(request_id); _require(type(attempt_route) is RouteAssociation)
        payload = attempt_route.to_json()
        with self._write() as db:
            row = self._unresolved(db, request_id)
            if row["dispatched"] is not None:
                if row["dispatched"] != 1 or row["route_association"] != payload:
                    raise UsageError("accounting_conflict")
                return "same"
            db.execute("UPDATE usage_starts SET dispatched=1,route_association=? WHERE request_id=?", (payload, request_id))
            self._revision(db, row["domain_id"])
        return "recorded"

    def note_observation(self, request_id, checkpoint):
        _uuid(request_id); _require(type(checkpoint) is Observation)
        with self._write() as db:
            row = self._unresolved(db, request_id)
            start = RequestStart.from_json(row["start_payload"])
            if row["dispatched"] != 1:
                raise UsageError("accounting_conflict")
            self._applicability(start, checkpoint.tokens)
            _require(checkpoint.observed_at >= start.accepted_at)
            if row["observation_payload"] is not None:
                old = Observation.from_json(row["observation_payload"])
                if checkpoint.sequence == old.sequence and checkpoint.to_json() == old.to_json():
                    return "same"
                if checkpoint.sequence <= old.sequence or checkpoint.observed_at < old.observed_at:
                    raise UsageError("accounting_conflict")
            db.execute("UPDATE usage_starts SET observation_payload=? WHERE request_id=?", (checkpoint.to_json(), request_id))
            self._revision(db, row["domain_id"])
        return "recorded"

    @staticmethod
    def _applicability(start, tokens):
        _require(tokens.input.applicability == start.input_applicability
                 and tokens.output.applicability == start.output_applicability)
        _require(tokens.input.applicability != "not_applicable" or all(getattr(tokens, n) is None for n in (
            "uncached_input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")))
        _require(tokens.output.applicability != "not_applicable" or tokens.reasoning_output_tokens is None)

    def finalize(self, terminal):
        _require(type(terminal) is Terminal)
        with self._write() as db:
            return self._finalize(db, terminal)

    def _finalize(self, db, terminal):
        row = db.execute("SELECT * FROM usage_starts WHERE request_id=?", (terminal.request_id,)).fetchone()
        if row is None:
            raise UsageError("accounting_start_missing")
        prior = db.execute("SELECT terminal_payload FROM usage_details WHERE request_id=?", (terminal.request_id,)).fetchone()
        payload = terminal.to_json()
        if prior:
            if prior[0] != payload:
                raise UsageError("accounting_conflict")
            return "same"
        start = RequestStart.from_json(row["start_payload"])
        self._applicability(start, terminal.tokens)
        _require(terminal.ended_at >= start.accepted_at)
        _require(start.usage_relation != "unobserved" or "usage_relation_unknown" in terminal.coverage)
        if row["dispatched"] == 1 and terminal.dispatched is False:
            raise UsageError("accounting_conflict")
        if row["route_association"] is not None and row["route_association"] != terminal.route.to_json():
            raise UsageError("accounting_conflict")
        db.execute("INSERT INTO usage_details VALUES(?,?,?,?)", (terminal.request_id, terminal.ended_at, terminal.outcome, payload))
        dimensions, counters = self._contribution(start, terminal, row["domain_id"])
        for table in ("usage_daily", "usage_cumulative"):
            self._add(db, table, dimensions, counters, start.accepted_at[:10], terminal.ended_at)
        self._revision(db, row["domain_id"])
        return "committed"

    @staticmethod
    def _contribution(start, terminal, domain_id):
        caller, usage = start.caller, terminal.tokens
        user = caller.end_user
        dimensions = {"domain_id": domain_id, "actor_kind": caller.actor.kind, "actor_id": caller.actor.id,
                      "binding_revision": caller.actor.binding_revision, "end_user_instance": user.instance if user else None,
                      "end_user_issuer": user.issuer if user else None, "end_user_subject": user.subject if user else None,
                      "grant_kind": caller.grant.kind, "grant_reference": caller.grant.reference,
                      "grant_revision": caller.grant.revision, "grant_policy_digest": caller.grant.policy_digest,
                      "grant_generation": caller.grant.generation, "grant_epoch": caller.grant.epoch,
                      "credential_id": caller.credential_id, "model": start.model, "outcome": terminal.outcome}
        for name, direction in (("input", usage.input), ("output", usage.output)):
            dimensions.update({name + "_applicability": direction.applicability, name + "_source": direction.source,
                               name + "_partial": int(direction.partial)})
        dimensions["group_key"] = _json({k: v for k, v in dimensions.items() if k != "domain_id"}, 8192)
        counts = dict.fromkeys(_COUNTER_NAMES, 0)
        counts["requests"] = int(start.parent_request_id is None)
        counts["attempts"] = int(terminal.dispatched is True)
        contributes = terminal.dispatched is not False and start.usage_relation != "unobserved" and not (
            start.parent_request_id is not None and start.usage_relation == "inclusive_parent")
        if contributes:
            for name, direction in (("input", usage.input), ("output", usage.output)):
                if direction.applicability == "not_applicable":
                    counts["not_applicable_" + name + "_requests"] = 1
                elif direction.source == "unknown":
                    counts["unknown_" + name + "_requests"] = 1
                else:
                    counts[direction.source + "_" + name] = direction.count
                counts["partial_" + name + "_requests"] = int(direction.partial)
            for field, counter, unknown, applicability in (
                ("cache_read_input_tokens", "cache_read_input", "unknown_cache_read_requests", usage.input.applicability),
                ("cache_creation_input_tokens", "cache_creation_input", "unknown_cache_creation_requests", usage.input.applicability),
                ("reasoning_output_tokens", "reasoning_output", "unknown_reasoning_requests", usage.output.applicability),
            ):
                if applicability == "applicable":
                    value = getattr(usage, field)
                    counts[counter] = value or 0
                    counts[unknown] = int(value is None)
        elif start.usage_relation == "unobserved":
            for name, direction in (("input", usage.input), ("output", usage.output)):
                counts[("unknown_" if direction.applicability == "applicable" else "not_applicable_") + name + "_requests"] = 1
        if start.parent_request_id is None and terminal.latency_ms is not None:
            counts["latency_sum_ms"], counts["latency_count"] = terminal.latency_ms, 1
            for bound in (100, 1000, 5000, 30000, 120000, 900000):
                counts[f"latency_le_{bound}_ms"] = int(terminal.latency_ms <= bound)
            counts["latency_le_inf"] = 1
        return dimensions, counts

    @staticmethod
    def _add(db, table, dimensions, counters, day, ended_at):
        values = dict(dimensions)
        key = {"domain_id": values["domain_id"], "group_key": values["group_key"]}
        if table == "usage_daily":
            values["accepted_day"] = key["accepted_day"] = day
        where = " AND ".join(n + "=?" for n in key)
        old = db.execute(f"SELECT * FROM {table} WHERE {where}", tuple(key.values())).fetchone()
        for name, contribution in counters.items():
            previous = old[name] if old else 0
            if type(previous) is not int or type(contribution) is not int or not 0 <= previous <= _MAX_INT - contribution:
                raise UsageError("accounting_unavailable")
            values[name] = previous + contribution
        values["last_activity_at"] = max(old["last_activity_at"], ended_at) if old else ended_at
        if old:
            names = (*counters, "last_activity_at")
            db.execute(f"UPDATE {table} SET " + ",".join(n + "=?" for n in names) + " WHERE " + where,
                       (*(values[n] for n in names), *key.values()))
        else:
            db.execute(f"INSERT INTO {table}({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))

    def recover(self, run_ids, *, host_domain_id):
        """Observe full Linux identity outside SQLite; no caller-supplied DEAD flag."""
        _id(host_domain_id)
        _require(type(run_ids) is tuple and len(run_ids) <= 1024 and len(set(run_ids)) == len(run_ids))
        for rid in run_ids:
            _uuid(rid)
        try:
            with self.key_store._connect() as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN")
                owners = {}
                for rid in run_ids:
                    row = db.execute("SELECT * FROM usage_runs WHERE run_id=?", (rid,)).fetchone()
                    if row is not None:
                        owners[rid] = self._run_owner(row)
        except (sqlite3.Error, KeyStoreError, OSError):
            raise UsageError("accounting_unavailable") from None
        states = {rid: observe_run(owner) if owner.host_domain_id == host_domain_id else "unknown"
                  for rid, owner in owners.items()}
        result = {"recovered_requests": 0, "dead_runs": 0, "live_runs": 0, "unknown_runs": len(run_ids) - len(owners)}
        with self._write() as db:
            at = _now()
            for rid, state in states.items():
                run = db.execute("SELECT * FROM usage_runs WHERE run_id=?", (rid,)).fetchone()
                if run is None or self._run_owner(run) != owners[rid]:
                    result["unknown_runs"] += 1
                    continue
                result[state + "_runs"] += 1
                if state == "dead":
                    for row in db.execute("SELECT s.* FROM usage_starts s LEFT JOIN usage_details d USING(request_id) "
                                          "WHERE s.run_id=? AND d.request_id IS NULL", (rid,)).fetchall():
                        start = RequestStart.from_json(row["start_payload"])
                        usage = Observation.from_json(row["observation_payload"]).tokens if row["observation_payload"] else TokenUsage(
                            input=TokenDirection(applicability=start.input_applicability),
                            output=TokenDirection(applicability=start.output_applicability))
                        usage = replace(usage, input=replace(usage.input, partial=usage.input.applicability == "applicable"),
                                        output=replace(usage.output, partial=usage.output.applicability == "applicable"))
                        coverage = ("dispatch_uncertain", "usage_incomplete", "recovered_partial")
                        if start.usage_relation == "unobserved":
                            coverage += ("usage_relation_unknown",)
                        terminal = Terminal(start.request_id, max(at, start.accepted_at), True if row["dispatched"] == 1 else None,
                                            "interrupted", "unknown", "interrupted",
                                            RouteAssociation.from_json(row["route_association"]) if row["route_association"] else RouteAssociation(),
                                            usage, error_code="interrupted", coverage=coverage)
                        self._finalize(db, terminal)
                        result["recovered_requests"] += 1
                    if run["state"] != "dead":
                        segment_start = db.execute("SELECT MAX(started_at) FROM usage_coverage_segments WHERE run_id=?",
                                                   (rid,)).fetchone()[0]
                        end = max(at, run["started_at"], segment_start or run["started_at"])
                        db.execute("UPDATE usage_runs SET state='dead',ended_at=? WHERE run_id=?", (end, rid))
                        db.execute("UPDATE usage_coverage_segments SET ended_at=?,closure_reason='owner_dead',end_uncertain=1 "
                                   "WHERE run_id=? AND ended_at IS NULL", (end, rid))
                        self._revision(db, run["domain_id"])
                elif run["state"] != state and run["state"] != "dead":
                    db.execute("UPDATE usage_runs SET state=? WHERE run_id=?", (state, rid))
                    self._revision(db, run["domain_id"])
        return result

    def record_failure(self, domain_id):
        """Integration may persist its fixed failure count after storage returns."""
        _id(domain_id)
        with self._write() as db:
            row = db.execute("SELECT accounting_failures FROM usage_domains WHERE domain_id=?", (domain_id,)).fetchone()
            if row is None or type(row[0]) is not int or row[0] >= _MAX_INT:
                raise UsageError("accounting_unavailable")
            db.execute("UPDATE usage_domains SET accounting_failures=? WHERE domain_id=?", (row[0] + 1, domain_id))
            self._revision(db, domain_id)
        self._pending_failure = False

    def health(self, domain_id):
        _id(domain_id)
        try:
            with self.key_store._connect() as db:
                db.row_factory = sqlite3.Row
                db.execute("BEGIN")
                domain = db.execute("SELECT * FROM usage_domains WHERE domain_id=?", (domain_id,)).fetchone()
                if domain is None:
                    raise UsageError("accounting_configuration_unsupported")
                unresolved = db.execute("SELECT COUNT(*) FROM usage_starts s LEFT JOIN usage_details d USING(request_id) "
                                        "WHERE s.domain_id=? AND d.request_id IS NULL", (domain_id,)).fetchone()[0]
                unknown = db.execute("SELECT COUNT(*) FROM usage_runs WHERE domain_id=? AND state='unknown'", (domain_id,)).fetchone()[0]
                return {"available": not self._pending_failure, "snapshot_revision": domain["snapshot_revision"],
                        "unresolved_requests": unresolved, "unknown_runs": unknown,
                        "accounting_failures": domain["accounting_failures"], "failure_pending": self._pending_failure,
                        "coverage_complete": False}  # health alone supplies no trusted owner roster
        except (sqlite3.Error, KeyStoreError, OSError):
            raise UsageError("accounting_unavailable") from None

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
