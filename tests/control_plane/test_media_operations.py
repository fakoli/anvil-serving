from __future__ import annotations

import dataclasses
import sqlite3
import threading

import pytest

from anvil_serving.media.contracts import JobState
from anvil_serving.media.errors import MediaError
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.lifecycle import MediaWorkerLifecycle
from anvil_serving import mcp


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


def test_prepare_requires_three_part_gate_and_attaches_receipt(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = _job(store)
    calls = []
    lifecycle = MediaWorkerLifecycle(
        store,
        status_operation=_status(running=False),
        manage_operation=lambda args: calls.append(dict(args)) or {"applied": not args["dry_run"], "plan": ["managed-serve-up"]},
    )

    preview = lifecycle.prepare(job.id, principal="hermes", service="media-worker")
    assert preview.human_required is True
    assert calls == [{"action": "up", "manifest": "", "names": ["media-worker"], "dry_run": True, "confirm": False}]
    assert store.get(job.id, principal="hermes").state == JobState.AWAITING_APPROVAL

    applied = lifecycle.prepare(
        job.id,
        principal="hermes",
        service="media-worker",
        confirm=True,
        human_approved=True,
    )
    assert applied.applied is True
    assert applied.owns_instance is True
    updated = store.get(job.id, principal="hermes")
    assert updated.state == JobState.PREPARING
    assert updated.approval["transactionId"] == applied.transaction_id
    assert updated.approval["operatorAction"]["arguments"]["manifest"] == ""


def test_prepare_binds_exact_manifest_across_preview_apply_and_teardown(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = _job(store)
    calls = []
    running = False

    def status(args):
        calls.append(("status", dict(args)))
        return _status(running=running)({})

    def manage(args):
        nonlocal running
        calls.append(("manage", dict(args)))
        if not args["dry_run"]:
            running = args["action"] == "up"
        return {"applied": not args["dry_run"]}

    lifecycle = MediaWorkerLifecycle(
        store,
        status_operation=status,
        manage_operation=manage,
    )
    preview = lifecycle.prepare(
        job.id,
        principal="hermes",
        service="media-worker",
        manifest="serves.comfyui.toml",
    )
    assert preview.manifest == "serves.comfyui.toml"
    approval = store.get(job.id, principal="hermes").approval
    assert approval["operatorAction"]["arguments"]["manifest"] == (
        "serves.comfyui.toml"
    )

    applied = lifecycle.prepare(
        job.id,
        principal="hermes",
        service="media-worker",
        manifest="serves.comfyui.toml",
        confirm=True,
        human_approved=True,
    )
    assert applied.manifest == "serves.comfyui.toml"
    with pytest.raises(MediaError, match="does not match") as mismatch:
        lifecycle.prepare(
            _job(store, "request-two").id,
            principal="hermes",
            service="media-worker",
            manifest="another.toml",
            confirm=True,
            human_approved=True,
        )
    assert mismatch.value.code == "media_worker_manifest_mismatch"
    assert not any(
        operation == "status" and arguments["manifest"] == "another.toml"
        for operation, arguments in calls
    )

    store.transition(job.id, JobState.CANCELED, principal="hermes")
    with pytest.raises(MediaError) as teardown_mismatch:
        lifecycle.teardown(
            job.id,
            principal="hermes",
            manifest="another.toml",
            confirm=True,
            human_approved=True,
        )
    assert teardown_mismatch.value.code == "media_worker_manifest_mismatch"


def test_preexisting_worker_is_never_owned_or_stopped(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = _job(store)
    calls = []
    lifecycle = MediaWorkerLifecycle(
        store,
        status_operation=_status(running=True),
        manage_operation=lambda args: calls.append(args) or {},
    )
    receipt = lifecycle.prepare(job.id, principal="hermes", service="media-worker")
    assert receipt.preexisting is True
    assert receipt.owns_instance is False
    store.transition(job.id, JobState.CANCELED, principal="hermes")
    teardown = lifecycle.teardown(
        job.id, principal="hermes", confirm=True, human_approved=True
    )
    assert teardown.applied is False
    assert calls == []


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
    owned = lifecycle.prepare(
        first.id, principal="hermes", service="media-worker", confirm=True, human_approved=True
    )
    linked = lifecycle.prepare(
        second.id, principal="hermes", service="media-worker", confirm=True, human_approved=True
    )
    assert linked.transaction_id == owned.transaction_id
    store.transition(first.id, JobState.CANCELED, principal="hermes")
    waiting = lifecycle.teardown(first.id, principal="hermes", confirm=True, human_approved=True)
    assert waiting.controller_receipt == {"reason": "owned_jobs_nonterminal"}
    assert [call["action"] for call in calls] == ["up"]
    store.transition(second.id, JobState.CANCELED, principal="hermes")
    preview = lifecycle.teardown(first.id, principal="hermes")
    assert preview.human_required is True
    released = lifecycle.teardown(
        first.id, principal="hermes", confirm=True, human_approved=True
    )
    assert released.applied is True
    assert [call["action"] for call in calls] == ["up", "down", "down"]


def test_receipt_repr_does_not_gain_mutable_job_state(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    receipt = MediaWorkerLifecycle(
        store,
        status_operation=_status(running=True),
        manage_operation=lambda _args: {},
    ).prepare(_job(store).id, principal="hermes", service="media-worker")
    assert dataclasses.is_dataclass(receipt)


def test_controller_catalog_exposes_only_typed_media_worker_operations():
    names = {tool["name"] for tool in mcp.list_tools()}
    assert {
        "media_worker_prepare",
        "media_worker_status",
        "media_worker_logs",
        "media_worker_teardown",
    } <= names
    prepare = next(tool for tool in mcp.list_tools() if tool["name"] == "media_worker_prepare")
    assert prepare["inputSchema"]["additionalProperties"] is False
    assert "docker" not in prepare["inputSchema"]["properties"]


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
    owner_result = []
    owner_error = []

    def prepare_owner():
        try:
            owner_result.append(
                first.prepare(
                    first_job.id,
                    principal="hermes",
                    service="media-worker",
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


def test_parallel_teardown_claim_stops_worker_once(tmp_path):
    state_path = tmp_path / "jobs.sqlite3"
    first_store = MediaJobStore(state_path)
    second_store = MediaJobStore(state_path)
    first_job = _job(first_store)
    second_job = _job(first_store, "request-two")
    down_entered = threading.Event()
    down_release = threading.Event()
    calls = []
    running = False

    def manage(args):
        nonlocal running
        calls.append(dict(args))
        if args["action"] == "up":
            running = True
        if args["action"] == "down":
            down_entered.set()
            assert down_release.wait(5)
            running = False
        return {"applied": True}

    def status(_args):
        return _status(running=running)({})

    first = MediaWorkerLifecycle(
        first_store, status_operation=status, manage_operation=manage
    )
    second = MediaWorkerLifecycle(
        second_store, status_operation=status, manage_operation=manage
    )
    first.prepare(
        first_job.id,
        principal="hermes",
        service="media-worker",
        confirm=True,
        human_approved=True,
    )
    second.prepare(
        second_job.id,
        principal="hermes",
        service="media-worker",
        confirm=True,
        human_approved=True,
    )
    first_store.transition(first_job.id, JobState.CANCELED, principal="hermes")
    first_store.transition(second_job.id, JobState.CANCELED, principal="hermes")
    result = []

    thread = threading.Thread(
        target=lambda: result.append(
            first.teardown(
                first_job.id,
                principal="hermes",
                confirm=True,
                human_approved=True,
            )
        )
    )
    thread.start()
    assert down_entered.wait(5)
    concurrent = second.teardown(
        second_job.id,
        principal="hermes",
        confirm=True,
        human_approved=True,
    )
    assert concurrent.applied is False
    assert concurrent.controller_receipt == {"reason": "release_in_progress"}
    down_release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert result[0].applied is True
    assert [call["action"] for call in calls].count("down") == 1


def _expire_lifecycle_lease(state_path, transaction_id):
    with sqlite3.connect(state_path) as db:
        db.execute(
            "UPDATE media_lifecycle_transactions SET lease_expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00+00:00", transaction_id),
        )


def test_expired_prepare_claim_recovers_after_controller_crash(tmp_path):
    state_path = tmp_path / "jobs.sqlite3"
    store = MediaJobStore(state_path)
    job = _job(store)
    calls = []
    crashed = MediaWorkerLifecycle(
        store,
        status_operation=_status(running=False),
        manage_operation=lambda args: calls.append(dict(args)) or {"applied": True},
    )
    claimed, owner = crashed._claim_prepare(job.id, "media-worker")
    assert owner is True
    _expire_lifecycle_lease(state_path, claimed["id"])

    restarted = MediaWorkerLifecycle(
        MediaJobStore(state_path),
        status_operation=_status(running=False),
        manage_operation=lambda args: calls.append(dict(args)) or {"applied": True},
    )
    recovered = restarted.prepare(
        job.id,
        principal="hermes",
        service="media-worker",
        confirm=True,
        human_approved=True,
    )
    assert recovered.applied is True
    assert recovered.transaction_id != claimed["id"]
    assert [call["action"] for call in calls] == ["up"]


def test_expired_prepare_claim_observes_started_worker_and_advances_job(tmp_path):
    state_path = tmp_path / "jobs.sqlite3"
    store = MediaJobStore(state_path)
    job = _job(store)
    store.transition(
        job.id,
        JobState.AWAITING_APPROVAL,
        principal="hermes",
        approval={"humanRequired": True},
    )
    crashed = MediaWorkerLifecycle(
        store,
        status_operation=_status(running=False),
        manage_operation=lambda _args: {"applied": True},
    )
    claimed, owner = crashed._claim_prepare(job.id, "media-worker")
    assert owner is True
    _expire_lifecycle_lease(state_path, claimed["id"])

    restarted = MediaWorkerLifecycle(
        MediaJobStore(state_path),
        status_operation=_status(running=True),
        manage_operation=lambda _args: (_ for _ in ()).throw(
            AssertionError("an already-started worker must not be started twice")
        ),
    )
    recovered = restarted.prepare(
        job.id,
        principal="hermes",
        service="media-worker",
        confirm=True,
        human_approved=True,
    )
    assert recovered.transaction_id == claimed["id"]
    updated = restarted.store.get(job.id, principal="hermes")
    assert updated.state == JobState.PREPARING
    assert updated.approval["approved"] is True


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
