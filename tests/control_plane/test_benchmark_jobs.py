from __future__ import annotations

import json
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.control_plane.controller import store as store_module
from anvil_serving.control_plane.controller.store import BenchmarkJobStore, OperationStore
from anvil_serving.observability.workloads import (
    ResultStatus,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOwner,
    WorkloadQuery,
    WorkloadState,
    source_result_to_json,
    workload_id,
)


def _spec(run_id: str = "run-001", **changes):
    value = {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": run_id,
        "ownership_id": "campaign-001",
        "suite": "context",
        "profile": "context-smoke-v1",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"},
        "worker": {"id": "ai-mbp25"},
        "submitted_at": "2026-08-03T12:00:00Z",
        "timeout_s": 600,
        "parameters": {"depth": 32768},
    }
    value.update(changes)
    return value


def _store(tmp_path: Path) -> BenchmarkJobStore:
    return BenchmarkJobStore(
        str(tmp_path / "jobs.sqlite3"), run_root=str(tmp_path / "runs")
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


def test_status_and_cursor_logs_survive_restart(tmp_path):
    store = _store(tmp_path)
    store.submit(_spec())
    store.append_log("run-001", level="INFO", message="queued")
    store.append_log("run-001", level="INFO", message="worker assigned")

    restarted = _store(tmp_path)
    assert restarted.status("run-001")["state"] == "queued"
    page = restarted.logs("run-001", cursor=1, limit=1)
    assert [entry["message"] for entry in page["entries"]] == ["worker assigned"]
    assert page["next_cursor"] == 2


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


def test_unknown_job_and_invalid_cursors_fail_closed(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(BenchmarkJobError) as exc:
        store.logs("missing", cursor=0)
    assert exc.value.code == "job_not_found"
    store.submit(_spec())
    with pytest.raises(BenchmarkJobError) as exc:
        store.logs("run-001", cursor=-1)
    assert exc.value.code == "bad_log_cursor"


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


def test_operation_workload_projection_absent_excluded_and_inputs_precede_storage(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)

    excluded = store.list_workloads(
        "node-a", WorkloadQuery(owner=WorkloadOwner.BENCHMARK), now,
    )
    assert excluded.status is ResultStatus.COMPLETE
    assert excluded.records == ()
    assert not Path(store.path).exists()
    with pytest.raises(WorkloadError):
        store.list_workloads("bad host", WorkloadQuery(), now)
    with pytest.raises(WorkloadError):
        store.list_workloads("node-a", WorkloadQuery(), now.replace(tzinfo=None))
    assert not Path(store.path).exists()


def test_operation_workload_projection_lease_identity_and_state_mapping(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    base = now.timestamp()
    _seed_operations(
        store,
        [
            ("running", base - 60, base - 60, None, None),
            ("succeeded", base - 5, base - 5, None, None),
            ("future-state", base - 4, base - 4, None, None),
        ],
        leases=[(0, base - 1)],
    )

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.COMPLETE
    assert {record.state for record in result.records} == {
        WorkloadState.RUNNING, WorkloadState.TERMINAL, WorkloadState.UNSUPPORTED,
    }
    running = next(record for record in result.records if record.state is WorkloadState.RUNNING)
    assert running.updated_at == now - timedelta(seconds=60)
    assert running.source_timestamp == now - timedelta(seconds=1)
    expected = workload_id(
        "node-a", WorkloadKind.CONTROLLER_OPERATION, WorkloadOwner.CONTROLLER,
        f"operation-row:1:{store_module._canonical_operation_timestamp(base - 60)}",
    )
    assert running.id == expected
    assert "private" not in repr(result)


def test_operation_workloads_filter_order_before_limit_and_report_extra(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    base = now.timestamp()
    rows = [("succeeded", base - 7200, base - 7200, None, None) for _ in range(205)]
    rows.extend(
        [
            ("running", base - 2, base - 2, None, None),
            ("running", base - 1, base - 1, None, None),
            ("running", base, base, None, None),
        ]
    )
    _seed_operations(store, rows)

    result = store.list_workloads(
        "node-a", WorkloadQuery(state=WorkloadState.RUNNING, limit=2), now,
    )

    assert [record.updated_at for record in result.records] == [
        now, now - timedelta(seconds=1),
    ]
    assert result.status is ResultStatus.PARTIAL
    assert result.truncation.omitted is None


def test_operation_workloads_quarantine_numeric_invalid_future_and_keep_healthy(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    base = now.timestamp()
    _seed_operations(
        store,
        [
            ("future-state", 0, 1, None, None),
            ("running", base, base + 30, None, None),
            ("running", base, base + 30.000001, None, None),
            ("running", "not-an-epoch", base, None, None),
            ("running", base, float("inf"), None, None),
        ],
    )

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert len(result.records) == 2
    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.FUTURE
    assert {record.created_at for record in result.records} == {
        datetime(1970, 1, 1, tzinfo=UTC), now,
    }


def test_operation_workloads_missing_lease_table_is_unavailable_but_missing_row_is_valid(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute(
            "CREATE TABLE operation_records (idempotency_key TEXT, fingerprint TEXT, request_id TEXT, status TEXT, created_at REAL, updated_at REAL, expires_at REAL, response TEXT, result TEXT, error TEXT)",
        )
    assert store.list_workloads("node-a", WorkloadQuery(), now).status is ResultStatus.UNAVAILABLE

    other = tmp_path / "other"
    other.mkdir()
    store = _operation_store(other)
    _seed_operations(store, [("running", now.timestamp() - 60, now.timestamp() - 60, None, None)])
    result = store.list_workloads("node-a", WorkloadQuery(), now)
    assert result.status is ResultStatus.COMPLETE
    assert result.records == ()


def test_operation_workload_projection_avoids_lifecycle_helpers_and_validates_each_row(
    tmp_path, monkeypatch,
):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    _seed_operations(
        store,
        [("running", now.timestamp(), now.timestamp(), None, None) for _ in range(2)],
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("lifecycle helper used")

    monkeypatch.setattr(store, "_connection", forbidden)
    monkeypatch.setattr(store, "lookup", forbidden)
    monkeypatch.setattr(store, "recover_interrupted", forbidden)
    original = store_module.validate_source_records
    calls = 0

    def reject_second(records, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise WorkloadError(WorkloadErrorCode.INVALID, "fixed")
        return original(records, **kwargs)

    monkeypatch.setattr(store_module, "validate_source_records", reject_second)
    result = store.list_workloads("node-a", WorkloadQuery(), now)
    assert calls == 2
    assert len(result.records) == 1
    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID


def test_operation_workload_snapshot_uses_only_safe_scalars_and_never_creates_missing_db(
    tmp_path, monkeypatch,
):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    missing = store.list_workloads("node-a", WorkloadQuery(), now)
    assert missing.status is ResultStatus.UNAVAILABLE
    assert not Path(store.path).exists()

    _seed_operations(
        store,
        [("running", now.timestamp(), now.timestamp(), None, None)],
    )
    timestamps: list[object] = []
    digests: list[tuple[object, object]] = []
    real_timestamp = store_module._canonical_operation_timestamp
    real_digest = store_module._operation_digest

    def timestamp_spy(value):
        timestamps.append(value)
        return real_timestamp(value)

    def digest_spy(host, rowid, created):
        digests.append((rowid, created))
        return real_digest(host, rowid, created)

    monkeypatch.setattr(store_module, "_canonical_operation_timestamp", timestamp_spy)
    monkeypatch.setattr(store_module, "_operation_digest", digest_spy)
    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert len(result.records) == 1
    assert timestamps and all(isinstance(value, (int, float)) for value in timestamps)
    assert digests and all(
        rowid == 1 and created == "2026-09-05T00:00:00.000000Z"
        for rowid, created in digests
    )
    assert "private" not in repr(timestamps + digests + list(result.records))


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


def test_operation_workloads_quarantine_duplicate_leases_and_keep_healthy_peer(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    base = now.timestamp()
    _seed_operations(
        store,
        [
            ("running", base - 5, base - 5, None, None),
            ("running", base - 4, base - 4, None, None),
        ],
    )
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("DROP TABLE operation_leases")
        connection.execute(
            "CREATE TABLE operation_leases (idempotency_key TEXT, owner TEXT, updated_at)",
        )
        connection.executemany(
            "INSERT INTO operation_leases VALUES (?, 'lease-owner-private', ?)",
            [
                ("caller-key-0", base - 1),
                ("caller-key-0", base),
                ("caller-key-1", base - 1),
            ],
        )

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert len(result.records) == 1
    assert result.records[0].created_at == now - timedelta(seconds=4)


def test_operation_workload_final_result_validation_is_contained(tmp_path, monkeypatch):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    _seed_operations(store, [("running", now.timestamp(), now.timestamp(), None, None)])
    original = store_module.SourceResult
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkloadError(WorkloadErrorCode.INVALID, "fixed")
        return original(*args, **kwargs)

    monkeypatch.setattr(store_module, "SourceResult", fail_once)
    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert calls == 2
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE


def test_operation_workloads_enforce_lease_freshness_and_future_boundaries(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    base = now.timestamp()
    old = base - 60
    _seed_operations(
        store,
        [("running", old, old, None, None) for _ in range(4)],
        leases=[
            (0, base - 30),
            (1, base - 30.000001),
            (2, base + 30),
            (3, base + 30.000001),
        ],
    )

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.FUTURE
    assert {record.source_timestamp for record in result.records} == {
        now - timedelta(seconds=30),
        now + timedelta(seconds=30),
    }


def test_operation_workloads_terminal_and_unknown_states_ignore_lease_time(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    base = now.timestamp()
    _seed_operations(
        store,
        [
            ("succeeded", base - 5, base - 5, None, None),
            ("future-state", base - 120, base - 120, None, None),
        ],
        leases=[(0, "malformed-private-lease"), (1, base)],
    )

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.COMPLETE
    assert {record.state for record in result.records} == {
        WorkloadState.TERMINAL,
        WorkloadState.UNSUPPORTED,
    }
    assert {record.source_timestamp for record in result.records} == {
        now - timedelta(seconds=5),
        now - timedelta(seconds=120),
    }


def test_operation_workloads_filter_stale_prefix_before_fresh_lease_limit(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    base = now.timestamp()
    rows = [("running", base - 60, base - 60, None, None) for _ in range(205)]
    rows.append(("running", base - 3600, base - 3600, None, None))
    _seed_operations(store, rows, leases=[(205, base)])

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.COMPLETE
    assert len(result.records) == 1
    assert result.records[0].updated_at == now - timedelta(hours=1)
    assert result.records[0].source_timestamp == now


def test_operation_workload_lock_contention_is_bounded(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    timeouts: list[float] = []

    class ContendedLock:
        def acquire(self, *, timeout):
            timeouts.append(timeout)
            return False

        def release(self):
            raise AssertionError("an unacquired lock must not be released")

    store._lock = ContendedLock()
    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.UNAVAILABLE
    assert len(timeouts) == 1 and 0 < timeouts[0] <= 1.0


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


def test_operation_workload_snapshot_is_coherent_with_concurrent_writer(
    tmp_path, monkeypatch,
):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    before = now - timedelta(seconds=1)
    _seed_operations(
        store,
        [("running", before.timestamp(), before.timestamp(), None, None)],
        leases=[(0, before.timestamp())],
    )
    entered = threading.Event()
    release = threading.Event()
    original = store_module._canonical_operation_timestamp

    def blocking_timestamp(value):
        entered.set()
        assert release.wait(1.0)
        return original(value)

    monkeypatch.setattr(store_module, "_canonical_operation_timestamp", blocking_timestamp)
    captured: list[object] = []
    reader = threading.Thread(
        target=lambda: captured.append(
            store.list_workloads("node-a", WorkloadQuery(), now),
        ),
    )
    reader.start()
    try:
        assert entered.wait(1.0)
        with closing(sqlite3.connect(store.path, timeout=1.0)) as connection, connection:
            connection.execute(
                "UPDATE operation_records SET status = 'succeeded', updated_at = ?",
                (now.timestamp(),),
            )
            connection.execute(
                "UPDATE operation_leases SET updated_at = ?",
                (now.timestamp(),),
            )
    finally:
        release.set()
        reader.join(1.0)

    assert not reader.is_alive()
    assert len(captured) == 1
    assert captured[0].records[0].state is WorkloadState.RUNNING
    assert captured[0].records[0].updated_at == before
    assert captured[0].records[0].source_timestamp == before


def test_operation_workload_closed_writer_wal_snapshot_is_logically_read_only(tmp_path):
    store = _operation_store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    _seed_operations(store, [("running", now.timestamp(), now.timestamp(), None, None)])
    database = Path(store.path)
    before_bytes = database.read_bytes()
    with closing(sqlite3.connect(store.path)) as connection:
        before_schema = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name",
        ).fetchall()
        before_rows = connection.execute(
            "SELECT * FROM operation_records ORDER BY rowid",
        ).fetchall()

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.COMPLETE
    assert database.read_bytes() == before_bytes
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name",
        ).fetchall() == before_schema
        assert connection.execute(
            "SELECT * FROM operation_records ORDER BY rowid",
        ).fetchall() == before_rows
    assert {path.name for path in tmp_path.iterdir()} <= {
        database.name,
        database.name + "-wal",
        database.name + "-shm",
    }


def _seed_benchmark_reader(path, rows):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE benchmark_jobs (state, record)")
        connection.executemany("INSERT INTO benchmark_jobs VALUES (?, ?)", rows)


def _benchmark_reader_row(state, at):
    return state, json.dumps({"submitted_at": at.isoformat(), "updated_at": at.isoformat(),
                              "private": "payload-must-not-be-hydrated"})


def test_standalone_benchmark_reader_missing_parents_never_constructs_store(tmp_path, monkeypatch):
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("writable owner must not be opened")

    monkeypatch.setattr(BenchmarkJobStore, "__init__", forbidden)
    monkeypatch.setattr(BenchmarkJobStore, "_connection", forbidden)
    monkeypatch.setattr(BenchmarkJobStore, "_decode_record", forbidden)
    monkeypatch.setattr(store_module, "resolve_owned_run_path", forbidden)
    before = tuple(tmp_path.iterdir())
    result = store_module.read_benchmark_workloads(
        tmp_path / "missing" / "jobs.sqlite3", "node-a", WorkloadQuery(),
        datetime(2026, 9, 5, tzinfo=UTC),
    )
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert result.records == () and result.truncation.omitted is None
    assert tuple(tmp_path.iterdir()) == before
    assert not attempted


@pytest.mark.parametrize("path", [None, "", b"database.sqlite3", 7, True])
def test_standalone_benchmark_reader_invalid_path_is_fixed_unavailable(path, monkeypatch):
    monkeypatch.setattr(store_module, "_read_snapshot_rows", lambda *a, **k: pytest.fail("read"))
    result = store_module.read_benchmark_workloads(
        path, "node-a", WorkloadQuery(), datetime(2026, 9, 5, tzinfo=UTC),
    )
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE


def test_standalone_benchmark_reader_validates_before_path_lock_and_sqlite(monkeypatch):
    touched = []

    class UnreadablePath(os.PathLike):
        def __fspath__(self):
            touched.append("path")
            raise AssertionError("path read")

    class UnreadableLock:
        def acquire(self, **kwargs):
            touched.append("lock")
            raise AssertionError("lock read")

    monkeypatch.setattr(store_module, "_read_snapshot_rows", lambda *a, **k: pytest.fail("SQLite"))
    now = datetime(2026, 9, 5, tzinfo=UTC)
    for host, query, at in [
        ("bad host", WorkloadQuery(), now),
        ("node-a", object(), now),
        ("node-a", WorkloadQuery(), now.replace(tzinfo=None)),
    ]:
        with pytest.raises(WorkloadError):
            store_module.read_benchmark_workloads(
                UnreadablePath(), host, query, at, _lock=UnreadableLock(),
            )
    for query in [WorkloadQuery(owner=WorkloadOwner.ROUTER),
                  WorkloadQuery(kind=WorkloadKind.CONTROLLER_OPERATION),
                  WorkloadQuery(host="node-b")]:
        result = store_module.read_benchmark_workloads(
            UnreadablePath(), "node-a", query, now, _lock=UnreadableLock(),
        )
        assert result.status is ResultStatus.COMPLETE and result.records == ()
    assert not touched


@pytest.mark.parametrize("query,count,status,error", [
    (WorkloadQuery(), 3, ResultStatus.PARTIAL, WorkloadErrorCode.FUTURE),
    (WorkloadQuery(limit=1), 1, ResultStatus.PARTIAL, None),
    (WorkloadQuery(state=WorkloadState.TERMINAL), 1, ResultStatus.COMPLETE, None),
    (WorkloadQuery(state=WorkloadState.UNSUPPORTED), 0, ResultStatus.PARTIAL, WorkloadErrorCode.INVALID),
])
def test_standalone_benchmark_reader_real_database_parity_and_literal_results(
    tmp_path, monkeypatch, query, count, status, error,
):
    now = datetime(2026, 9, 5, tzinfo=UTC)
    store = _store(tmp_path)
    _seed_benchmark_reader(store.path, [
        _benchmark_reader_row("queued", now),
        _benchmark_reader_row("running", now - timedelta(seconds=1)),
        _benchmark_reader_row("completed", now - timedelta(seconds=2)),
        ("malformed", "not-json"),
        _benchmark_reader_row("running", now + timedelta(seconds=31)),
    ])
    expected = store.list_workloads("node-a", query, now)

    def forbidden(*args, **kwargs):
        pytest.fail("standalone read constructed or hydrated writable owner")

    monkeypatch.setattr(BenchmarkJobStore, "__init__", forbidden)
    monkeypatch.setattr(BenchmarkJobStore, "_connection", forbidden)
    monkeypatch.setattr(BenchmarkJobStore, "_decode_record", forbidden)
    result = store_module.read_benchmark_workloads(Path(store.path), "node-a", query, now)
    assert source_result_to_json(result) == source_result_to_json(expected)
    assert (len(result.records), result.status, result.error) == (count, status, error)
    assert "payload-must-not-be-hydrated" not in source_result_to_json(result)
    assert all(record.source_timestamp <= now for record in result.records)


def test_standalone_benchmark_reader_observes_live_wal_without_checkpoint(tmp_path):
    now = datetime(2026, 9, 5, tzinfo=UTC)
    path = tmp_path / "wal.sqlite3"
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE benchmark_jobs (state, record)")
        before = store_module.read_benchmark_workloads(path, "node-a", WorkloadQuery(), now)
        assert before.status is ResultStatus.COMPLETE and before.records == ()
        writer.execute("INSERT INTO benchmark_jobs VALUES (?, ?)", _benchmark_reader_row("queued", now))
        assert Path(str(path) + "-wal").stat().st_size > 0
        after = store_module.read_benchmark_workloads(str(path), "node-a", WorkloadQuery(), now)
        assert after.status is ResultStatus.COMPLETE and len(after.records) == 1
        record = after.records[0]
        assert record.state is WorkloadState.QUEUED
        assert record.source_timestamp == now
        assert record.id == workload_id("node-a", WorkloadKind.BENCHMARK_JOB,
                                        WorkloadOwner.BENCHMARK, "benchmark-row:1")


def test_benchmark_instance_reader_forwards_exact_owner_path_lock_and_clock(tmp_path, monkeypatch):
    store = _store(tmp_path)
    now = datetime(2026, 9, 5, tzinfo=UTC)
    query = WorkloadQuery(limit=3)
    calls = []
    sentinel = object()

    def read(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(store_module, "read_benchmark_workloads", read)
    assert store.list_workloads("node-a", query, now) is sentinel
    assert calls == [((store.path, "node-a", query, now),
                      {"_lock": store._lock, "_snapshot_clock": store._snapshot_clock})]


def test_standalone_benchmark_reader_contended_lock_keeps_one_second_budget(tmp_path):
    timeouts = []

    class ContendedLock:
        def acquire(self, *, timeout):
            timeouts.append(timeout)
            return False

        def release(self):
            pytest.fail("unacquired lock released")

    ticks = iter((2.0, 2.25))
    result = store_module.read_benchmark_workloads(
        tmp_path / "absent.sqlite3", "node-a", WorkloadQuery(),
        datetime(2026, 9, 5, tzinfo=UTC), _lock=ContendedLock(),
        _snapshot_clock=lambda: next(ticks),
    )
    assert result.status is ResultStatus.UNAVAILABLE
    assert timeouts == [0.75]


def test_standalone_benchmark_reader_bad_database_is_unavailable_without_mutation(tmp_path):
    path = tmp_path / "bad.sqlite3"
    path.write_bytes(b"not-a-database")
    result = store_module.read_benchmark_workloads(
        path, "node-a", WorkloadQuery(), datetime(2026, 9, 5, tzinfo=UTC),
    )
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert path.read_bytes() == b"not-a-database"
    assert tuple(tmp_path.iterdir()) == (path,)
