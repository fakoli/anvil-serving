"""Portable benchmark-job contracts and ownership-safe state transitions."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import re
import urllib.parse
from collections.abc import Mapping
from typing import Any

from .artifacts import is_filesystem_root, path_is_within, real_path
from .limits import (
    MAX_BENCHMARK_JOB_ID_CHARS,
    MAX_BENCHMARK_JOB_JSON_DEPTH,
    MAX_BENCHMARK_JOB_JSON_ITEMS,
    MAX_BENCHMARK_JOB_LOG_BYTES,
    MAX_BENCHMARK_JOB_LOG_ENTRIES,
    MAX_BENCHMARK_JOB_LOG_MESSAGE_CHARS,
    MAX_BENCHMARK_JOB_SECONDS,
    MAX_BENCHMARK_JOB_SPEC_BYTES,
)


JOB_SPEC_SCHEMA = "anvil-serving.benchmark-job-spec/v1"
JOB_RECORD_SCHEMA = "anvil-serving.benchmark-job/v1"
JOB_ARTIFACT_SCHEMA = "anvil-serving.benchmark-result/v1"

JOB_STATES = frozenset({
    "queued",
    "running",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
})
TERMINAL_JOB_STATES = frozenset({"completed", "failed", "cancelled"})
JOB_TRANSITIONS = {
    "queued": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"cancelling", "completed", "failed", "cancelled"}),
    "cancelling": frozenset({"cancelled", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,%d}$" % (
    MAX_BENCHMARK_JOB_ID_CHARS - 1
))
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_SECRET_KEY_RE = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|password|secret|credential)",
    re.IGNORECASE,
)
_ALLOWED_SPEC_KEYS = frozenset({
    "schema",
    "run_id",
    "ownership_id",
    "suite",
    "profile",
    "endpoint",
    "worker",
    "submitted_at",
    "timeout_s",
    "parameters",
})
_ALLOWED_ENDPOINT_KEYS = frozenset({"base_url", "model", "auth_env"})
_ALLOWED_WORKER_KEYS = frozenset({"id"})


class BenchmarkJobError(Exception):
    """Typed validation or lifecycle failure for a benchmark job."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def utc_now() -> str:
    """Return a stable UTC timestamp suitable for artifacts and job state."""
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_timestamp(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BenchmarkJobError("bad_timestamp", f"{field} must be an RFC3339 timestamp")
    text = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BenchmarkJobError(
            "bad_timestamp", f"{field} must be an RFC3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BenchmarkJobError("bad_timestamp", f"{field} must include a UTC offset")
    return text


def _validate_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise BenchmarkJobError(
            "bad_identifier",
            f"{field} must be 1-{MAX_BENCHMARK_JOB_ID_CHARS} portable characters",
            {"field": field},
        )
    return value


def _validate_closed_mapping(
    value: Any,
    *,
    field: str,
    allowed: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BenchmarkJobError("bad_spec", f"{field} must be an object")
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise BenchmarkJobError(
            "bad_spec",
            f"{field} contains unsupported fields",
            {"field": field, "unsupported": unexpected},
        )
    return dict(value)


def _validate_endpoint(value: Any) -> dict[str, str]:
    endpoint = _validate_closed_mapping(
        value, field="endpoint", allowed=_ALLOWED_ENDPOINT_KEYS
    )
    base_url = endpoint.get("base_url")
    model = endpoint.get("model")
    if not isinstance(base_url, str) or not base_url.strip():
        raise BenchmarkJobError("bad_endpoint", "endpoint.base_url must be non-empty")
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise BenchmarkJobError("bad_endpoint", "endpoint.base_url must be HTTP(S)")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise BenchmarkJobError(
            "bad_endpoint",
            "endpoint.base_url must not contain credentials, query, or fragment",
        )
    if parsed.hostname.lower() == "localhost":
        raise BenchmarkJobError(
            "bad_endpoint", "endpoint.base_url must use 127.0.0.1 instead of localhost"
        )
    if not isinstance(model, str) or not model.strip() or len(model) > 512:
        raise BenchmarkJobError("bad_endpoint", "endpoint.model must be 1-512 characters")
    result = {"base_url": base_url.strip().rstrip("/"), "model": model.strip()}
    auth_env = endpoint.get("auth_env")
    if auth_env is not None:
        if not isinstance(auth_env, str) or not _ENV_RE.fullmatch(auth_env):
            raise BenchmarkJobError(
                "bad_endpoint", "endpoint.auth_env must name an environment variable"
            )
        result["auth_env"] = auth_env
    return result


def _validate_worker(value: Any) -> dict[str, str]:
    worker = _validate_closed_mapping(value, field="worker", allowed=_ALLOWED_WORKER_KEYS)
    return {"id": _validate_id(worker.get("id"), field="worker.id")}


def _validate_json_value(
    value: Any,
    *,
    path: str,
    depth: int = 0,
    counter: list[int] | None = None,
) -> Any:
    if counter is None:
        counter = [0]
    if depth > MAX_BENCHMARK_JOB_JSON_DEPTH:
        raise BenchmarkJobError("bad_spec", f"{path} exceeds the nesting limit")
    counter[0] += 1
    if counter[0] > MAX_BENCHMARK_JOB_JSON_ITEMS:
        raise BenchmarkJobError("bad_spec", "parameters exceed the item limit")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise BenchmarkJobError("bad_spec", f"{path} must be a finite number")
        return value
    if isinstance(value, str):
        if "\x00" in value or len(value) > MAX_BENCHMARK_JOB_LOG_MESSAGE_CHARS:
            raise BenchmarkJobError("bad_spec", f"{path} contains invalid or oversized text")
        return value
    if isinstance(value, list):
        return [
            _validate_json_value(
                item, path=f"{path}[{index}]", depth=depth + 1, counter=counter
            )
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise BenchmarkJobError("bad_spec", f"{path} has an invalid key")
            if _SECRET_KEY_RE.search(key):
                raise BenchmarkJobError(
                    "secret_in_spec", f"{path}.{key} cannot carry credential material"
                )
            result[key] = _validate_json_value(
                item, path=f"{path}.{key}", depth=depth + 1, counter=counter
            )
        return result
    raise BenchmarkJobError("bad_spec", f"{path} contains a non-JSON value")


def canonical_json_bytes(value: Any) -> bytes:
    """Return the stable representation used to bind jobs and artifacts."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def validate_job_spec(value: Any) -> dict[str, Any]:
    """Validate and normalize a portable benchmark job specification."""
    spec = _validate_closed_mapping(value, field="job spec", allowed=_ALLOWED_SPEC_KEYS)
    if spec.get("schema") != JOB_SPEC_SCHEMA:
        raise BenchmarkJobError(
            "bad_schema", f"job spec schema must be {JOB_SPEC_SCHEMA!r}"
        )
    normalized = {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": _validate_id(spec.get("run_id"), field="run_id"),
        "ownership_id": _validate_id(spec.get("ownership_id"), field="ownership_id"),
        "suite": _validate_id(spec.get("suite"), field="suite"),
        "profile": _validate_id(spec.get("profile"), field="profile"),
        "endpoint": _validate_endpoint(spec.get("endpoint")),
        "worker": _validate_worker(spec.get("worker")),
        "submitted_at": _validate_timestamp(spec.get("submitted_at"), field="submitted_at"),
    }
    timeout_s = spec.get("timeout_s")
    if not isinstance(timeout_s, int) or isinstance(timeout_s, bool):
        raise BenchmarkJobError("bad_timeout", "timeout_s must be an integer")
    if not 1 <= timeout_s <= MAX_BENCHMARK_JOB_SECONDS:
        raise BenchmarkJobError(
            "bad_timeout", f"timeout_s must be between 1 and {MAX_BENCHMARK_JOB_SECONDS}"
        )
    normalized["timeout_s"] = timeout_s
    normalized["parameters"] = _validate_json_value(
        spec.get("parameters", {}), path="parameters"
    )
    encoded = canonical_json_bytes(normalized)
    if len(encoded) > MAX_BENCHMARK_JOB_SPEC_BYTES:
        raise BenchmarkJobError(
            "spec_too_large",
            f"job spec exceeds {MAX_BENCHMARK_JOB_SPEC_BYTES} bytes",
        )
    return normalized


def job_spec_sha256(spec: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(validate_job_spec(spec))).hexdigest()


def new_job_record(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Create the initial durable job record for a validated specification."""
    normalized = validate_job_spec(spec)
    submitted_at = normalized["submitted_at"]
    return {
        "schema": JOB_RECORD_SCHEMA,
        "spec": normalized,
        "spec_sha256": hashlib.sha256(canonical_json_bytes(normalized)).hexdigest(),
        "state": "queued",
        "revision": 1,
        "submitted_at": submitted_at,
        "updated_at": submitted_at,
        "started_at": None,
        "finished_at": None,
        "cancel_requested_at": None,
        "failure": None,
        "artifact": None,
        "logs": {"next_cursor": 0, "retained_from": 0, "truncated": False, "entries": []},
    }


def validate_job_record(value: Any) -> dict[str, Any]:
    """Validate the stable invariants needed before mutating persisted state."""
    if not isinstance(value, Mapping) or value.get("schema") != JOB_RECORD_SCHEMA:
        raise BenchmarkJobError("bad_job_record", "job record has an unsupported schema")
    record = copy.deepcopy(dict(value))
    record["spec"] = validate_job_spec(record.get("spec"))
    expected_digest = hashlib.sha256(canonical_json_bytes(record["spec"])).hexdigest()
    if record.get("spec_sha256") != expected_digest:
        raise BenchmarkJobError("spec_digest_mismatch", "job spec digest does not match")
    state = record.get("state")
    if state not in JOB_STATES:
        raise BenchmarkJobError("bad_job_state", f"unsupported job state: {state!r}")
    revision = record.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise BenchmarkJobError("bad_job_record", "job revision must be a positive integer")
    for field in ("submitted_at", "updated_at"):
        record[field] = _validate_timestamp(record.get(field), field=field)
    return record


def transition_job(
    value: Mapping[str, Any],
    target: str,
    *,
    timestamp: str | None = None,
    failure: Mapping[str, Any] | None = None,
    artifact: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a transitioned copy, rejecting illegal or contradictory states."""
    record = validate_job_record(value)
    if target not in JOB_STATES:
        raise BenchmarkJobError("bad_job_state", f"unsupported job state: {target!r}")
    current = record["state"]
    if target == current:
        return record
    if target not in JOB_TRANSITIONS[current]:
        raise BenchmarkJobError(
            "illegal_job_transition",
            f"job cannot transition from {current!r} to {target!r}",
            {"from": current, "to": target},
        )
    now = _validate_timestamp(timestamp or utc_now(), field="transition timestamp")
    record["state"] = target
    record["revision"] += 1
    record["updated_at"] = now
    if target == "running":
        record["started_at"] = now
    if target == "cancelling":
        record["cancel_requested_at"] = now
    if target in TERMINAL_JOB_STATES:
        record["finished_at"] = now
    if failure is not None:
        record["failure"] = _validate_json_value(dict(failure), path="failure")
    if target == "completed" and record.get("failure") is not None:
        raise BenchmarkJobError("bad_job_record", "completed jobs cannot retain a failure")
    if target == "failed" and record.get("failure") is None:
        raise BenchmarkJobError("bad_job_record", "failed jobs require failure details")
    if artifact is not None:
        record["artifact"] = _validate_json_value(dict(artifact), path="artifact")
    return record


def _one_line(value: Any, *, limit: int) -> str:
    text = " ".join(str(value).replace("\x00", "").splitlines()).strip()
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def append_job_log(
    value: Mapping[str, Any],
    *,
    level: str,
    message: Any,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Append a bounded cursor-addressable log entry to a copied job record."""
    record = validate_job_record(value)
    logs = copy.deepcopy(record.get("logs"))
    if not isinstance(logs, dict) or not isinstance(logs.get("entries"), list):
        raise BenchmarkJobError("bad_job_record", "job logs have an invalid shape")
    cursor = logs.get("next_cursor")
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        raise BenchmarkJobError("bad_job_record", "job log cursor is invalid")
    entry = {
        "cursor": cursor,
        "timestamp": _validate_timestamp(timestamp or utc_now(), field="log timestamp"),
        "level": _one_line(level, limit=32).lower() or "info",
        "message": _one_line(message, limit=MAX_BENCHMARK_JOB_LOG_MESSAGE_CHARS),
    }
    entries = list(logs["entries"])
    entries.append(entry)
    logs["next_cursor"] = cursor + 1
    while entries and (
        len(entries) > MAX_BENCHMARK_JOB_LOG_ENTRIES
        or len(canonical_json_bytes(entries)) > MAX_BENCHMARK_JOB_LOG_BYTES
    ):
        entries.pop(0)
        logs["truncated"] = True
    logs["entries"] = entries
    logs["retained_from"] = entries[0]["cursor"] if entries else logs["next_cursor"]
    record["logs"] = logs
    record["revision"] += 1
    record["updated_at"] = entry["timestamp"]
    return record


def build_artifact_envelope(
    value: Mapping[str, Any],
    *,
    results: Mapping[str, Any] | None = None,
    failure: Mapping[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build the common provenance envelope for complete and partial results."""
    record = validate_job_record(value)
    spec = record["spec"]
    status = record["state"]
    envelope = {
        "schema": JOB_ARTIFACT_SCHEMA,
        "run": {
            "run_id": spec["run_id"],
            "ownership_id": spec["ownership_id"],
            "suite": spec["suite"],
            "profile": spec["profile"],
            "spec_sha256": record["spec_sha256"],
        },
        "provenance": {
            "endpoint": copy.deepcopy(spec["endpoint"]),
            "worker": copy.deepcopy(spec["worker"]),
            "submitted_at": spec["submitted_at"],
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
        },
        "status": status,
        "partial": status != "completed",
        "created_at": _validate_timestamp(created_at or utc_now(), field="created_at"),
        "results": _validate_json_value(dict(results or {}), path="results"),
        "failure": _validate_json_value(
            dict(failure or record.get("failure") or {}), path="failure"
        ) or None,
    }
    return envelope


def resolve_owned_run_path(
    root: str,
    *,
    ownership_id: str,
    run_id: str,
    relative: str | None = None,
) -> str:
    """Resolve a path beneath ``root/<owner>/<run>`` without traversal escapes."""
    root_path = real_path(root)
    if is_filesystem_root(root_path) or not os.path.isdir(root_path):
        raise BenchmarkJobError(
            "bad_run_root", "benchmark run root must be an existing non-root directory"
        )
    owner = _validate_id(ownership_id, field="ownership_id")
    run = _validate_id(run_id, field="run_id")
    run_root = real_path(os.path.join(root_path, owner, run))
    if not path_is_within(run_root, root_path):
        raise BenchmarkJobError("unsafe_run_path", "run path escapes the benchmark root")
    if relative is None:
        return run_root
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise BenchmarkJobError("unsafe_run_path", "relative run path is invalid")
    candidate = real_path(relative, base=run_root)
    if not path_is_within(candidate, run_root):
        raise BenchmarkJobError("unsafe_run_path", "relative run path escapes its owned run")
    return candidate
