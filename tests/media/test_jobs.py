import datetime as dt
import sqlite3

import pytest

from anvil_serving.media import JobState, MediaError, MediaJobStore
from anvil_serving.control_plane.mcp.security import normalize_caller_context


NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)


def store(tmp_path):
    return MediaJobStore(tmp_path / "media-jobs.sqlite3")


def create(target, *, digest="a" * 64, key="request-1"):
    return target.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest=digest,
        idempotency_key=key,
        now=NOW,
    )


def test_idempotency_returns_one_job_and_rejects_changed_inputs(tmp_path):
    target = store(tmp_path)
    first, created = create(target)
    second, duplicate_created = create(target)
    assert created is True
    assert duplicate_created is False
    assert first.id == second.id
    with pytest.raises(MediaError) as error:
        create(target, digest="b" * 64)
    assert error.value.code == "idempotency_conflict"


def test_restart_preserves_ordered_state_and_backend_prompt(tmp_path):
    target = store(tmp_path)
    accepted, _ = create(target)
    target.set_backend_prompt(accepted.id, "prompt-private-1", principal="hermes")
    target.transition(accepted.id, JobState.QUEUED, principal="hermes", now=NOW)
    reopened = store(tmp_path).get(accepted.id, principal="hermes")
    assert reopened.state == JobState.QUEUED
    assert reopened.backend_prompt_id == "prompt-private-1"
    assert [event.sequence for event in reopened.events] == [1, 2]


def test_restart_preserves_selected_quality_profile(tmp_path):
    target = store(tmp_path)
    accepted, _ = target.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key="quality-request",
        quality_profile="high",
        now=NOW,
    )
    reopened = store(tmp_path).get(accepted.id, principal="hermes")
    assert reopened.quality_profile == "high"
    assert reopened.as_public_dict()["qualityProfile"] == "high"


def test_v1_job_store_migrates_forward_without_losing_jobs(tmp_path):
    path = tmp_path / "media-jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE media_schema (version INTEGER NOT NULL);
            INSERT INTO media_schema(version) VALUES (1);
            CREATE TABLE media_jobs (
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
            CREATE TABLE media_job_events (
                job_id TEXT NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                state TEXT NOT NULL,
                at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(job_id, sequence)
            );
            CREATE TABLE media_job_artifacts (
                job_id TEXT NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
                artifact_json TEXT NOT NULL,
                PRIMARY KEY(job_id, artifact_json)
            );
            """
        )
        timestamp = NOW.isoformat()
        db.execute(
            "INSERT INTO media_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "job_0123456789abcdef",
                "hermes",
                "image.test-v1",
                "v1",
                "accepted",
                timestamp,
                timestamp,
                "a" * 64,
                "legacy-request",
                "",
                None,
            ),
        )
        db.execute(
            "INSERT INTO media_job_events VALUES (?,?,?,?,?)",
            ("job_0123456789abcdef", 1, "accepted", timestamp, ""),
        )
    reopened = MediaJobStore(path)
    legacy = reopened.get("job_0123456789abcdef", principal="hermes")
    assert legacy.quality_profile == ""
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM media_schema").fetchone()[0] == 2
        columns = {row[1] for row in db.execute("PRAGMA table_info(media_jobs)")}
    assert "quality_profile" in columns


def test_cross_principal_lookup_is_indistinguishable_from_absence(tmp_path):
    target = store(tmp_path)
    accepted, _ = create(target)
    with pytest.raises(MediaError) as error:
        target.get(accepted.id, principal="other")
    assert error.value.code == "job_not_found"
    assert error.value.status == 404


def test_jobs_persist_only_input_digest_not_prompt(tmp_path):
    target = store(tmp_path)
    accepted, _ = create(target)
    raw = b"".join(path.read_bytes() for path in tmp_path.glob("media-jobs.sqlite3*"))
    assert b"a scenic private prompt" not in raw
    assert target.get(accepted.id, principal="hermes").input_digest == "a" * 64


def test_external_principal_contract_is_validated_before_any_insert(tmp_path):
    target = store(tmp_path)
    caller = normalize_caller_context(
        {"principal": "Alice@example.com", "scopes": ["media:submit"]}
    )
    accepted, created = target.create(
        principal=caller.principal,
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key="external-principal",
    )
    assert created is True
    assert accepted.principal == "Alice@example.com"

    with pytest.raises(MediaError):
        target.create(
            principal="",
            workflow_id="image.test-v1",
            workflow_version="v1",
            input_digest="b" * 64,
            idempotency_key="invalid-principal",
        )
    with sqlite3.connect(target.path) as db:
        poisoned = db.execute(
            "SELECT COUNT(*) FROM media_jobs WHERE idempotency_key=?",
            ("invalid-principal",),
        ).fetchone()[0]
    assert poisoned == 0
