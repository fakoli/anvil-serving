"""Durable custody for one approved native propagation job.

This is deliberately separate from controller operation receipts: a lost reply
must be reconciled from the durable job, never retried as a new process.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import sqlite3
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from ..propagation import PropagationContractError, parse_contract

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE = frozenset({"launch_pending", "launch_custody", "registered", "executing", "cancellation_requested", "reconciling"})
_TERMINAL = frozenset({"applied", "failed", "cancelled", "recovery_required"})
_MAX_RESULT = 64 * 1024
_MAX_CHILD_RESULT = 256 * 1024
_MAX_NATIVE_EFFECTS = 384
_PUBLIC_STATE = {state: "running" for state in _ACTIVE | {"launch_pending"}}


class PropagationJobError(ValueError):
    """A stable public failure code; never place child details here."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _id(value: object, code: str = "malformed_identity") -> str:
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise PropagationJobError(code)
    return value


def _digest(value: object, code: str = "malformed_digest") -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise PropagationJobError(code)
    return value


def _json(value: object, *, limit: int = _MAX_RESULT) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PropagationJobError("malformed_payload") from exc
    if len(raw.encode("ascii")) > limit:
        raise PropagationJobError("payload_too_large")
    return raw


def _stamp(now: datetime) -> str:
    if not isinstance(now, datetime) or now.tzinfo != timezone.utc or now.utcoffset().total_seconds() != 0:
        raise PropagationJobError("malformed_clock")
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    """Constructor-owned execution details; requests only name an accepted one."""

    profile_id: str
    digest: str
    argv: tuple[str, ...]
    executable_sha256: str
    artifact_pins: tuple[tuple[str, str], ...] = ()
    cwd: str | None = None
    environment: Mapping[str, str] | None = None
    budget_seconds: int = 900
    heartbeat_seconds: int = 30
    output_limit: int = _MAX_CHILD_RESULT

    def __post_init__(self) -> None:
        _id(self.profile_id)
        _digest(self.digest)
        _digest(self.executable_sha256)
        if (not self.argv or any(type(item) is not str or not item for item in self.argv)
                or not Path(self.argv[0]).is_absolute() or self.cwd is not None and not Path(self.cwd).is_absolute()):
            raise ValueError("invalid execution profile")
        if type(self.budget_seconds) is not int or not 1 <= self.budget_seconds <= 900:
            raise ValueError("invalid execution profile")
        if type(self.heartbeat_seconds) is not int or not 1 <= self.heartbeat_seconds <= 30:
            raise ValueError("invalid execution profile")
        if type(self.output_limit) is not int or not 1024 <= self.output_limit <= _MAX_CHILD_RESULT:
            raise ValueError("invalid execution profile")
        env = self.environment or {}
        if (not isinstance(env, Mapping) or len(env) > 32 or any(type(key) is not str or type(value) is not str or not key or len(key) > 128 or len(value) > 4096 for key, value in env.items())):
            raise ValueError("invalid execution profile")
        object.__setattr__(self, "environment", MappingProxyType(dict(env)))
        pins: list[tuple[str, str]] = []
        for pin in self.artifact_pins:
            if type(pin) is not tuple or len(pin) != 2 or type(pin[0]) is not str or not Path(pin[0]).is_absolute():
                raise ValueError("invalid execution profile")
            pins.append((pin[0], _digest(pin[1])))
        object.__setattr__(self, "artifact_pins", tuple(pins))

    def private_value(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "digest": self.digest, "argv": list(self.argv),
                "executable_sha256": self.executable_sha256, "artifact_pins": list(self.artifact_pins),
                "cwd": self.cwd, "environment": dict(self.environment or {}),
                "budget_seconds": self.budget_seconds, "heartbeat_seconds": self.heartbeat_seconds,
                "output_limit": self.output_limit}

    @classmethod
    def from_private_value(cls, value: object) -> "ExecutionProfile":
        if not isinstance(value, dict) or set(value) != {"profile_id", "digest", "argv", "executable_sha256", "artifact_pins", "cwd", "environment", "budget_seconds", "heartbeat_seconds", "output_limit"}:
            raise PropagationJobError("storage_unavailable")
        try:
            return cls(value["profile_id"], value["digest"], tuple(value["argv"]), value["executable_sha256"],
                       tuple(tuple(item) for item in value["artifact_pins"]), value["cwd"], value["environment"], value["budget_seconds"],
                       value["heartbeat_seconds"], value["output_limit"])
        except (TypeError, ValueError, PropagationJobError) as exc:
            raise PropagationJobError("storage_unavailable") from exc

    def verify_executable(self) -> None:
        for path, expected in ((self.argv[0], self.executable_sha256), *self.artifact_pins):
            try:
                handle = open(path, "rb", buffering=0)
            except OSError as exc:
                raise PropagationJobError("profile_unavailable") from exc
            with handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual != expected:
                raise PropagationJobError("profile_changed")


