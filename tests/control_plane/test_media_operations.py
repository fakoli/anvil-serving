from __future__ import annotations

import dataclasses
import threading

from anvil_serving.media.contracts import JobState
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
    lifecycle = MediaWorkerLifecycle(
        store,
        status_operation=_status(running=False),
        manage_operation=lambda args: calls.append(dict(args)) or {"applied": not args["dry_run"]},
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

    def manage(args):
        calls.append(dict(args))
        if args["action"] == "down":
            down_entered.set()
            assert down_release.wait(5)
        return {"applied": True}

    first = MediaWorkerLifecycle(
        first_store, status_operation=_status(running=False), manage_operation=manage
    )
    second = MediaWorkerLifecycle(
        second_store, status_operation=_status(running=False), manage_operation=manage
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
