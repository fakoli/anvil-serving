"""Fenced recovery for one registered successor that never admitted traffic.

This is deliberately not a synthetic managed-container anchor.  It preserves
the attempted run and coverage row, records why they are terminal, and accepts
only a complete full-proc proof that the registered PID namespace is extinct.
"""
from dataclasses import asdict, fields
import hashlib
from itertools import islice
import os
from pathlib import Path
import re
import sqlite3
import time

from . import container_owner as custody
from .keys import _private_json
from .maintenance import _publish
from .usage_store import RunOwner, _now, _utc, _validate_schema


_SCHEMA = "router-managed-startup-abort/v1"
_AUTH_SCHEMA = "router-startup-abort-authorization/v1"


def authorization(value, *, at=None):
    names = {"schema", "operation_id", "run_id", "configuration_revision",
             "roster_revision", "generation", "closure_sha256",
             "sidecar_snapshot_sha256", "acknowledged_at", "expires_at"}
    custody.require(type(value) is dict and set(value) == names
                    and value["schema"] == _AUTH_SCHEMA)
    for name in ("operation_id", "configuration_revision", "roster_revision",
                 "closure_sha256", "sidecar_snapshot_sha256"):
        custody.hex_id(value[name])
    from .usage_store import _uuid
    _uuid(value["run_id"])
    custody.require(type(value["generation"]) is int and 1 <= value["generation"] < 2**53
                    and type(value["acknowledged_at"]) is int
                    and type(value["expires_at"]) is int
                    and 0 < value["acknowledged_at"] < value["expires_at"]
                    <= value["acknowledged_at"] + 900)
    observed = int(time.time()) if at is None else at
    custody.require(type(observed) is int
                    and value["acknowledged_at"] <= observed <= value["expires_at"])
    return value


def _registration(value):
    names = {"owner_id", "domain_id", "run_id", "run_owner",
             "configuration_revision", "roster_revision", "store_binding_sha256",
             "segment_id", "started_at", "enabled"}
    custody.require(type(value) is dict and set(value) == names)
    from .usage_store import _id, _uuid
    _id(value["owner_id"]); _id(value["domain_id"]); _uuid(value["run_id"])
    _uuid(value["segment_id"]); _utc(value["started_at"])
    custody.require(type(value["enabled"]) is bool
                    and type(value["run_owner"]) is dict
                    and set(value["run_owner"]) == {f.name for f in fields(RunOwner)}
                    and all(type(item) is (str if name in {"host_domain_id", "boot_id"} else int)
                            and (bool(item) if type(item) is str else item >= 0)
                            for name, item in value["run_owner"].items()))
    owner = RunOwner(**value["run_owner"])
    custody.require(owner.host_domain_id == value["owner_id"]
                    and custody.roster(owner) == value["roster_revision"])
    for name in ("configuration_revision", "roster_revision", "store_binding_sha256"):
        custody.hex_id(value[name])
    return value


def _scan(value, owner):
    names = {"boot_id", "pid_namespace_device", "pid_namespace_inode",
             "processes_scanned", "inaccessible", "matches"}
    custody.require(type(value) is dict and set(value) == names
                    and value["boot_id"] == owner.boot_id
                    and value["pid_namespace_device"] == owner.pid_namespace_device
                    and value["pid_namespace_inode"] == owner.pid_namespace_inode
                    and type(value["processes_scanned"]) is int
                    and 1 <= value["processes_scanned"] <= 131072
                    and value["inaccessible"] == 0 and value["matches"] == 0)
    return value