class JobStore:
    def __init__(self, path: str | Path, profiles: Mapping[str, ExecutionProfile] | None = None) -> None:
        self.path = str(path)
        self.profiles = dict(profiles or {})
        if any(key != value.profile_id for key, value in self.profiles.items()):
            raise ValueError("profiles must be keyed by profile_id")
        self._migrate()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS propagation_native_jobs (
                job_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL UNIQUE, operation_id TEXT NOT NULL,
                binding_digest TEXT NOT NULL, contract_digest TEXT NOT NULL, canonical_contract BLOB NOT NULL,
                preview_digest TEXT NOT NULL, resources_json TEXT NOT NULL, deadline_at TEXT NOT NULL,
                profile_json TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, heartbeat_at TEXT,
                completed_at TEXT, launch_token_digest TEXT, pid INTEGER, start_ticks TEXT, boot_id TEXT,
                registered_at TEXT, result_json TEXT, convergence_ref TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS propagation_native_launches (
                job_id TEXT PRIMARY KEY REFERENCES propagation_native_jobs(job_id), token_digest TEXT NOT NULL,
                pid INTEGER, start_ticks TEXT, boot_id TEXT, custody_at TEXT NOT NULL, registered_at TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS propagation_native_resources (
                resource_key TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES propagation_native_jobs(job_id)
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS propagation_native_children (
                job_id TEXT PRIMARY KEY REFERENCES propagation_native_jobs(job_id), effects_json TEXT NOT NULL,
                state TEXT NOT NULL, pid INTEGER, start_ticks TEXT, boot_id TEXT, custody_at TEXT NOT NULL,
                registered_at TEXT, result_json TEXT, result_digest TEXT
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS propagation_native_settlements (
                job_id TEXT PRIMARY KEY REFERENCES propagation_native_jobs(job_id),
                intent_id TEXT NOT NULL, contract_digest TEXT NOT NULL,
                receipt_json TEXT NOT NULL
            )""")
            for column in ("result_json TEXT", "result_digest TEXT"):
                try:
                    db.execute("ALTER TABLE propagation_native_children ADD COLUMN " + column)
                except sqlite3.OperationalError:
                    pass

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for field in ("resources_json", "profile_json", "result_json"):
            if value.get(field) is not None:
                value[field.removesuffix("_json")] = json.loads(value.pop(field))
        value["canonical_contract"] = bytes(value["canonical_contract"])
        return value

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        value = JobStore._row(row)
        internal = value["state"]
        value["state"] = _PUBLIC_STATE.get(internal, internal)
        value["launch_phase"] = internal
        value["never_launched"] = internal == "launch_pending"
        return value

    def _job(self, db: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM propagation_native_jobs WHERE job_id=?", (_id(job_id),)).fetchone()
        if row is None:
            raise PropagationJobError("job_not_found")
        return row

    def submit(self, binding: Mapping[str, Any], now: datetime | None = None) -> tuple[dict[str, Any], bool]:
        """Persist exact approved custody before any process can be created."""
        if not isinstance(binding, Mapping) or set(binding) != {"intent_id", "operation_id", "canonical_contract", "preview_digest", "profile_id", "profile_digest", "resources", "deadline"}:
            raise PropagationJobError("malformed_binding")
        intent_id, operation_id = _id(binding["intent_id"]), _id(binding["operation_id"])
        profile_id, profile_digest = _id(binding["profile_id"]), _digest(binding["profile_digest"])
        try:
            contract = parse_contract(binding["canonical_contract"])
        except PropagationContractError as exc:
            raise PropagationJobError(exc.code) from exc
        if (contract.value["execution_profile_ref"], contract.value["execution_profile_digest"], contract.value["deadline_at"]) != (profile_id, profile_digest, binding["deadline"]):
            raise PropagationJobError("binding_mismatch")
        _digest(binding["preview_digest"])
        deadline = _parse_stamp(binding["deadline"])
        stamp = _stamp(now or datetime.now(timezone.utc))
        if (type(binding["resources"]) is not list or any(type(item) is not str or _ID.fullmatch(item) is None for item in binding["resources"])
                or binding["resources"] != sorted(set(binding["resources"]))):
            raise PropagationJobError("malformed_resources")
        accepted_resources = sorted({key for target in contract.value["targets"] for key in target["resource_keys"]})
        if binding["resources"] != accepted_resources:
            raise PropagationJobError("resource_binding_mismatch")
        resources = _json(binding["resources"], limit=_MAX_RESULT)
        # A fresh preview can have a new observation timestamp. It does not
        # authorize a relaunch or replace the original accepted custody.
        stable = {"intent_id": intent_id, "operation_id": operation_id, "contract_digest": contract.digest,
                  "profile_id": profile_id, "profile_digest": profile_digest, "resources": json.loads(resources)}
        binding_digest = hashlib.sha256(_json(stable).encode("ascii")).hexdigest()
        job_id = "propagation-job-" + hashlib.sha256(_json(["propagation-native/v1", intent_id, operation_id]).encode("ascii")).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM propagation_native_jobs WHERE intent_id=?", (intent_id,)).fetchone()
            if row is not None:
                if row["binding_digest"] != binding_digest:
                    db.execute("ROLLBACK")
                    raise PropagationJobError("job_conflict")
                db.execute("COMMIT")
                return self._public(row), True
            if deadline <= _parse_stamp(stamp):
                db.execute("ROLLBACK")
                raise PropagationJobError("deadline_expired")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='propagation_intents'").fetchone():
                current = db.execute(
                    "SELECT i.generation, i.terminal_at, h.generation AS current_generation "
                    "FROM propagation_intents i JOIN propagation_scope_high_water h ON i.scope=h.scope "
                    "WHERE i.intent_id=? AND i.contract_digest=?", (intent_id, contract.digest),
                ).fetchone()
                if current is None or current["generation"] != current["current_generation"] or current["terminal_at"] is not None:
                    db.execute("ROLLBACK")
                    raise PropagationJobError("stale_generation")
            profile = self.profiles.get(profile_id)
            if profile is None or profile.digest != profile_digest:
                db.execute("ROLLBACK")
                raise PropagationJobError("profile_not_approved")
            # First release has one fleet writer, including disjoint target sets.
            if db.execute("SELECT 1 FROM propagation_native_resources LIMIT 1").fetchone():
                db.execute("ROLLBACK")
                raise PropagationJobError("resource_conflict")
            db.execute("""INSERT INTO propagation_native_jobs(job_id,intent_id,operation_id,binding_digest,contract_digest,canonical_contract,preview_digest,resources_json,deadline_at,profile_json,state,created_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (job_id, intent_id, operation_id, binding_digest, contract.digest, contract.canonical,
                        binding["preview_digest"], resources, binding["deadline"], _json(profile.private_value()),
                        "launch_pending", stamp))
            db.executemany("INSERT INTO propagation_native_resources(resource_key,job_id) VALUES(?,?)",
                           ((resource, job_id) for resource in binding["resources"]))
            row = self._job(db, job_id)
            db.execute("COMMIT")
        return self._public(row), False

    def lookup(self, job_id: str) -> dict[str, Any]:
        with self._connection() as db:
            return self._public(self._job(db, job_id))

    def lookup_internal(self, job_id: str) -> dict[str, Any]:
        """Private supervisor view; controller adapters use :meth:`lookup`."""
        with self._connection() as db:
            return self._row(self._job(db, job_id))

    def lookup_by_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM propagation_native_jobs WHERE intent_id=?", (_id(intent_id),)).fetchone()
        return None if row is None else self._public(row)

    def prepare_launch(self, job_id: str, now: datetime | None = None) -> str:
        stamp = _stamp(now or datetime.now(timezone.utc))
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["state"] != "launch_pending":
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            db.execute("UPDATE propagation_native_jobs SET state=?, launch_token_digest=?, heartbeat_at=? WHERE job_id=?",
                       ("launch_custody", digest, stamp, job_id))
            db.execute("INSERT INTO propagation_native_launches(job_id,token_digest,custody_at) VALUES(?,?,?)",
                       (job_id, digest, stamp))
            db.execute("COMMIT")
        return token

    def register_launch(self, job_id: str, token: str, identity: Mapping[str, Any], now: datetime | None = None) -> None:
        stamp = _stamp(now or datetime.now(timezone.utc))
        if not isinstance(identity, Mapping) or set(identity) != {"pid", "start_ticks", "boot_id"} or type(identity["pid"]) is not int or identity["pid"] < 1 or type(identity["start_ticks"]) is not str or not identity["start_ticks"] or type(identity["boot_id"]) is not str or not identity["boot_id"]:
            raise PropagationJobError("malformed_process_identity")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["state"] != "launch_custody" or row["launch_token_digest"] != digest:
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            db.execute("""UPDATE propagation_native_jobs SET state=?,pid=?,start_ticks=?,boot_id=?,registered_at=?,heartbeat_at=? WHERE job_id=?""",
                       ("registered", identity["pid"], identity["start_ticks"], identity["boot_id"], stamp, stamp, job_id))
            db.execute("UPDATE propagation_native_launches SET pid=?,start_ticks=?,boot_id=?,registered_at=? WHERE job_id=?",
                       (identity["pid"], identity["start_ticks"], identity["boot_id"], stamp, job_id))
            db.execute("COMMIT")

    def begin_execution(self, job_id: str, token: str, identity: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
        stamp = _stamp(now or datetime.now(timezone.utc))
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["state"] == "cancellation_requested":
                db.execute("ROLLBACK")
                raise PropagationJobError("cancellation_requested")
            if (row["state"], row["launch_token_digest"], row["pid"], row["start_ticks"], row["boot_id"]) != ("registered", digest, identity.get("pid"), identity.get("start_ticks"), identity.get("boot_id")):
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            if _parse_stamp(row["deadline_at"]) <= _parse_stamp(stamp):
                db.execute("UPDATE propagation_native_jobs SET state=?,completed_at=? WHERE job_id=?", ("recovery_required", stamp, job_id))
                db.execute("COMMIT")
                raise PropagationJobError("deadline_expired")
            db.execute("UPDATE propagation_native_jobs SET state=?,heartbeat_at=? WHERE job_id=?", ("executing", stamp, job_id))
            row = self._job(db, job_id)
            db.execute("COMMIT")
        return self._row(row)

    def heartbeat(self, job_id: str, identity: Mapping[str, Any], now: datetime | None = None) -> None:
        stamp = _stamp(now or datetime.now(timezone.utc))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["state"] not in _ACTIVE or (row["pid"], row["start_ticks"], row["boot_id"]) != (identity.get("pid"), identity.get("start_ticks"), identity.get("boot_id")):
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            db.execute("UPDATE propagation_native_jobs SET heartbeat_at=? WHERE job_id=?", (stamp, job_id))
            db.execute("COMMIT")

    @staticmethod
    def _planned_effects(contract_digest: str, contract: bytes) -> list[dict[str, str]]:
        value = json.loads(contract.decode("ascii"))
        effects = []
        for target in value["targets"]:
            for effect in target["effects"]:
                digest = hashlib.sha256(_json(["effect/v1", contract_digest, target["target_id"], effect]).encode("ascii")).hexdigest()
                effects.append({"effect_id": "effect-" + digest, "state": "uncertain", "receipt_ref": "native-" + digest})
        return effects

    def prepare_profile_child(self, job_id: str, identity: Mapping[str, Any], now: datetime | None = None) -> list[dict[str, str]]:
        """Commit the planned native effects before profile ``Popen``."""
        stamp = _stamp(now or datetime.now(timezone.utc))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["state"] != "executing" or (row["pid"], row["start_ticks"], row["boot_id"]) != (identity.get("pid"), identity.get("start_ticks"), identity.get("boot_id")):
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            effects = self._planned_effects(row["contract_digest"], bytes(row["canonical_contract"]))
            if len(effects) > _MAX_NATIVE_EFFECTS:
                db.execute("ROLLBACK")
                raise PropagationJobError("native_result_capacity")
            effects_json = _json(effects, limit=_MAX_CHILD_RESULT)
            profile = ExecutionProfile.from_private_value(json.loads(row["profile_json"]))
            # Reserve enough output for every accepted effect row using the
            # largest valid opaque fields and longest closed outcome/state.
            completed_example = _json({"outcome": "uncertain", "native_effects": [
                {"effect_id": "e" * 128, "state": "cancelled", "receipt_ref": "r" * 128}
                for _ in effects], "quiescent": False}, limit=_MAX_CHILD_RESULT)
            if len(completed_example.encode("ascii")) > profile.output_limit:
                db.execute("ROLLBACK")
                raise PropagationJobError("native_result_capacity")
            try:
                db.execute("INSERT INTO propagation_native_children(job_id,effects_json,state,custody_at) VALUES(?,?,?,?)",
                           (job_id, effects_json, "launch_custody", stamp))
            except sqlite3.IntegrityError as exc:
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required") from exc
            db.execute("COMMIT")
        return effects

    def register_profile_child(self, job_id: str, identity: Mapping[str, Any], now: datetime | None = None) -> None:
        stamp = _stamp(now or datetime.now(timezone.utc))
        if not isinstance(identity, Mapping) or type(identity.get("pid")) is not int or identity["pid"] < 1 or type(identity.get("start_ticks")) is not str or type(identity.get("boot_id")) is not str:
            raise PropagationJobError("malformed_process_identity")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM propagation_native_children WHERE job_id=?", (_id(job_id),)).fetchone()
            if row is None or row["state"] != "launch_custody":
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            db.execute("UPDATE propagation_native_children SET state=?,pid=?,start_ticks=?,boot_id=?,registered_at=? WHERE job_id=?",
                       ("registered", identity["pid"], identity["start_ticks"], identity["boot_id"], stamp, job_id))
            db.execute("COMMIT")

    def pending_effects(self, job_id: str) -> list[dict[str, str]]:
        with self._connection() as db:
            row = db.execute("SELECT effects_json FROM propagation_native_children WHERE job_id=?", (_id(job_id),)).fetchone()
        return [] if row is None else json.loads(row["effects_json"])

    def record_child_result(self, job_id: str, result: Mapping[str, Any]) -> str:
        """Keep the full bounded native result beside its exact child custody."""
        payload = _json(dict(result), limit=_MAX_CHILD_RESULT)
        digest = hashlib.sha256(payload.encode("ascii")).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM propagation_native_children WHERE job_id=?", (_id(job_id),)).fetchone()
            if row is None or row["state"] != "registered":
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            db.execute("UPDATE propagation_native_children SET state=?,result_json=?,result_digest=? WHERE job_id=?",
                       ("completed", payload, digest, job_id))
            db.execute("COMMIT")
        return digest

    def load_child_result(self, job_id: str, supervisor_identity: Mapping[str, Any], child_identity: Mapping[str, Any], digest: str) -> dict[str, Any]:
        with self._connection() as db:
            job = self._job(db, job_id)
            row = db.execute("SELECT result_json,result_digest,state,pid,start_ticks,boot_id FROM propagation_native_children WHERE job_id=?", (job_id,)).fetchone()
        if ((job["pid"], job["start_ticks"], job["boot_id"]) != (supervisor_identity.get("pid"), supervisor_identity.get("start_ticks"), supervisor_identity.get("boot_id"))
                or row is None or row["state"] != "completed" or (row["pid"], row["start_ticks"], row["boot_id"]) != (child_identity.get("pid"), child_identity.get("start_ticks"), child_identity.get("boot_id"))
                or row["result_digest"] != _digest(digest) or not isinstance(row["result_json"], str)):
            raise PropagationJobError("launch_reconciliation_required")
        if hashlib.sha256(row["result_json"].encode("ascii")).hexdigest() != digest:
            raise PropagationJobError("storage_unavailable")
        try:
            return json.loads(row["result_json"])
        except (TypeError, ValueError) as exc:
            raise PropagationJobError("storage_unavailable") from exc

    def child_result_reference(self, job_id: str, digest: str) -> dict[str, str | int]:
        with self._connection() as db:
            job = self._job(db, job_id)
            row = db.execute("SELECT pid,start_ticks,boot_id,result_digest,state FROM propagation_native_children WHERE job_id=?", (job_id,)).fetchone()
        if row is None or row["state"] != "completed" or row["result_digest"] != _digest(digest):
            raise PropagationJobError("launch_reconciliation_required")
        return {"job_id": job_id, "contract_digest": job["contract_digest"], "child_pid": row["pid"],
                "child_start_ticks": row["start_ticks"], "child_boot_id": row["boot_id"], "result_digest": digest}

    def completed_child_result(self, job_id: str, supervisor_identity: Mapping[str, Any]) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute("SELECT result_digest FROM propagation_native_children WHERE job_id=? AND state='completed'", (_id(job_id),)).fetchone()
        if row is None:
            return None
        with self._connection() as db:
            child = db.execute("SELECT pid,start_ticks,boot_id FROM propagation_native_children WHERE job_id=?", (job_id,)).fetchone()
        return self.load_child_result(job_id, supervisor_identity, {"pid": child["pid"], "start_ticks": child["start_ticks"], "boot_id": child["boot_id"]}, row["result_digest"])

    def record_result(self, job_id: str, identity: Mapping[str, Any], result: Mapping[str, Any], reconciliation: str = "uncertain", now: datetime | None = None) -> dict[str, Any]:
        stamp = _stamp(now or datetime.now(timezone.utc))
        if reconciliation not in {"applied", "failed", "cancelled", "uncertain"}:
            raise PropagationJobError("malformed_reconciliation")
        if not isinstance(result, Mapping) or set(result) != {"outcome", "native_effects", "quiescent"} or result["outcome"] not in {"applied", "failed", "cancelled", "uncertain"} or type(result["native_effects"]) is not list or type(result["quiescent"]) is not bool:
            raise PropagationJobError("malformed_result")
        result_digest = hashlib.sha256(_json(dict(result), limit=_MAX_CHILD_RESULT).encode("ascii")).hexdigest()
        payload = _json({"outcome": result["outcome"], "quiescent": result["quiescent"],
                         "native_effect_count": len(result["native_effects"]), "result_digest": result_digest})
        expected_effects = self.pending_effects(job_id)
        if result["outcome"] == "applied" and ({item["effect_id"] for item in result["native_effects"]} != {item["effect_id"] for item in expected_effects}
                                              or any(item["state"] != "applied" for item in result["native_effects"])):
            raise PropagationJobError("incomplete_native_effects")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["state"] not in {"executing", "cancellation_requested"} or (row["pid"], row["start_ticks"], row["boot_id"]) != (identity.get("pid"), identity.get("start_ticks"), identity.get("boot_id")):
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            state = "recovery_required"
            if result["quiescent"]:
                if result["outcome"] == "cancelled":
                    state = "cancelled" if reconciliation == "cancelled" else "recovery_required"
                elif result["outcome"] == "failed":
                    state = "failed" if reconciliation == "failed" else "recovery_required"
                elif result["outcome"] == "applied" and reconciliation == "applied":
                    state = "applied"
                elif result["outcome"] == "uncertain":
                    state = "recovery_required"
            convergence = None
            if state == "applied":
                convergence = hashlib.sha256(_json(["convergence/v1", row["contract_digest"], 1]).encode("ascii")).hexdigest()
            db.execute("UPDATE propagation_native_jobs SET state=?,result_json=?,completed_at=?,heartbeat_at=?,convergence_ref=? WHERE job_id=?",
                       (state, payload, stamp, stamp, convergence, job_id))
            if state in {"applied", "failed", "cancelled"}:
                db.execute("DELETE FROM propagation_native_resources WHERE job_id=?", (job_id,))
            row = self._job(db, job_id)
            db.execute("COMMIT")
        return self._row(row)

    def record_pre_profile_cancellation(self, job_id: str, identity: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
        """Close a cancellation that the durable pre-Popen gate refused.

        This narrow transition is not a substitute for uncertain native-child
        recovery: it is valid only when the registered supervisor identity is
        exact and no profile-child reservation was ever recorded.
        """
        stamp = _stamp(now or datetime.now(timezone.utc))
        if (not isinstance(identity, Mapping) or set(identity) != {"pid", "start_ticks", "boot_id"}
                or type(identity["pid"]) is not int or identity["pid"] < 1
                or type(identity["start_ticks"]) is not str or not identity["start_ticks"]
                or type(identity["boot_id"]) is not str or not identity["boot_id"]):
            raise PropagationJobError("malformed_process_identity")
        result = {"outcome": "cancelled", "native_effects": [], "quiescent": True}
        result_digest = hashlib.sha256(_json(result, limit=_MAX_CHILD_RESULT).encode("ascii")).hexdigest()
        payload = _json({"outcome": "cancelled", "quiescent": True, "native_effect_count": 0,
                         "result_digest": result_digest})
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            child = db.execute("SELECT 1 FROM propagation_native_children WHERE job_id=?", (_id(job_id),)).fetchone()
            if (row["state"] != "cancellation_requested" or row["result_json"] is not None or child is not None
                    or (row["pid"], row["start_ticks"], row["boot_id"])
                    != (identity["pid"], identity["start_ticks"], identity["boot_id"])):
                db.execute("ROLLBACK")
                raise PropagationJobError("launch_reconciliation_required")
            db.execute("UPDATE propagation_native_jobs SET state=?,result_json=?,completed_at=?,heartbeat_at=? WHERE job_id=?",
                       ("cancelled", payload, stamp, stamp, job_id))
            db.execute("DELETE FROM propagation_native_resources WHERE job_id=?", (job_id,))
            row = self._job(db, job_id)
            db.execute("COMMIT")
        return self._row(row)

    def cancellation_receipt(self, job_id: str, intent_id: str, contract_digest: str) -> dict | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM propagation_native_settlements WHERE job_id=?", (_id(job_id),)).fetchone()
            if row is None:
                return None
            if (row["intent_id"], row["contract_digest"]) != (_id(intent_id), _digest(contract_digest)):
                raise PropagationJobError("intent_conflict")
            if self._job(db, job_id)["state"] != "cancelled":
                raise PropagationJobError("settlement_conflict")
            return json.loads(row["receipt_json"])

    def _cancellation_binding(self, db, job_id):
        row = self._job(db, job_id)
        launch = db.execute("SELECT * FROM propagation_native_launches WHERE job_id=?", (job_id,)).fetchone()
        child = db.execute("SELECT * FROM propagation_native_children WHERE job_id=?", (job_id,)).fetchone()
        if row["state"] != "recovery_required" or launch is None or child is None or child["state"] not in {"registered", "completed"}:
            raise PropagationJobError("launch_reconciliation_required")
        identities = [{key: selected[key] for key in ("pid", "start_ticks", "boot_id")}
                      for selected in (row, child)]
        if (any(type(identity["pid"]) is not int or identity["pid"] < 1
                or any(type(identity[key]) is not str or not identity[key] for key in ("start_ticks", "boot_id"))
                for identity in identities)
                or any(row[key] != launch[key] for key in ("pid", "start_ticks", "boot_id"))
                or row["launch_token_digest"] != launch["token_digest"]):
            raise PropagationJobError("launch_reconciliation_required")
        resources = [item[0] for item in db.execute("SELECT resource_key FROM propagation_native_resources WHERE job_id=? ORDER BY resource_key", (job_id,))]
        if resources != json.loads(row["resources_json"]):
            raise PropagationJobError("resource_binding_mismatch")
        stored = [dict(selected) for selected in (row, launch, child)]
        stored[0]["canonical_contract"] = hashlib.sha256(bytes(row["canonical_contract"])).hexdigest()
        custody_digest = hashlib.sha256(_json(stored, limit=1024 * 1024).encode("ascii")).hexdigest()
        return {"job": self._row(row), "identities": identities, "custody_digest": custody_digest}

    def cancellation_binding(self, job_id: str) -> dict:
        with self._connection() as db:
            db.execute("BEGIN")
            return self._cancellation_binding(db, _id(job_id))

    def reconcile_cancelled(self, job_id: str, custody_digest: str, receipt: Mapping[str, Any]) -> dict:
        """Settle exact empty custody without rewriting the original child result."""
        payload = _json(dict(receipt))
        _digest(custody_digest)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT receipt_json FROM propagation_native_settlements WHERE job_id=?", (_id(job_id),)).fetchone()
            if existing is not None:
                if existing[0] != payload:
                    raise PropagationJobError("settlement_conflict")
                return json.loads(existing[0])
            binding = self._cancellation_binding(db, job_id)
            job = binding["job"]
            if (binding["custody_digest"] != custody_digest
                    or (receipt.get("job_id"), receipt.get("intent_id"), receipt.get("contract_digest"))
                    != (job_id, job["intent_id"], job["contract_digest"])
                    or receipt.get("quiescent") is not True or receipt.get("state") != "cancelled"):
                raise PropagationJobError("settlement_conflict")
            db.execute("INSERT INTO propagation_native_settlements VALUES(?,?,?,?)", (job_id, job["intent_id"], job["contract_digest"], payload))
            db.execute("UPDATE propagation_native_jobs SET state='cancelled' WHERE job_id=?", (job_id,))
            db.execute("DELETE FROM propagation_native_resources WHERE job_id=?", (job_id,))
            db.execute("COMMIT")
        return json.loads(payload)

    def reconcile_applied(self, job_id: str, result_digest: str, now: datetime | None = None) -> dict[str, Any]:
        """Record installed-owner readback after the original execution stopped.

        This does not launch or retry anything. Keep the original child result,
        effect IDs and process custody as evidence; release resource custody only
        after a matching durable quiescence fact and exact owner reconciliation.
        """
        stamp = _stamp(now or datetime.now(timezone.utc))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            summary = json.loads(row["result_json"]) if row["result_json"] else {}
            child = db.execute("SELECT state,result_digest,result_json FROM propagation_native_children WHERE job_id=?", (job_id,)).fetchone()
            if (row["state"] != "recovery_required" or summary.get("quiescent") is not True
                    or summary.get("result_digest") != result_digest or child is None
                    or child["state"] != "completed" or child["result_digest"] != result_digest
                    or hashlib.sha256(child["result_json"].encode("ascii")).hexdigest() != result_digest
                    or json.loads(child["result_json"]).get("quiescent") is not True):
                raise PropagationJobError("launch_reconciliation_required")
            convergence = hashlib.sha256(_json(["convergence/v1", row["contract_digest"], 1]).encode("ascii")).hexdigest()
            db.execute("UPDATE propagation_native_jobs SET state='applied',completed_at=?,heartbeat_at=?,convergence_ref=? WHERE job_id=?",
                       (stamp, stamp, convergence, job_id))
            db.execute("DELETE FROM propagation_native_resources WHERE job_id=?", (job_id,))
            row = self._job(db, job_id)
            db.execute("COMMIT")
        return self._public(row)

    def request_cancel(self, job_id: str, now: datetime | None = None) -> dict[str, Any]:
        _stamp(now or datetime.now(timezone.utc))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["state"] in _ACTIVE:
                db.execute("UPDATE propagation_native_jobs SET state=? WHERE job_id=?", ("cancellation_requested", job_id))
                row = self._job(db, job_id)
            db.execute("COMMIT")
        return self._row(row)

    def recovery_required(self, job_id: str, now: datetime | None = None) -> None:
        stamp = _stamp(now or datetime.now(timezone.utc))
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._job(db, job_id)
            if row["state"] not in _TERMINAL:
                db.execute("UPDATE propagation_native_jobs SET state=?,completed_at=? WHERE job_id=?", ("recovery_required", stamp, job_id))
            db.execute("COMMIT")
