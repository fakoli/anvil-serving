from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import pytest

from anvil_serving.benchmarking.jobs import (
    BenchmarkJobError,
    JOB_SPEC_SCHEMA,
)
from anvil_serving.control_plane.controller import store as store_module
from anvil_serving.control_plane.controller.store import BenchmarkJobStore, OperationStore
from anvil_serving.observability.workloads import (
    ResultStatus,
    WorkloadQuery,
)


def _spec(run_id: str = "run-001", **changes):
    value = {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": run_id,
        "ownership_id": "campaign-001",
        "suite": "context",
        "profile": "smoke",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"},
        "worker": {"id": "ai-laptop-node"},
        "submitted_at": "2026-08-03T12:00:00Z",
        "timeout_s": 600,
        "parameters": {"depth": 32768},
    }
    value.update(changes)
    return value


def _store(tmp_path: Path) -> BenchmarkJobStore:
    return BenchmarkJobStore(
        str(tmp_path / "jobs.sqlite3"), run_root=str(tmp_path / "runs"), source_id="controller-a",
    )


def test_submit_is_idempotent_and_conflicts_fail_closed(tmp_path):
    store = _store(tmp_path)
    disposition, submitted = store.submit(_spec())
    assert disposition == "submitted"
    restarted = _store(tmp_path)
    repeated, existing = restarted.submit(_spec())
    assert repeated == "existing"
    assert existing == submitted

    with pytest.raises(BenchmarkJobError, match="different immutable specification") as exc:
        restarted.submit(_spec(timeout_s=601))
    assert exc.value.code == "run_id_conflict"


def test_read_only_store_refuses_absent_state_and_does_not_create_files(tmp_path):
    database = tmp_path / "state" / "jobs.sqlite3"
    run_root = tmp_path / "runs"
    with pytest.raises(BenchmarkJobError, match="read-only benchmark store is unavailable") as exc:
        BenchmarkJobStore(str(database), run_root=str(run_root), read_only=True)
    assert exc.value.code == "benchmark_store_unavailable"
    assert not database.parent.exists()
    assert not run_root.exists()


def test_read_only_store_status_preserves_schema_and_data(tmp_path):
    writable = _store(tmp_path)
    writable.submit(_spec())
    database = tmp_path / "jobs.sqlite3"
    run_root = tmp_path / "runs"
    with sqlite3.connect(database) as connection:
        before_schema = connection.execute("PRAGMA schema_version").fetchone()[0]
        before_record = connection.execute("SELECT record FROM benchmark_jobs WHERE run_id = 'run-001'").fetchone()[0]
    reader = BenchmarkJobStore(str(database), run_root=str(run_root), read_only=True)
    assert reader.status("run-001")["state"] == "queued"
    with pytest.raises(BenchmarkJobError, match="benchmark store is read-only") as exc:
        reader.submit(_spec("run-002"))
    assert exc.value.code == "benchmark_store_read_only"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA schema_version").fetchone()[0] == before_schema
        assert connection.execute("SELECT record FROM benchmark_jobs WHERE run_id = 'run-001'").fetchone()[0] == before_record


