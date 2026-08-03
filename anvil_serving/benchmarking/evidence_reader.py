"""Bounded retrieval for stage files referenced by durable job artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .jobs import BenchmarkJobError, resolve_owned_run_path


MAX_JOB_EVIDENCE_BYTES = 1024 * 1024


def _referenced_files(artifact: Mapping[str, Any]) -> dict[str, str]:
    evidence = artifact.get("results", {}).get("evidence", {})
    stages = evidence.get("stages", []) if isinstance(evidence, Mapping) else []
    references: dict[str, str] = {}
    for stage in stages if isinstance(stages, list) else []:
        for reference in stage.get("evidence", []) if isinstance(stage, Mapping) else []:
            if not isinstance(reference, Mapping):
                continue
            path = reference.get("path")
            digest = reference.get("sha256")
            if isinstance(path, str) and isinstance(digest, str):
                references[path] = digest
    return references


def read_referenced_job_evidence(
    store: Any, record: Mapping[str, Any], relative_path: str
) -> dict[str, Any]:
    """Read one digest-bound stage file named by the terminal job artifact."""
    if not isinstance(relative_path, str) or not relative_path:
        raise BenchmarkJobError("bad_evidence_path", "evidence path must be non-empty")
    artifact = store.artifact(record["spec"]["run_id"])
    if artifact is None:
        raise BenchmarkJobError("artifact_pending", "benchmark artifact is not available")
    references = _referenced_files(artifact)
    expected_digest = references.get(relative_path)
    if expected_digest is None:
        raise BenchmarkJobError(
            "unreferenced_evidence", "evidence path is not referenced by the job artifact"
        )
    path = Path(resolve_owned_run_path(
        store.run_root,
        ownership_id=record["spec"]["ownership_id"],
        run_id=record["spec"]["run_id"],
        relative=relative_path,
    ))
    if path.is_symlink() or not path.is_file():
        raise BenchmarkJobError("evidence_unavailable", "referenced evidence is unavailable")
    raw = path.read_bytes()
    if len(raw) > MAX_JOB_EVIDENCE_BYTES:
        raise BenchmarkJobError("evidence_too_large", "referenced evidence exceeds the read limit")
    observed_digest = hashlib.sha256(raw).hexdigest()
    if observed_digest != expected_digest:
        raise BenchmarkJobError(
            "evidence_digest_mismatch", "referenced evidence digest does not match"
        )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkJobError("bad_evidence_json", "referenced evidence is invalid JSON") from exc
    return {
        "path": relative_path,
        "bytes": len(raw),
        "sha256": observed_digest,
        "data": value,
    }