def validate(record):
    names = {"schema", "phase", "sequence", "registration", "closure_sha256",
             "predecessor_transfers_sha256", "authorization", "authorization_sha256", "extinction",
             "ended_at", "implementation_sha256"}
    custody.require(type(record) is dict and set(record) == names
                    and record["schema"] == _SCHEMA
                    and record["phase"] in {"abort-pending", "aborted"}
                    and record["sequence"] == (0 if record["phase"] == "abort-pending" else 1))
    registration = _registration(record["registration"])
    auth = authorization(record["authorization"], at=record["authorization"]["acknowledged_at"])
    custody.require(custody.digest(auth) == record["authorization_sha256"]
                    and auth["run_id"] == registration["run_id"]
                    and auth["configuration_revision"] == registration["configuration_revision"]
                    and auth["roster_revision"] == registration["roster_revision"]
                    and auth["closure_sha256"] == record["closure_sha256"])
    owner = RunOwner(**registration["run_owner"])
    _scan(record["extinction"], owner); _utc(record["ended_at"])
    for name in ("closure_sha256", "predecessor_transfers_sha256",
                 "authorization_sha256", "implementation_sha256"):
        custody.hex_id(record[name])
    return record


def _records(store, target, expected_target=None):
    folder = custody.location(store, target).parent
    with os.scandir(folder) as entries:
        names = sorted(entry.name for entry in islice(entries, 1025))
    custody.require(len(names) <= 1024
                    and all(re.fullmatch(r"[0-9a-f-]{36}\.json", name) for name in names))
    target_name = target + ".json"
    if target_name in names:
        custody.require(expected_target is not None
                        and custody.digest(_private_json(folder / target_name)) == expected_target)
        names.remove(target_name)
    else:
        custody.require(expected_target is None)
    values = {}
    for name in names:
        value = _private_json(folder / name)
        custody.require(type(value) is dict)
        if value.get("schema") == _SCHEMA:
            value = validate(value)
            run_id = value["registration"]["run_id"]
        else:
            value = custody.validate(value)
            run_id = value["anchor"]["run_id"]
        custody.require(name == run_id + ".json")
        values[run_id] = value
    custody.require(len(values) == len(names))
    return values


def _candidate(usage, auth, *, expected_target=None, allow_dead=False):
    store = usage.key_store
    custody.require(getattr(store._writer_context, "offline_custody", False)
                    and usage.owner_config is not None
                    and usage.owner_config.router_owner_backend == "managed-container")
    binding = custody.binding(store)
    custody.require(binding["owner_id"] == usage.owner_config.router_owner_id)
    state = custody.closure(store)
    closure = state["closure"]
    custody.require(custody.digest(state) == auth["closure_sha256"]
                    and closure is not None and closure["consumed"] is False
                    and closure["configuration_revision"] == auth["configuration_revision"]
                    and closure["roster_revision"] == auth["roster_revision"]
                    and closure["generation"] == auth["generation"])
    with store._connect() as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        _validate_schema(db)
        row = db.execute("SELECT * FROM usage_runs WHERE run_id=?", (auth["run_id"],)).fetchone()
        coverage = db.execute("SELECT * FROM usage_coverage_segments WHERE run_id=? LIMIT 2",
                              (auth["run_id"],)).fetchall()
        starts = db.execute("SELECT COUNT(*) FROM usage_starts WHERE run_id=?", (auth["run_id"],)).fetchone()[0]
        domain = db.execute("SELECT configuration_revision FROM usage_domains WHERE domain_id=?",
                            (usage.owner_config.usage_domain_id,)).fetchone()
        custody.require(row is not None and len(coverage) == 1 and starts == 0
                        and row["domain_id"] == usage.owner_config.usage_domain_id
                        and coverage[0]["enabled"] in (0, 1)
                        and domain is not None and domain[0] == auth["configuration_revision"])
        owner = usage._run_owner(row)
        registration = {"owner_id": binding["owner_id"], "domain_id": row["domain_id"],
            "run_id": row["run_id"], "run_owner": asdict(owner),
            "configuration_revision": coverage[0]["configuration_revision"],
            "roster_revision": custody.roster(owner),
            "store_binding_sha256": custody.digest(binding), "segment_id": coverage[0]["segment_id"],
            "started_at": row["started_at"], "enabled": bool(coverage[0]["enabled"])}
        open_run = (row["state"] == "live" and row["ended_at"] is None
                    and coverage[0]["ended_at"] is None and coverage[0]["closure_reason"] is None
                    and coverage[0]["end_uncertain"] == 0)
        closed_abort = (allow_dead and row["state"] == "dead" and row["ended_at"] is not None
                        and coverage[0]["ended_at"] == row["ended_at"]
                        and coverage[0]["closure_reason"] == "owner_dead"
                        and coverage[0]["end_uncertain"] == 0)
        custody.require(registration["configuration_revision"] == auth["configuration_revision"]
                        and registration["roster_revision"] == auth["roster_revision"]
                        and coverage[0]["started_at"] == row["started_at"]
                        and (open_run or closed_abort))
        db.execute("ROLLBACK")
    records = _records(store, auth["run_id"], expected_target)
    snapshot = custody.digest({run + ".json": custody.digest(value) for run, value in records.items()})
    custody.require(snapshot == auth["sidecar_snapshot_sha256"])
    transfers = []
    for value in records.values():
        transfer = value.get("transfer")
        if transfer is not None and transfer["successor_run_id"] == auth["run_id"]:
            custody.require(transfer["successor_configuration_revision"] == auth["configuration_revision"]
                            and transfer["successor_roster_revision"] == auth["roster_revision"]
                            and transfer["successor_generation"] == auth["generation"])
            transfers.append(custody.digest(value))
        elif transfer is not None:
            successor = records.get(transfer["successor_run_id"])
            identity = custody.successor_identity(successor) if successor is not None else None
            custody.require(identity is not None and all(
                transfer["successor_" + name] == identity[name]
                for name in ("configuration_revision", "roster_revision")))
    custody.require(bool(transfers))
    return registration, custody.digest(sorted(transfers)), owner


