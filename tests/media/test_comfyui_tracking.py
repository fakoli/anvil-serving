import datetime as dt

import pytest

from anvil_serving.media import (
    BackendOutput,
    BackendStatus,
    JobState,
    MediaJobReconciler,
    MediaJobStore,
    normalize_progress_event,
)
from anvil_serving.media.errors import MediaError


NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
ENDPOINT = "http://127.0.0.1:8188"


def create(store):
    job, _ = store.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key="one",
        now=NOW,
    )
    job = store.transition(job.id, JobState.PREPARING, principal="hermes", now=NOW)
    job = store.transition(job.id, JobState.SUBMITTING, principal="hermes", now=NOW)
    store.check_backend_endpoint(job.id, ENDPOINT, principal="hermes", bind=True)
    return store.set_backend_prompt(job.id, "prompt_123", principal="hermes")


def test_progress_events_are_prompt_scoped_and_bounded():
    assert normalize_progress_event(
        {"type": "progress", "data": {"prompt_id": "prompt_123", "value": 2, "max": 4}},
        prompt_id="prompt_123",
    ).progress == 0.5
    assert normalize_progress_event(
        {"type": "progress", "data": {"prompt_id": "other", "value": 2, "max": 4}},
        prompt_id="prompt_123",
    ) is None


def test_restart_reconciles_existing_prompt_without_submission(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    calls = []

    def history(prompt_id):
        calls.append(prompt_id)
        return BackendStatus(prompt_id, "running")

    reopened = MediaJobStore(tmp_path / "jobs.sqlite3")
    changed = MediaJobReconciler(
        reopened, history, backend_endpoint=ENDPOINT,
    ).reconcile(reopened.get(job.id, principal="hermes"))
    assert changed.state == JobState.RUNNING
    assert calls == ["prompt_123"]
    assert [event.state for event in changed.events][-4:] == [
        JobState.PREPARING,
        JobState.SUBMITTING,
        JobState.QUEUED,
        JobState.RUNNING,
    ]


def test_stale_accepted_job_fails_instead_of_remaining_wedged(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job, _ = store.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key="one",
        now=NOW,
    )
    calls = []
    changed = MediaJobReconciler(
        store, lambda prompt: calls.append(prompt), backend_endpoint=ENDPOINT,
    ).reconcile(job)
    assert changed.state == JobState.FAILED
    assert changed.events[-1].reason == "accepted_recovery_required"
    assert calls == []


def test_reconciler_recovers_submitting_prompt_without_resubmission(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    # Rebuild the crash shape without a locally persisted prompt id.
    second, _ = store.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="b" * 64,
        idempotency_key="two",
        now=NOW,
    )
    second = store.transition(
        second.id, JobState.PREPARING, principal="hermes", now=NOW
    )
    second = store.transition(
        second.id, JobState.SUBMITTING, principal="hermes", now=NOW
    )
    store.check_backend_endpoint(second.id, ENDPOINT, principal="hermes", bind=True)
    found = []
    changed = MediaJobReconciler(
        store,
        lambda prompt: BackendStatus(prompt, "queued"),
        find_prompt=lambda job_id: found.append(job_id) or "prompt_recovered",
        backend_endpoint=ENDPOINT,
    ).reconcile(second)
    assert changed.state == JobState.QUEUED
    assert changed.backend_prompt_id == "prompt_recovered"
    assert changed.events[-1].reason == ""
    assert found == [second.id]
    assert job.backend_prompt_id == "prompt_123"


def test_history_failure_is_terminal_and_truthful(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    failed = MediaJobReconciler(
        store, lambda prompt: BackendStatus(prompt, "failed", error_code="execution_failed"),
        backend_endpoint=ENDPOINT,
    ).reconcile(job)
    assert failed.state == JobState.FAILED
    assert failed.events[-1].reason == "execution_failed"


def test_restart_drift_and_legacy_jobs_never_contact_a_backend(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    bound = create(store)
    calls = []
    with pytest.raises(MediaError) as drift:
        MediaJobReconciler(
            store,
            lambda prompt: calls.append(prompt),
            backend_endpoint="http://127.0.0.1:8189",
        ).reconcile(bound)
    assert drift.value.code == "backend_identity_conflict"
    assert calls == []

    recovering, _ = store.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="c" * 64,
        idempotency_key="recovering",
    )
    recovering = store.transition(
        recovering.id, JobState.PREPARING, principal="hermes"
    )
    recovering = store.transition(
        recovering.id, JobState.SUBMITTING, principal="hermes"
    )
    store.check_backend_endpoint(
        recovering.id, ENDPOINT, principal="hermes", bind=True
    )
    with pytest.raises(MediaError) as recovery_drift:
        MediaJobReconciler(
            store,
            lambda prompt: calls.append(prompt),
            find_prompt=lambda job_id: calls.append(job_id),
            backend_endpoint="http://127.0.0.1:8189",
        ).reconcile(recovering)
    assert recovery_drift.value.code == "backend_identity_conflict"
    assert store.get(recovering.id, principal="hermes").state == JobState.SUBMITTING
    assert calls == []

    legacy, _ = store.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="b" * 64,
        idempotency_key="legacy",
    )
    legacy = store.transition(legacy.id, JobState.PREPARING, principal="hermes")
    legacy = store.transition(legacy.id, JobState.SUBMITTING, principal="hermes")
    legacy = store.set_backend_prompt(legacy.id, "legacy_prompt", principal="hermes")
    with pytest.raises(MediaError) as unavailable:
        MediaJobReconciler(
            store,
            lambda prompt: calls.append(prompt),
            backend_endpoint=ENDPOINT,
        ).reconcile(legacy)
    assert unavailable.value.code == "backend_identity_unavailable"
    assert calls == []


def test_completed_output_rechecks_binding_before_capture(tmp_path, monkeypatch):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    calls = []
    original = store.check_backend_endpoint

    def verify_then_drift(*args, **kwargs):
        calls.append("verify")
        if len(calls) > 1:
            raise MediaError("backend_identity_conflict", "backend changed", status=409)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "check_backend_endpoint", verify_then_drift)
    captured = []
    with pytest.raises(MediaError) as error:
        MediaJobReconciler(
            store,
            lambda prompt: BackendStatus(
                prompt, "completed", outputs=(BackendOutput("1", "private.png"),),
            ),
            capture=lambda current, output: captured.append((current, output)),
            backend_endpoint=ENDPOINT,
        ).reconcile(job)
    assert error.value.code == "backend_identity_conflict"
    assert calls == ["verify", "verify"]
    assert captured == []
