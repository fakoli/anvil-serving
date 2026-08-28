import datetime as dt

from anvil_serving.media import JobState, MediaCancellationService, MediaJobStore


NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)


def create(store, *, key="one"):
    job, _ = store.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key=key,
        now=NOW,
    )
    return job


def service(store, calls, *, exclusive=False):
    return MediaCancellationService(
        store,
        delete_queued=lambda prompt: calls.append(("delete", prompt)),
        interrupt_exclusive=lambda: calls.append(("interrupt", None)),
        owns_active_slot=lambda job: exclusive,
    )


def test_pending_cancellation_never_contacts_backend(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    calls = []
    result = service(store, calls).cancel(job.id, principal="hermes")
    assert result.canceled is True
    assert result.job.state == JobState.CANCELED
    assert calls == []


def test_queued_cancellation_deletes_only_owned_prompt(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    job = store.set_backend_prompt(job.id, "prompt_123", principal="hermes")
    job = store.transition(job.id, JobState.QUEUED, principal="hermes")
    calls = []
    result = service(store, calls).cancel(job.id, principal="hermes")
    assert result.job.state == JobState.CANCELED
    assert calls == [("delete", "prompt_123")]


def test_running_cancellation_fails_closed_without_exclusive_slot(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    job = store.set_backend_prompt(job.id, "prompt_123", principal="hermes")
    job = store.transition(job.id, JobState.QUEUED, principal="hermes")
    job = store.transition(job.id, JobState.RUNNING, principal="hermes")
    calls = []
    result = service(store, calls, exclusive=False).cancel(job.id, principal="hermes")
    assert result.canceled is False
    assert result.reason == "running_not_exclusively_owned"
    assert calls == []


def test_running_cancellation_interrupts_only_with_exclusive_proof(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = create(store)
    job = store.set_backend_prompt(job.id, "prompt_123", principal="hermes")
    job = store.transition(job.id, JobState.QUEUED, principal="hermes")
    job = store.transition(job.id, JobState.RUNNING, principal="hermes")
    calls = []
    result = service(store, calls, exclusive=True).cancel(job.id, principal="hermes")
    assert result.backend_interrupted is True
    assert result.job.state == JobState.CANCELED
    assert calls == [("interrupt", None)]
