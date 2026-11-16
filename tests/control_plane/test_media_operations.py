from __future__ import annotations

import sqlite3
import threading

import pytest

from anvil_serving.media.contracts import JobState
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.lifecycle import MediaWorkerLifecycle


def _job(store: MediaJobStore, key: str = "request-one"):
    return store.create(
        principal="hermes",
        workflow_id="image.flux2-klein-4b-fp8-v1",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key=key,
    )[0]


def _status(*, running: bool):
    return lambda _args: {
        "ok": True,
        "result": {
            "serves": [{"name": "media-worker", "running": running, "health_status": 200 if running else None}],
            "reservations": {"gpu_roles": []},
        },
    }


def test_teardown_waits_for_every_owned_job_and_is_confirmed(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    first = _job(store)
    second = _job(store, "request-two")
    calls = []
    running = False

    def status(_args):
        return _status(running=running)({})

    def manage(args):
        nonlocal running
        calls.append(dict(args))
        if not args["dry_run"]:
            running = args["action"] == "up"
        return {"applied": not args["dry_run"]}

    lifecycle = MediaWorkerLifecycle(
        store,
        status_operation=status,
        manage_operation=manage,
    )
    first_preview = lifecycle.prepare(
        first.id, principal="hermes", service="media-worker"
    )
    owned = lifecycle.prepare(
        first.id,
        principal="hermes",
        service="media-worker",
        transaction_id=first_preview.transaction_id,
        confirm=True,
        human_approved=True,
    )
    linked = lifecycle.prepare(
        second.id, principal="hermes", service="media-worker"
    )
    assert linked.transaction_id == owned.transaction_id
    calls.clear()
    store.transition(first.id, JobState.CANCELED, principal="hermes")
    waiting = lifecycle.teardown(first.id, principal="hermes", confirm=True, human_approved=True)
    assert waiting.controller_receipt == {"reason": "owned_jobs_nonterminal"}
    assert calls == []
    store.transition(second.id, JobState.CANCELED, principal="hermes")
    preview = lifecycle.teardown(first.id, principal="hermes")
    assert preview.human_required is True
    released = lifecycle.teardown(
        first.id, principal="hermes", confirm=True, human_approved=True
    )
    assert released.applied is True
    assert [call["action"] for call in calls] == ["down", "down"]


def test_parallel_prepare_across_lifecycle_instances_starts_worker_once(tmp_path):
    state_path = tmp_path / "jobs.sqlite3"
    first_store = MediaJobStore(state_path)
    second_store = MediaJobStore(state_path)
    first_job = _job(first_store)
    second_job = _job(first_store, "request-two")
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def manage(args):
        calls.append(dict(args))
        if not args["dry_run"]:
            entered.set()
            assert release.wait(5)
        return {"applied": True, "plan": ["managed-serve-up"]}

    def status(args):
        return _status(running=entered.is_set())(args)

    first = MediaWorkerLifecycle(
        first_store, status_operation=status, manage_operation=manage
    )
    second = MediaWorkerLifecycle(
        second_store, status_operation=status, manage_operation=manage
    )
    first_preview = first.prepare(
        first_job.id, principal="hermes", service="media-worker"
    )
    second_preview = second.prepare(
        second_job.id, principal="hermes", service="media-worker"
    )
    calls.clear()
    owner_result = []
    owner_error = []

    def prepare_owner():
        try:
            owner_result.append(
                first.prepare(
                    first_job.id,
                    principal="hermes",
                    service="media-worker",
                    transaction_id=first_preview.transaction_id,
                    confirm=True,
                    human_approved=True,
                )
            )
        except Exception as exc:  # pragma: no cover - assertion aid
            owner_error.append(exc)

    thread = threading.Thread(target=prepare_owner)
    thread.start()
    assert entered.wait(5)
    linked = second.prepare(
        second_job.id,
        principal="hermes",
        service="media-worker",
        transaction_id=second_preview.transaction_id,
        confirm=True,
        human_approved=True,
    )
    assert linked.applied is False
    assert linked.controller_receipt["phase"] == "preparing"
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert not owner_error
    assert len(calls) == 1
    assert linked.transaction_id == owner_result[0].transaction_id
    status = second.status(second_job.id, principal="hermes")
    assert status["transaction"]["controllerReceipt"]["applied"] is True


def _expire_lifecycle_lease(state_path, transaction_id):
    with sqlite3.connect(state_path) as db:
        db.execute(
            "UPDATE media_lifecycle_transactions SET lease_expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", transaction_id),
        )


def test_failed_prepare_recovers_only_from_authoritative_running_status(tmp_path):
    state_path = tmp_path / "jobs.sqlite3"
    store = MediaJobStore(state_path)
    job = _job(store)
    mutating_calls = []
    running = False

    def status(_args):
        return _status(running=running)({})

    def ambiguous_manage(args):
        nonlocal running
        if not args["dry_run"]:
            mutating_calls.append(dict(args))
            running = True
            raise RuntimeError("response was lost")
        return {"applied": False}

    lifecycle = MediaWorkerLifecycle(
        store,
        status_operation=status,
        manage_operation=ambiguous_manage,
    )
    preview = lifecycle.prepare(
        job.id, principal="hermes", service="media-worker"
    )
    with pytest.raises(RuntimeError, match="response was lost"):
        lifecycle.prepare(
            job.id,
            principal="hermes",
            service="media-worker",
            transaction_id=preview.transaction_id,
            confirm=True,
            human_approved=True,
        )

    recovered = lifecycle.prepare(
        job.id,
        principal="hermes",
        service="media-worker",
        transaction_id=preview.transaction_id,
        confirm=True,
        human_approved=True,
    )
    assert recovered.applied is True
    assert recovered.controller_receipt == {
        "applied": True,
        "observedRunning": True,
        "previousPhase": "failed",
        "recovered": True,
    }
    assert len(mutating_calls) == 1


def test_expired_release_claim_recovers_when_worker_is_already_stopped(tmp_path):
    state_path = tmp_path / "jobs.sqlite3"
    store = MediaJobStore(state_path)
    job = _job(store)
    running = True

    def status(_args):
        return _status(running=running)({})

    lifecycle = MediaWorkerLifecycle(
        store,
        status_operation=status,
        manage_operation=lambda _args: {"applied": True},
    )
    receipt = lifecycle.prepare(
        job.id,
        principal="hermes",
        service="media-worker",
    )
    assert receipt.preexisting is True

    # Record an owned worker transaction to model a process that crashed after
    # the managed stop succeeded but before the release result was persisted.
    with sqlite3.connect(state_path) as db:
        db.execute(
            "UPDATE media_lifecycle_transactions SET owns_instance=1,preexisting=0,status='active' WHERE id=?",
            (receipt.transaction_id,),
        )
    store.transition(job.id, JobState.CANCELED, principal="hermes")
    assert lifecycle._claim_release(receipt.transaction_id) is True
    _expire_lifecycle_lease(state_path, receipt.transaction_id)
    running = False

    restarted = MediaWorkerLifecycle(
        MediaJobStore(state_path),
        status_operation=status,
        manage_operation=lambda _args: (_ for _ in ()).throw(
            AssertionError("an already-stopped worker must not be stopped twice")
        ),
    )
    released = restarted.teardown(job.id, principal="hermes")
    assert released.applied is True
    assert released.controller_receipt["recovered"] is True
    assert released.controller_receipt["previousPhase"] == "releasing"
