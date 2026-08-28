import datetime as dt

from anvil_serving.media import (
    BackendStatus,
    JobState,
    MediaJobReconciler,
    MediaJobStore,
    normalize_progress_event,
)


NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)


def create(store):
    job, _ = store.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key="one",
        now=NOW,
    )
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
    changed = MediaJobReconciler(reopened, history).reconcile(reopened.get(job.id, principal="hermes"))
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
        store, lambda prompt: calls.append(prompt)
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
    found = []
    changed = MediaJobReconciler(
        store,
        lambda prompt: BackendStatus(prompt, "queued"),
        find_prompt=lambda job_id: found.append(job_id) or "prompt_recovered",
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
        store, lambda prompt: BackendStatus(prompt, "failed", error_code="execution_failed")
    ).reconcile(job)
    assert failed.state == JobState.FAILED
    assert failed.events[-1].reason == "execution_failed"
