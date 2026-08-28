import datetime as dt

import pytest

from anvil_serving.media import JobState, MediaError, MediaJobStore


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
