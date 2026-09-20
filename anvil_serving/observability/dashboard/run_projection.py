"""Bounded public run projections for declared dashboard owners."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from ...benchmarking.jobs import BenchmarkJobError
from ...control_plane.controller.store import benchmark_job_ref
from .contracts import ObservatoryError, digest, identifier


BENCHMARK_SOURCE = "benchmark"
MAX_NATIVE_ID_BYTES = 1024


def validated_benchmark_job_ref(value: object) -> dict[str, str] | None:
    """Accept only the closed owner-issued benchmark correlation contract."""
    if not isinstance(value, Mapping) or set(value) != {
        "schema", "issuer", "namespace", "native_id",
    }:
        return None
    try:
        expected = benchmark_job_ref(value["issuer"], value["native_id"])
    except BenchmarkJobError:
        return None
    return expected if dict(value) == expected else None


def benchmark_correlation(issuer: object, native_id: object) -> dict[str, str] | None:
    """Derive a benchmark reference from the trusted list source and native ID."""
    try:
        return benchmark_job_ref(issuer, native_id)
    except BenchmarkJobError:
        return None


def benchmark_correlation_id(value: object) -> str | None:
    """Project a validated closed reference into one compact browser join key."""
    reference = validated_benchmark_job_ref(value)
    return "benchmark-job-" + digest(reference) if reference is not None else None


def projected_run_id(owner_id: str, source: str, native_id: str) -> str:
    """Return a deterministic namespaced ID without interpreting owner-native text."""
    identifier(owner_id)
    identifier(source)
    if type(native_id) is not str or not native_id:
        raise ObservatoryError("invalid_run", "The owner returned an invalid run.")
    try:
        encoded = native_id.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ObservatoryError("invalid_run", "The owner returned an invalid run.") from exc
    if len(encoded) > MAX_NATIVE_ID_BYTES or any(
        ord(char) < 32 or 127 <= ord(char) <= 159 for char in native_id
    ):
        raise ObservatoryError("invalid_run", "The owner returned an invalid run.")
    return "run-" + digest({"owner_id": owner_id, "source": source, "native_id": native_id})


def list_benchmark_runs(
    adapter: Any,
    *,
    can_read: Callable[[str], bool],
    resource_id: str,
    limit: int = 100,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Authorize before asking the benchmark owner for rows, cursors, or counts."""
    resource_id = identifier(resource_id)
    if not callable(can_read) or can_read(resource_id) is not True:
        raise ObservatoryError("forbidden", "You are not authorized to read this run source.", 403)
    response = adapter.list_benchmark_jobs(limit=limit, cursor=cursor)
    if not isinstance(response, Mapping) or response.get("schema") != "anvil-serving.benchmark-job-list/v1":
        raise ObservatoryError("owner_unavailable", "The benchmark owner returned no valid run list.", 503)
    items = response.get("items")
    source = response.get("source")
    if not isinstance(items, list) or len(items) > 100 or not isinstance(source, Mapping):
        raise ObservatoryError("owner_unavailable", "The benchmark owner returned no valid run list.", 503)
    owner_id = source.get("id")
    try:
        owner_id = identifier(owner_id)
        observed_at = source.get("observed_at")
        if source.get("status") != "fresh" or type(observed_at) is not str:
            raise ValueError
        datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        deadline = source.get("deadline_seconds")
        if type(deadline) not in {int, float} or isinstance(deadline, bool) or not 0 < deadline <= 2:
            raise ValueError
        partial = source.get("partial", False)
        if type(partial) is not bool:
            raise ValueError
    except (TypeError, ValueError, ObservatoryError):
        raise ObservatoryError("owner_unavailable", "The benchmark owner returned no fresh run list.", 503) from None
    projected = [
        _project_benchmark_row(
            row, owner_id=owner_id, resource_id=resource_id, observed_at=observed_at,
        )
        for row in items
    ]
    next_cursor = response.get("next_cursor")
    if next_cursor is not None and (type(next_cursor) is not str or len(next_cursor) > 128):
        raise ObservatoryError("owner_unavailable", "The benchmark owner returned no valid run list.", 503)
    return {
        "items": projected,
        "next_cursor": next_cursor,
        "sources": [{
            "id": owner_id,
            "status": "fresh",
            "observed_at": observed_at,
            "deadline_seconds": deadline,
            "truncated": source.get("truncated") is True,
            "partial": partial,
        }],
    }


def _project_benchmark_row(
    row: object, *, owner_id: str, resource_id: str, observed_at: str,
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ObservatoryError("owner_unavailable", "The benchmark owner returned no valid run list.", 503)
    native_id = row.get("native_id")
    native_state = row.get("native_state")
    required_text = ("suite", "profile", "model", "submitted_at", "updated_at")
    if type(native_state) is not str or any(type(row.get(key)) is not str for key in required_text):
        raise ObservatoryError("owner_unavailable", "The benchmark owner returned no valid run list.", 503)
    artifact = row.get("artifact")
    evidence_refs = []
    if isinstance(artifact, Mapping) and type(artifact.get("sha256")) is str:
        evidence_refs.append({
            "owner_id": owner_id,
            "artifact_id": native_id,
            "sha256": artifact["sha256"],
        })
    return {
        "id": projected_run_id(owner_id, BENCHMARK_SOURCE, native_id),
        "owner_id": owner_id,
        "source": BENCHMARK_SOURCE,
        "native_id": native_id,
        "kind": "benchmark",
        "resource_id": resource_id,
        "title": row["suite"] + " benchmark",
        "suite": row["suite"],
        "profile": row["profile"],
        "model": row["model"],
        "native_state": native_state,
        "status": native_state,
        "submitted_at": row["submitted_at"],
        "updated_at": row["updated_at"],
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "observed_at": observed_at,
        "freshness": "fresh",
        # Never forward owner-supplied bare correlation strings. The list
        # source identity and durable job-native ID form the only benchmark ref.
        "correlation_id": benchmark_correlation_id(
            benchmark_correlation(owner_id, native_id)
        ),
        "evidence_refs": evidence_refs,
    }