def _database_state(usage, registration, ended_at, *, apply):
    with usage._write() as db:
        row = db.execute("SELECT * FROM usage_runs WHERE run_id=?", (registration["run_id"],)).fetchone()
        coverage = db.execute("SELECT * FROM usage_coverage_segments WHERE segment_id=?",
                              (registration["segment_id"],)).fetchone()
        custody.require(row is not None and coverage is not None
                        and usage._run_owner(row) == RunOwner(**registration["run_owner"])
                        and row["run_id"] == registration["run_id"]
                        and row["domain_id"] == registration["domain_id"]
                        and row["started_at"] == registration["started_at"]
                        and coverage["run_id"] == registration["run_id"]
                        and coverage["domain_id"] == registration["domain_id"]
                        and coverage["configuration_revision"] == registration["configuration_revision"]
                        and coverage["started_at"] == registration["started_at"]
                        and coverage["enabled"] == int(registration["enabled"])
                        and db.execute("SELECT COUNT(*) FROM usage_starts WHERE run_id=?",
                                       (registration["run_id"],)).fetchone()[0] == 0)
        if row["state"] == "live":
            custody.require(row["ended_at"] is None and coverage["ended_at"] is None
                            and coverage["closure_reason"] is None and coverage["end_uncertain"] == 0)
            if apply:
                db.execute("UPDATE usage_runs SET state='dead',ended_at=? WHERE run_id=?",
                           (ended_at, registration["run_id"]))
                db.execute("UPDATE usage_coverage_segments SET ended_at=?,closure_reason='owner_dead',"
                           "end_uncertain=0 WHERE segment_id=?", (ended_at, registration["segment_id"]))
                usage._revision(db, registration["domain_id"])
        else:
            custody.require(row["state"] == "dead" and row["ended_at"] == ended_at
                            and coverage["ended_at"] == ended_at
                            and coverage["closure_reason"] == "owner_dead"
                            and coverage["end_uncertain"] == 0)


