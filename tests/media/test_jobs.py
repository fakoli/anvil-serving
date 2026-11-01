import datetime as dt
import sqlite3
import threading

import pytest

from anvil_serving.media import MediaError, MediaJobStore
from anvil_serving.control_plane.mcp.security import normalize_caller_context
from anvil_serving.observability.workloads import (
    ResultStatus, WorkloadErrorCode, WorkloadKind, WorkloadOwner, WorkloadQuery,
    WorkloadState, workload_id,
)


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


def _workload_rows(target, states, *, created=NOW, updated=NOW):
    with sqlite3.connect(target.path) as db:
        db.executemany(
            "INSERT INTO media_jobs(id,principal,workflow_id,workflow_version,state,created_at,"
            "updated_at,input_digest,idempotency_key) VALUES (?,?,?,?,?,?,?,?,?)",
            [(f"job_{i:032d}", "private-principal", "image.test-v1", "v1", state,
              created.isoformat(), updated.isoformat(), "a" * 64, f"private-key-{i}")
             for i, state in enumerate(states)],
        )


def test_media_workload_top_k_filters_before_limit_and_preserves_digest_order(tmp_path):
    target = store(tmp_path)
    _workload_rows(target, ["running"] * 230 + ["completed"] * 220)
    result = target.list_workloads("node-a", WorkloadQuery(state=WorkloadState.TERMINAL, limit=1000), NOW)
    expected = sorted(workload_id("node-a", WorkloadKind.MEDIA_JOB, WorkloadOwner.MEDIA,
                                  f"job_{i:032d}") for i in range(230, 450))
    assert [r.id for r in result.records] == expected[:200]
    assert result.truncation.returned == 200 and result.truncation.omitted == 20
    assert result.status is ResultStatus.PARTIAL
    with sqlite3.connect(target.path) as db:
        db.execute("UPDATE media_jobs SET updated_at=? WHERE id=?",
                   ((NOW + dt.timedelta(seconds=1)).isoformat(), "job_" + "0" * 32))
    recent = target.list_workloads("node-a", WorkloadQuery(limit=1), NOW + dt.timedelta(seconds=1))
    assert recent.records[0].updated_at == NOW + dt.timedelta(seconds=1)


def test_media_snapshot_is_atomic_with_independent_concurrent_writer(tmp_path, monkeypatch):
    from anvil_serving.media import jobs
    target = store(tmp_path)
    _workload_rows(target, ["running"] * 4)
    original = jobs._media_workload_record
    entered, written = threading.Event(), threading.Event()

    def record(*args):
        if not entered.is_set():
            entered.set()
            assert written.wait(5)
        return original(*args)

    errors = []

    def write():
        try:
            assert entered.wait(5)
            with sqlite3.connect(target.path) as db:
                db.execute("UPDATE media_jobs SET state='completed',updated_at=?",
                           ((NOW + dt.timedelta(seconds=1)).isoformat(),))
        except BaseException as exc:
            errors.append(exc)
        finally:
            written.set()

    monkeypatch.setattr(jobs, "_media_workload_record", record)
    thread = threading.Thread(target=write)
    thread.start()
    try:
        result = target.list_workloads("node-a", WorkloadQuery(), NOW)
        assert len(result.records) == 4
        assert {r.state for r in result.records} == {WorkloadState.RUNNING}
        assert {r.updated_at for r in result.records} == {NOW}
    finally:
        entered.set()
        thread.join(5)
    assert not thread.is_alive() and errors == []
    after = target.list_workloads("node-a", WorkloadQuery(), NOW + dt.timedelta(seconds=1))
    assert {r.state for r in after.records} == {WorkloadState.TERMINAL}


def test_media_read_absence_deadline_and_owner_exclusion_do_not_create_database(tmp_path):
    target = MediaJobStore.__new__(MediaJobStore)
    target.path = tmp_path / "absent.sqlite3"
    target._lock = threading.RLock()
    result = target.list_workloads("node-a", WorkloadQuery(), NOW)
    assert result.status is ResultStatus.UNAVAILABLE
    assert not target.path.exists()
    ticks = iter([0.0, 2.0])
    result = target.list_workloads("node-a", WorkloadQuery(), NOW, _monotonic=lambda: next(ticks))
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    result = target.list_workloads("node-a", WorkloadQuery(owner=WorkloadOwner.ROUTER), NOW,
                                   _monotonic=lambda: (_ for _ in ()).throw(AssertionError()))
    assert result.status is ResultStatus.COMPLETE and result.records == ()
    assert not target.path.exists()