def test_run_list_interrupts_sql_before_the_owner_deadline_and_releases_lock(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.submit(_spec())
    ticks = iter((0.0, 0.0, 2.0))
    store._snapshot_clock = lambda: next(ticks, 2.0)
    monkeypatch.setattr(store_module, "_STORE_PROGRESS_INSTRUCTIONS", 1)

    with pytest.raises(BenchmarkJobError) as exc:
        store.list_runs()

    assert exc.value.code == "list_deadline_exceeded"
    assert store._lock.acquire(blocking=False)
    store._lock.release()


def test_run_list_backfills_more_than_one_thousand_records(tmp_path):
    store = _store(tmp_path)
    for index in range(1001):
        store.submit(_spec(f"run-{index:04d}"))

    seen, cursor = [], None
    while True:
        page = store.list_runs(limit=100, cursor=cursor)
        seen.extend(row["native_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == [f"run-{index:04d}" for index in reversed(range(1001))]


def test_terminal_artifact_survives_restart_and_is_digest_checked(tmp_path):
    store = _store(tmp_path)
    store.submit(_spec())
    store.transition("run-001", "running")
    completed = store.transition("run-001", "completed", results={"score": 1.0})
    assert completed["artifact"]["path"] == "artifact.json"

    restarted = _store(tmp_path)
    assert restarted.artifact("run-001")["results"] == {"score": 1.0}
    artifact_path = tmp_path / "runs" / "campaign-001" / "run-001" / "artifact.json"
    artifact_path.write_text(json.dumps({"tampered": True}), encoding="utf-8")
    with pytest.raises(BenchmarkJobError) as exc:
        restarted.artifact("run-001")
    assert exc.value.code == "artifact_digest_mismatch"


def test_cancel_records_partial_artifact_before_owned_cleanup(tmp_path):
    store = _store(tmp_path)
    store.submit(_spec())
    store.transition("run-001", "running")
    observed = []

    def cleanup(path: str) -> None:
        artifact = tmp_path / "runs" / "campaign-001" / "run-001" / "artifact.json"
        observed.append((Path(path), artifact.exists()))

    cancelled = store.cancel("run-001", cleanup=cleanup)
    assert cancelled["state"] == "cancelled"
    assert observed == [
        (tmp_path / "runs" / "campaign-001" / "run-001" / "work", True)
    ]
    assert store.artifact("run-001")["status"] == "cancelled"


def test_cancel_queued_job_and_repeat_are_idempotent(tmp_path):
    store = _store(tmp_path)
    store.submit(_spec())
    first = store.cancel("run-001")
    second = store.cancel("run-001", cleanup=lambda _: pytest.fail("cleanup repeated"))
    assert first["state"] == second["state"] == "cancelled"


UTC = timezone.utc


def _operation_store(tmp_path: Path, *, clock=None) -> OperationStore:
    kwargs = {} if clock is None else {"_snapshot_clock": clock}
    return OperationStore(str(tmp_path / "operations.sqlite3"), **kwargs)


def _seed_operations(
    store: OperationStore,
    rows: list[tuple[object, object, object, object, object]],
    *,
    leases: list[tuple[object, object]] | None = None,
) -> None:
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE operation_records (
                idempotency_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                request_id TEXT NOT NULL, status, created_at, updated_at,
                expires_at REAL NOT NULL, response TEXT, result TEXT, error TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE operation_leases (
                idempotency_key TEXT PRIMARY KEY, owner TEXT NOT NULL, updated_at
            )
            """
        )
        connection.executemany(
            "INSERT INTO operation_records VALUES (?, ?, ?, ?, ?, ?, 9999999999, ?, ?, ?)",
            (
                (
                    f"caller-key-{index}",
                    "fingerprint-private",
                    "request-private",
                    status,
                    created,
                    updated,
                    "response-private",
                    "result-private",
                    "error-private",
                )
                for index, (status, created, updated, _ignored, _ignored2) in enumerate(rows)
            ),
        )
        connection.executemany(
            "INSERT INTO operation_leases VALUES (?, 'lease-owner-private', ?)",
            (
                (f"caller-key-{index}", updated)
                for index, updated in (leases or [])
            ),
        )


def test_operation_workload_rowid_reuse_is_fenced_by_created_timestamp(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    first = now.timestamp() - 5
    _seed_operations(store, [("running", first, first, None, None)])
    before = store.list_workloads("node-a", WorkloadQuery(), now).records[0].id
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("DELETE FROM operation_records")
        connection.execute(
            "INSERT INTO operation_records VALUES (?, ?, ?, ?, ?, ?, 9999999999, NULL, NULL, NULL)",
            ("caller-reused", "fingerprint-private", "request-private", "running", first + 1, first + 1),
        )
    after = store.list_workloads("node-a", WorkloadQuery(), now).records[0].id
    assert before != after


def test_operation_workload_deadline_covers_progress_and_post_fetch(
    tmp_path, monkeypatch,
):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    _seed_operations(
        store,
        [("running", now.timestamp(), now.timestamp(), None, None) for _ in range(20)],
    )
    values = iter((0.0, 0.0, 0.0, 2.0))
    store._snapshot_clock = lambda: next(values, 2.0)
    monkeypatch.setattr(store_module, "_STORE_PROGRESS_INSTRUCTIONS", 1)
    assert store.list_workloads(
        "node-a", WorkloadQuery(), now,
    ).status is ResultStatus.UNAVAILABLE

    expired = False
    original = store_module._canonical_operation_timestamp

    def clock():
        return 2.0 if expired else 0.0

    def expire_after_scalar(value):
        nonlocal expired
        result = original(value)
        expired = True
        return result

    store._snapshot_clock = clock
    monkeypatch.setattr(store_module, "_STORE_PROGRESS_INSTRUCTIONS", 1_000_000)
    monkeypatch.setattr(
        store_module, "_canonical_operation_timestamp", expire_after_scalar,
    )
    assert store.list_workloads(
        "node-a", WorkloadQuery(), now,
    ).status is ResultStatus.UNAVAILABLE


def _seed_benchmark_reader(path, rows):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE benchmark_jobs (state, record)")
        connection.executemany("INSERT INTO benchmark_jobs VALUES (?, ?)", rows)


def _benchmark_reader_row(state, at):
    return state, json.dumps({"submitted_at": at.isoformat(), "updated_at": at.isoformat(),
                              "private": "payload-must-not-be-hydrated"})


def test_new_v2_job_checks_budget_before_durable_creation(tmp_path):
    store = _store(tmp_path)
    value = _spec(profile="pilot-v2", parameters={}, timeout_s=1801)
    with pytest.raises(BenchmarkJobError) as exc:
        store.submit(value)
    assert exc.value.code == "profile_budget_exceeded"
    assert store.status(value["run_id"]) is None
