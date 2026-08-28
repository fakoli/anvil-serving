"""SQLite-backed durable, idempotent media jobs."""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any, Mapping

from .contracts import JobEvent, JobState, MediaArtifact, MediaJob, utc_now
from .errors import MediaError


SCHEMA_VERSION = 1
MAX_IDEMPOTENCY_KEY = 128


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def _time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MediaError("job_store_corrupt", "stored job timestamp is invalid", status=500) from exc
    if parsed.tzinfo is None:
        raise MediaError("job_store_corrupt", "stored job timestamp lacks a timezone", status=500)
    return parsed.astimezone(dt.timezone.utc)


class MediaJobStore:
    """Own durable job truth without storing prompts or generated media bytes."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)
        if not self.path.parent.is_dir():
            raise MediaError("job_store_unavailable", "media job-state directory does not exist", status=500)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_schema (
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS media_jobs (
                    id TEXT PRIMARY KEY,
                    principal TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    input_digest TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    backend_prompt_id TEXT NOT NULL DEFAULT '',
                    approval_json TEXT,
                    UNIQUE(principal, workflow_id, workflow_version, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS media_job_events (
                    job_id TEXT NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(job_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS media_job_artifacts (
                    job_id TEXT NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
                    artifact_json TEXT NOT NULL,
                    PRIMARY KEY(job_id, artifact_json)
                );
                """
            )
            rows = db.execute("SELECT version FROM media_schema").fetchall()
            if not rows:
                db.execute("INSERT INTO media_schema(version) VALUES (?)", (SCHEMA_VERSION,))
            elif len(rows) != 1 or rows[0]["version"] != SCHEMA_VERSION:
                raise MediaError("job_store_schema", "media job-state schema version is unsupported", status=500)

    def create(
        self,
        *,
        principal: str,
        workflow_id: str,
        workflow_version: str,
        input_digest: str,
        idempotency_key: str,
        now: dt.datetime | None = None,
    ) -> tuple[MediaJob, bool]:
        if not isinstance(idempotency_key, str) or not idempotency_key or len(idempotency_key) > MAX_IDEMPOTENCY_KEY:
            raise MediaError("invalid_idempotency_key", "idempotency key is outside policy")
        if not isinstance(input_digest, str) or len(input_digest) != 64:
            raise MediaError("invalid_input_digest", "input digest is invalid")
        created = now or utc_now()
        job_id = "job_" + secrets.token_urlsafe(24)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id, input_digest FROM media_jobs WHERE principal=? AND workflow_id=? AND workflow_version=? AND idempotency_key=?",
                (principal, workflow_id, workflow_version, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["input_digest"] != input_digest:
                    db.execute("ROLLBACK")
                    raise MediaError(
                        "idempotency_conflict",
                        "idempotency key was already used with different inputs",
                        status=409,
                    )
                db.execute("COMMIT")
                return self.get(existing["id"], principal=principal), False
            db.execute(
                "INSERT INTO media_jobs(id,principal,workflow_id,workflow_version,state,created_at,updated_at,input_digest,idempotency_key) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    principal,
                    workflow_id,
                    workflow_version,
                    JobState.ACCEPTED.value,
                    _iso(created),
                    _iso(created),
                    input_digest,
                    idempotency_key,
                ),
            )
            db.execute(
                "INSERT INTO media_job_events(job_id,sequence,state,at,reason) VALUES (?,?,?,?,?)",
                (job_id, 1, JobState.ACCEPTED.value, _iso(created), ""),
            )
            db.execute("COMMIT")
        return self.get(job_id, principal=principal), True

    def get(self, job_id: str, *, principal: str | None = None, allow_cross_principal: bool = False) -> MediaJob:
        with self._connect() as db:
            row = db.execute("SELECT * FROM media_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or (principal is not None and row["principal"] != principal and not allow_cross_principal):
                raise MediaError("job_not_found", "media job was not found", status=404)
            events = db.execute(
                "SELECT sequence,state,at,reason FROM media_job_events WHERE job_id=? ORDER BY sequence",
                (job_id,),
            ).fetchall()
            artifacts_raw = db.execute(
                "SELECT artifact_json FROM media_job_artifacts WHERE job_id=? ORDER BY artifact_json",
                (job_id,),
            ).fetchall()
        artifacts = tuple(_artifact_from_json(item["artifact_json"]) for item in artifacts_raw)
        approval = json.loads(row["approval_json"]) if row["approval_json"] else None
        return MediaJob(
            id=row["id"],
            principal=row["principal"],
            workflow_id=row["workflow_id"],
            workflow_version=row["workflow_version"],
            state=JobState(row["state"]),
            created_at=_time(row["created_at"]),
            updated_at=_time(row["updated_at"]),
            events=tuple(JobEvent(item["sequence"], JobState(item["state"]), _time(item["at"]), item["reason"]) for item in events),
            artifacts=artifacts,
            approval=approval,
            backend_prompt_id=row["backend_prompt_id"],
            input_digest=row["input_digest"],
        )

    def transition(
        self,
        job_id: str,
        state: JobState,
        *,
        principal: str,
        reason: str = "",
        approval: Mapping[str, Any] | None = None,
        now: dt.datetime | None = None,
    ) -> MediaJob:
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.get(job_id, principal=principal)
            changed = current.transition(state, reason=reason, at=now)
            approval_json = (
                json.dumps(dict(approval), sort_keys=True, separators=(",", ":"), allow_nan=False)
                if approval is not None
                else (json.dumps(dict(current.approval), sort_keys=True, separators=(",", ":")) if current.approval else None)
            )
            db.execute(
                "UPDATE media_jobs SET state=?,updated_at=?,approval_json=? WHERE id=?",
                (changed.state.value, _iso(changed.updated_at), approval_json, job_id),
            )
            event = changed.events[-1]
            db.execute(
                "INSERT INTO media_job_events(job_id,sequence,state,at,reason) VALUES (?,?,?,?,?)",
                (job_id, event.sequence, event.state.value, _iso(event.at), event.reason),
            )
            db.execute("COMMIT")
        return self.get(job_id, principal=principal)

    def set_backend_prompt(self, job_id: str, prompt_id: str, *, principal: str) -> MediaJob:
        if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 128:
            raise MediaError("invalid_backend_prompt", "backend prompt identity is invalid")
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self.get(job_id, principal=principal)
            if current.backend_prompt_id and current.backend_prompt_id != prompt_id:
                db.execute("ROLLBACK")
                raise MediaError("backend_prompt_conflict", "job already owns another backend prompt", status=409)
            db.execute("UPDATE media_jobs SET backend_prompt_id=? WHERE id=?", (prompt_id, job_id))
            db.execute("COMMIT")
        return self.get(job_id, principal=principal)

    def add_artifact(self, artifact: MediaArtifact) -> MediaJob:
        payload = _artifact_json(artifact)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.get(artifact.job_id, principal=artifact.principal)
            db.execute(
                "INSERT OR IGNORE INTO media_job_artifacts(job_id,artifact_json) VALUES (?,?)",
                (artifact.job_id, payload),
            )
            db.execute("COMMIT")
        return self.get(artifact.job_id, principal=artifact.principal)

    def nonterminal(self) -> list[MediaJob]:
        placeholders = ",".join("?" for _ in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELED))
        with self._connect() as db:
            rows = db.execute(
                f"SELECT id FROM media_jobs WHERE state NOT IN ({placeholders}) ORDER BY created_at,id",
                (JobState.COMPLETED.value, JobState.FAILED.value, JobState.CANCELED.value),
            ).fetchall()
        return [self.get(row["id"], allow_cross_principal=True) for row in rows]


def _artifact_json(artifact: MediaArtifact) -> str:
    return json.dumps(
        {
            "id": artifact.id,
            "job_id": artifact.job_id,
            "principal": artifact.principal,
            "workflow_id": artifact.workflow_id,
            "workflow_version": artifact.workflow_version,
            "media_type": artifact.media_type,
            "byte_length": artifact.byte_length,
            "sha256": artifact.sha256,
            "expires_at": _iso(artifact.expires_at),
            "source_path": artifact.source_path,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _artifact_from_json(raw: str) -> MediaArtifact:
    try:
        value = json.loads(raw)
        value["expires_at"] = _time(value["expires_at"])
        return MediaArtifact(**value)
    except (TypeError, KeyError, json.JSONDecodeError, MediaError) as exc:
        raise MediaError("job_store_corrupt", "stored artifact record is invalid", status=500) from exc


__all__ = ["MediaJobStore"]