def preview_startup_abort(usage, run_id):
    """Read the exact authorization inputs while the caller holds both fences."""
    from .usage_store import _uuid
    _uuid(run_id)
    store = usage.key_store
    custody.require(getattr(store._writer_context, "offline_custody", False))
    path = custody.location(store, run_id)
    if os.path.lexists(path):
        record = validate(_private_json(path))
        auth = record["authorization"]
        registration, transfers, owner = _candidate(
            usage, auth, expected_target=custody.digest(record), allow_dead=True)
        custody.require(registration == record["registration"]
                        and transfers == record["predecessor_transfers_sha256"])
        return {"schema": "router-startup-abort-preview/v1",
                **{name: auth[name] for name in ("run_id", "configuration_revision",
                    "roster_revision", "generation", "closure_sha256", "sidecar_snapshot_sha256")},
                "run_owner": asdict(owner), "registration_sha256": custody.digest(registration),
                "predecessor_transfers_sha256": transfers}
    state = custody.closure(store)
    closure = state["closure"]
    custody.require(closure is not None and closure["consumed"] is False)
    with store._connect() as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM usage_runs WHERE run_id=?", (run_id,)).fetchone()
        coverage = db.execute("SELECT configuration_revision FROM usage_coverage_segments "
                              "WHERE run_id=? LIMIT 2", (run_id,)).fetchall()
        custody.require(row is not None and len(coverage) == 1)
        owner = usage._run_owner(row)
    records = _records(store, run_id)
    snapshot = custody.digest({rid + ".json": custody.digest(value) for rid, value in records.items()})
    auth = {"run_id": run_id, "configuration_revision": coverage[0][0],
            "roster_revision": custody.roster(owner), "generation": closure["generation"],
            "closure_sha256": custody.digest(state), "sidecar_snapshot_sha256": snapshot}
    registration, transfers, checked_owner = _candidate(usage, auth)
    custody.require(checked_owner == owner)
    return {"schema": "router-startup-abort-preview/v1", **auth,
            "run_owner": asdict(owner), "registration_sha256": custody.digest(registration),
            "predecessor_transfers_sha256": transfers}


def abort_startup(usage, authorization_value, *, namespace_scan):
    """Finalize one acknowledged never-admitted registration under offline custody."""
    store = usage.key_store
    custody.require(type(authorization_value) is dict and type(authorization_value.get("run_id")) is str)
    path = custody.location(store, authorization_value["run_id"])
    existing = _private_json(path) if os.path.lexists(path) else None
    auth = authorization(authorization_value,
                         at=authorization_value.get("acknowledged_at") if existing else None)
    if existing is not None:
        record = validate(existing)
        custody.require(record["authorization"] == auth
                        and record["authorization_sha256"] == custody.digest(auth))
        registration = record["registration"]
        owner = RunOwner(**registration["run_owner"])
        _scan(namespace_scan(owner), owner)
        current_registration, transfers, current_owner = _candidate(
            usage, auth, expected_target=custody.digest(record), allow_dead=True)
        custody.require(current_registration == registration
                        and transfers == record["predecessor_transfers_sha256"]
                        and current_owner == owner)
        _database_state(usage, registration, record["ended_at"], apply=True)
        if record["phase"] == "aborted":
            return record
        finalized = {**record, "phase": "aborted", "sequence": 1}
        _publish(path, finalized, expected=custody.digest(record))
        return finalized

    registration, transfers, owner = _candidate(usage, auth)
    extinction = _scan(namespace_scan(owner), owner)
    ended_at = _now()
    custody.require(_utc(ended_at) >= _utc(registration["started_at"]))
    record = {"schema": _SCHEMA, "phase": "abort-pending", "sequence": 0,
              "registration": registration, "closure_sha256": auth["closure_sha256"],
              "predecessor_transfers_sha256": transfers,
              "authorization": auth, "authorization_sha256": custody.digest(auth), "extinction": extinction,
              "ended_at": ended_at,
              "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    validate(record)
    _publish(path, record)
    _scan(namespace_scan(owner), owner)
    current_registration, current_transfers, current_owner = _candidate(
        usage, auth, expected_target=custody.digest(record))
    custody.require(current_registration == registration and current_transfers == transfers
                    and current_owner == owner)
    _database_state(usage, registration, ended_at, apply=True)
    _scan(namespace_scan(owner), owner)
    finalized = {**record, "phase": "aborted", "sequence": 1}
    _publish(path, finalized, expected=custody.digest(record))
    return finalized
