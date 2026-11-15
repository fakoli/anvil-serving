import datetime as dt

import pytest

from anvil_serving.media import (
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


def test_persistent_missing_prompt_fails_but_outages_and_transient_absence_do_not(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    now = [0]
    observed = ["missing"]

    def history(prompt):
        if observed[0] == "outage":
            raise MediaError("backend_unavailable", "backend unavailable", status=503)
        return BackendStatus(prompt, "running" if observed[0] == "running" else "queued",
                             error_code="backend_prompt_missing" if observed[0] == "missing" else "")

    reconciler = MediaJobReconciler(store, history, backend_endpoint=ENDPOINT, clock=lambda: now[0])
    assert reconciler.reconcile(job).state != JobState.FAILED
    now[0] = 20; observed[0] = "running"
    job = reconciler.reconcile(job)
    assert job.state == JobState.RUNNING
    now[0] = 30; observed[0] = "missing"
    assert reconciler.reconcile(job).state == JobState.RUNNING
    now[0] = 70; observed[0] = "outage"
    with pytest.raises(MediaError):
        reconciler.reconcile(job)
    now[0] = 80; observed[0] = "missing"
    assert reconciler.reconcile(job).state == JobState.RUNNING
    now[0] = 109
    assert reconciler.reconcile(job).state == JobState.RUNNING
    now[0] = 110
    failed = reconciler.reconcile(job)
    assert failed.state == JobState.FAILED and failed.events[-1].reason == "backend_prompt_lost"
    assert not reconciler._missing_since
