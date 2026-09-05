"""Tests for the portable benchmark-job and artifact contracts."""

from __future__ import annotations

import copy
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from anvil_serving.benchmarking import jobs
from anvil_serving.control_plane.controller import store as store_module
from anvil_serving.control_plane.controller.store import BenchmarkJobStore
from anvil_serving.observability.workloads import (
    ResultStatus,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadState,
    map_store_state,
    workload_id,
)


NOW = "2026-08-03T08:00:00Z"
UTC = timezone.utc


def _spec(**overrides):
    value = {
        "schema": jobs.JOB_SPEC_SCHEMA,
        "run_id": "run-001",
        "ownership_id": "operator-001",
        "suite": "context",
        "profile": "smoke-v1",
        "endpoint": {
            "base_url": "http://127.0.0.1:8000/v1",
            "model": "llm.primary",
            "auth_env": "ANVIL_ROUTER_TOKEN",
        },
        "worker": {"id": "benchmark-worker"},
        "submitted_at": NOW,
        "timeout_s": 600,
        "parameters": {"context_tokens": [32768, 65536]},
    }
    value.update(overrides)
    return value


def test_job_spec_records_required_identity_and_stable_digest():
    spec = jobs.validate_job_spec(_spec())
    record = jobs.new_job_record(spec)

    assert record["schema"] == jobs.JOB_RECORD_SCHEMA
    assert record["state"] == "queued"
    assert record["spec"]["endpoint"]["model"] == "llm.primary"
    assert record["spec"]["worker"]["id"] == "benchmark-worker"
    assert record["spec_sha256"] == jobs.job_spec_sha256(spec)
    assert len(record["spec_sha256"]) == 64


def test_job_spec_is_closed_bounded_and_rejects_secret_material():
    with pytest.raises(jobs.BenchmarkJobError, match="unsupported fields"):
        jobs.validate_job_spec(_spec(extra="nope"))
    with pytest.raises(jobs.BenchmarkJobError, match="credential material"):
        jobs.validate_job_spec(_spec(parameters={"api_key": "do-not-store"}))
    with pytest.raises(jobs.BenchmarkJobError, match="1-128 portable"):
        jobs.validate_job_spec(_spec(run_id="../escape"))
    with pytest.raises(jobs.BenchmarkJobError, match="127.0.0.1"):
        jobs.validate_job_spec(_spec(endpoint={
            "base_url": "http://localhost:8000/v1", "model": "llm.primary"
        }))


def test_state_machine_rejects_terminal_restart_and_requires_failure_details():
    record = jobs.new_job_record(_spec())
    running = jobs.transition_job(record, "running", timestamp="2026-08-03T08:01:00Z")
    completed = jobs.transition_job(
        running,
        "completed",
        timestamp="2026-08-03T08:02:00Z",
        artifact={"path": "result.json", "sha256": "a" * 64},
    )

    assert record["state"] == "queued"
    assert completed["state"] == "completed"
    assert completed["finished_at"] == "2026-08-03T08:02:00Z"
    with pytest.raises(jobs.BenchmarkJobError, match="cannot transition"):
        jobs.transition_job(completed, "running")
    with pytest.raises(jobs.BenchmarkJobError, match="require failure"):
        jobs.transition_job(running, "failed")


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_partial_terminal_artifacts_keep_common_provenance(terminal):
    running = jobs.transition_job(
        jobs.new_job_record(_spec()), "running", timestamp="2026-08-03T08:01:00Z"
    )
    if terminal == "failed":
        record = jobs.transition_job(
            running,
            terminal,
            timestamp="2026-08-03T08:02:00Z",
            failure={"class": "worker_runtime", "message": "bounded failure"},
        )
    else:
        record = jobs.transition_job(
            running, terminal, timestamp="2026-08-03T08:02:00Z"
        )
    artifact = jobs.build_artifact_envelope(
        record,
        results={"attempts": [{"status": "completed"}]},
        created_at="2026-08-03T08:02:01Z",
    )

    assert artifact["schema"] == jobs.JOB_ARTIFACT_SCHEMA
    assert artifact["partial"] is True
    assert artifact["run"]["run_id"] == "run-001"
    assert artifact["run"]["spec_sha256"] == record["spec_sha256"]
    assert artifact["provenance"]["endpoint"]["model"] == "llm.primary"
    assert artifact["provenance"]["worker"]["id"] == "benchmark-worker"


def test_log_entries_are_single_line_bounded_cursor_addressable(monkeypatch):
    monkeypatch.setattr(jobs, "MAX_BENCHMARK_JOB_LOG_ENTRIES", 2)
    record = jobs.new_job_record(_spec())
    for index in range(3):
        record = jobs.append_job_log(
            record,
            level="INFO\nforged",
            message=f"line {index}\nsecond line",
            timestamp=f"2026-08-03T08:00:0{index}Z",
        )

    assert record["logs"]["truncated"] is True
    assert record["logs"]["retained_from"] == 1
    assert record["logs"]["next_cursor"] == 3
    assert [entry["cursor"] for entry in record["logs"]["entries"]] == [1, 2]
    assert all("\n" not in entry["message"] for entry in record["logs"]["entries"])
    assert record["logs"]["entries"][-1]["level"] == "info forged"


def test_owned_run_path_rejects_escape_and_broad_root(tmp_path):
    root = tmp_path / "jobs"
    root.mkdir()
    run_root = jobs.resolve_owned_run_path(
        str(root), ownership_id="operator", run_id="run-1"
    )
    artifact = jobs.resolve_owned_run_path(
        str(root), ownership_id="operator", run_id="run-1", relative="artifact.json"
    )

    assert artifact.startswith(run_root)
    with pytest.raises(jobs.BenchmarkJobError, match="escapes"):
        jobs.resolve_owned_run_path(
            str(root), ownership_id="operator", run_id="run-1", relative="../../escape"
        )
    with pytest.raises(jobs.BenchmarkJobError, match="non-root"):
        jobs.resolve_owned_run_path(
            str(tmp_path.anchor), ownership_id="operator", run_id="run-1"
        )


def test_validation_and_transitions_do_not_mutate_callers():
    spec = _spec()
    original_spec = copy.deepcopy(spec)
    record = jobs.new_job_record(spec)
    original_record = copy.deepcopy(record)

    jobs.validate_job_spec(spec)
    jobs.transition_job(record, "running", timestamp="2026-08-03T08:01:00Z")

    assert spec == original_spec
    assert record == original_record


def _workload_store(tmp_path: Path, *, clock=None) -> BenchmarkJobStore:
    kwargs = {} if clock is None else {"_snapshot_clock": clock}
    return BenchmarkJobStore(
        str(tmp_path / "jobs.sqlite3"),
        run_root=str(tmp_path / "runs"),
        **kwargs,
    )


def _seed_workloads(
    store: BenchmarkJobStore,
    rows: list[tuple[object, object]],
) -> None:
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE benchmark_jobs (
                run_id TEXT PRIMARY KEY,
                spec_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                revision INTEGER NOT NULL,
                record TEXT NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO benchmark_jobs VALUES (?, ?, ?, 1, ?)",
            (
                (f"caller-{index}", "a" * 64, state, record)
                for index, (state, record) in enumerate(rows)
            ),
        )


def _workload_payload(
    updated: datetime,
    *,
    submitted: datetime | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    def stamp(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    value: dict[str, object] = {
        "submitted_at": stamp(submitted or updated),
        "updated_at": stamp(updated),
    }
    if extra:
        value.update(extra)
    return json.dumps(value)


def test_store_state_mapping_is_fixed_and_unknown_text_is_unsupported():
    assert map_store_state(WorkloadOwner.BENCHMARK, "queued") == (
        WorkloadState.QUEUED,
        WorkloadPhase.QUEUED,
        None,
    )
    assert map_store_state(WorkloadOwner.BENCHMARK, "completed") == (
        WorkloadState.TERMINAL,
        WorkloadPhase.COMPLETED,
        WorkloadOutcome.SUCCESS,
    )
    assert map_store_state(WorkloadOwner.BENCHMARK, "cancelling") == (
        WorkloadState.UNSUPPORTED,
        WorkloadPhase.UNSUPPORTED,
        WorkloadOutcome.UNKNOWN,
    )
    with pytest.raises(WorkloadError, match="must be a string"):
        map_store_state(WorkloadOwner.BENCHMARK, True)
    assert map_store_state(WorkloadOwner.CONTROLLER, "succeeded") == (
        WorkloadState.TERMINAL,
        WorkloadPhase.COMPLETED,
        WorkloadOutcome.SUCCESS,
    )


def test_benchmark_workload_projection_absent_and_excluded_sources_do_not_create_db(
    tmp_path,
):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)

    excluded = store.list_workloads(
        "node-a", WorkloadQuery(owner=WorkloadOwner.ROUTER), now,
    )
    assert excluded.status is ResultStatus.COMPLETE
    assert excluded.records == ()
    assert excluded.truncation.omitted == 0
    assert not Path(store.path).exists()
    impossible = store.list_workloads(
        "node-a",
        WorkloadQuery(state=WorkloadState.TERMINAL, active_only=True),
        now,
    )
    assert impossible.status is ResultStatus.COMPLETE
    assert impossible.records == ()
    assert not Path(store.path).exists()


def test_benchmark_workload_inputs_fail_before_storage_access(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)

    with pytest.raises(WorkloadError, match="topology identifier"):
        store.list_workloads("unsafe host", WorkloadQuery(), now)
    with pytest.raises(WorkloadError, match="wrong type"):
        store.list_workloads("node-a", object(), now)
    with pytest.raises(WorkloadError, match="timezone"):
        store.list_workloads("node-a", WorkloadQuery(), now.replace(tzinfo=None))
    assert not Path(store.path).exists()

    unavailable = store.list_workloads("node-a", WorkloadQuery(), now)
    assert unavailable.status is ResultStatus.UNAVAILABLE
    assert unavailable.error is WorkloadErrorCode.UNAVAILABLE
    assert unavailable.truncation.omitted is None
    assert not Path(store.path).exists()


def test_benchmark_workload_existing_db_without_table_is_unavailable(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    before = Path(store.path).read_bytes()

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert Path(store.path).read_bytes() == before


def test_benchmark_workloads_map_states_without_caller_identity(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    states = ["queued", "running", "completed", "failed", "cancelled", "cancelling"]
    _seed_workloads(store, [(state, _workload_payload(now)) for state in states])

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.COMPLETE
    assert {record.state for record in result.records} == {
        WorkloadState.QUEUED,
        WorkloadState.RUNNING,
        WorkloadState.TERMINAL,
        WorkloadState.UNSUPPORTED,
    }
    assert all(len(record.id) == 64 and "caller" not in record.id for record in result.records)
    unsupported = next(
        record for record in result.records if record.state is WorkloadState.UNSUPPORTED
    )
    assert unsupported.outcome is WorkloadOutcome.UNKNOWN


def test_benchmark_active_only_excludes_known_unsupported_malformed_row(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(store, [("cancelling", "{}")])

    result = store.list_workloads(
        "node-a", WorkloadQuery(active_only=True), now,
    )

    assert result.status is ResultStatus.COMPLETE
    assert result.records == ()
    assert result.truncation.omitted == 0


def test_benchmark_workloads_filter_and_order_before_limit(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    old = now - timedelta(hours=2)
    rows = [("completed", _workload_payload(old)) for _ in range(205)]
    rows.extend(
        [
            ("running", _workload_payload(now - timedelta(seconds=2))),
            ("running", _workload_payload(now - timedelta(seconds=1))),
        ]
    )
    _seed_workloads(store, rows)

    result = store.list_workloads(
        "node-a", WorkloadQuery(state=WorkloadState.RUNNING, limit=2), now,
    )

    assert result.status is ResultStatus.COMPLETE
    assert [record.updated_at for record in result.records] == [
        now - timedelta(seconds=1),
        now - timedelta(seconds=2),
    ]


def test_benchmark_workloads_use_digest_tie_break_and_unknown_omission(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(store, [("running", _workload_payload(now)) for _ in range(3)])

    result = store.list_workloads("node-a", WorkloadQuery(limit=2), now)
    expected = sorted(
        workload_id(
            "node-a",
            WorkloadKind.BENCHMARK_JOB,
            WorkloadOwner.BENCHMARK,
            f"benchmark-row:{rowid}",
        )
        for rowid in range(1, 4)
    )

    assert [record.id for record in result.records] == expected[:2]
    assert result.status is ResultStatus.PARTIAL
    assert result.truncation.omitted is None


def test_benchmark_workloads_enforce_fresh_and_recent_boundaries(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(
        store,
        [
            ("running", _workload_payload(now - timedelta(seconds=30))),
            ("running", _workload_payload(now - timedelta(seconds=30, microseconds=1))),
            ("completed", _workload_payload(now - timedelta(seconds=60))),
            ("completed", _workload_payload(now - timedelta(seconds=60, microseconds=1))),
        ],
    )

    active = store.list_workloads("node-a", WorkloadQuery(active_only=True), now)
    recent = store.list_workloads(
        "node-a",
        WorkloadQuery(state=WorkloadState.TERMINAL, recent_seconds=60),
        now,
    )

    assert len(active.records) == 1
    assert active.records[0].updated_at == now - timedelta(seconds=30)
    assert len(recent.records) == 1
    assert recent.records[0].updated_at == now - timedelta(seconds=60)


def test_benchmark_workloads_filter_provably_old_invalid_rows_before_limit(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    old = now - timedelta(hours=2)
    payload = json.dumps({
        "submitted_at": "not-a-timestamp",
        "updated_at": old.isoformat().replace("+00:00", "Z"),
    })
    _seed_workloads(store, [("completed", payload)])

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.COMPLETE
    assert result.records == ()
    assert result.truncation.omitted == 0


def test_benchmark_workloads_quarantine_invalid_and_future_peers(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(
        store,
        [
            ("running", _workload_payload(now)),
            ("running", _workload_payload(now + timedelta(seconds=30))),
            ("running", "{not-json"),
            ("running", _workload_payload(now + timedelta(seconds=30, microseconds=1))),
        ],
    )

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert len(result.records) == 2
    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.FUTURE
    assert result.truncation.omitted is None


def test_benchmark_workloads_apply_canonical_validation_per_record(
    tmp_path, monkeypatch,
):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(store, [("running", _workload_payload(now)) for _ in range(2)])
    original = store_module.validate_source_records
    calls = 0

    def reject_second(records, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise WorkloadError(
                WorkloadErrorCode.FUTURE,
                "workload timestamp is too far in the future",
            )
        return original(records, **kwargs)

    monkeypatch.setattr(store_module, "validate_source_records", reject_second)
    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert calls == 2
    assert len(result.records) == 1
    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.FUTURE
    assert result.truncation.omitted is None


def test_benchmark_workloads_reject_oversized_record_without_materializing_it(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    oversized = _workload_payload(now, extra={"private": "x" * (8 * 1024 * 1024)})
    _seed_workloads(store, [("running", oversized)])

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.records == ()
    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert result.truncation.omitted is None


def test_benchmark_workload_udfs_receive_only_bounded_safe_scalars(tmp_path, monkeypatch):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    secret = "seeded-private-payload"
    _seed_workloads(
        store,
        [("running", _workload_payload(now, extra={"private": secret}))],
    )
    timestamp_values: list[object] = []
    digest_values: list[object] = []
    real_timestamp = store_module._canonical_store_timestamp
    real_digest = store_module._benchmark_digest

    def timestamp_spy(value):
        timestamp_values.append(value)
        return real_timestamp(value)

    def digest_spy(host, rowid):
        digest_values.append(rowid)
        return real_digest(host, rowid)

    monkeypatch.setattr(store_module, "_canonical_store_timestamp", timestamp_spy)
    monkeypatch.setattr(store_module, "_benchmark_digest", digest_spy)

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert len(result.records) == 1
    assert timestamp_values
    assert all(isinstance(value, str) and len(value) <= 64 for value in timestamp_values)
    assert digest_values and set(digest_values) == {1}
    assert secret not in repr(timestamp_values + digest_values)


@pytest.mark.parametrize("field", ["submitted_at", "updated_at"])
def test_benchmark_timestamp_udf_rejects_nul_suffix_before_projection(
    tmp_path, monkeypatch, field,
):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    valid = now.isoformat().replace("+00:00", "Z")
    hostile = valid + "\x00" + ("x" * 1_000_000)
    value = {"submitted_at": valid, "updated_at": valid}
    value[field] = hostile
    _seed_workloads(store, [("running", json.dumps(value))])
    seen: list[object] = []
    original = store_module._canonical_store_timestamp

    def timestamp_spy(candidate):
        seen.append(candidate)
        return original(candidate)

    monkeypatch.setattr(store_module, "_canonical_store_timestamp", timestamp_spy)
    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert result.records == ()
    assert seen
    assert all(
        isinstance(candidate, str) and len(candidate.encode("utf-8")) <= 65
        for candidate in seen
    )
    assert hostile not in seen


def test_benchmark_workload_projection_reuses_canonical_selector(tmp_path, monkeypatch):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(store, [("running", _workload_payload(now))])
    original = store_module.select_records
    calls: list[tuple[object, object]] = []

    def selector(records, query, **kwargs):
        calls.append((records, query))
        return original(records, query, **kwargs)

    monkeypatch.setattr(store_module, "select_records", selector)
    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert len(result.records) == 1
    assert len(calls) == 1
    assert calls[0][0] == result.records


def test_benchmark_workload_deadline_covers_lock_and_query(tmp_path, monkeypatch):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(store, [("running", _workload_payload(now)) for _ in range(20)])
    values = iter((0.0, 0.0, 0.0, 2.0))
    store._snapshot_clock = lambda: next(values, 2.0)
    monkeypatch.setattr(store_module, "_STORE_PROGRESS_INSTRUCTIONS", 1)

    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.UNAVAILABLE
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM benchmark_jobs").fetchone()[0] == 20


def test_snapshot_helper_checks_deadline_after_sub_progress_interval_query(tmp_path):
    expired = False
    calls = 0

    def clock():
        return 2.0 if expired else 0.0

    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(store, [("running", _workload_payload(now))])

    def expire_after_select():
        nonlocal calls, expired
        calls += 1
        expired = True
        return 1

    with pytest.raises(TimeoutError, match="deadline expired"):
        store_module._read_snapshot_rows(
            store.path,
            sql="SELECT anvil_expire_after_select()",
            parameters=(),
            deadline=1.0,
            monotonic=clock,
            functions=(("anvil_expire_after_select", 0, expire_after_select),),
        )

    assert calls == 1


def test_benchmark_workload_timestamp_cutoff_overflow_is_fixed_and_prelock(tmp_path):
    store = _workload_store(tmp_path)

    with pytest.raises(WorkloadError, match="outside the supported range") as raised:
        store.list_workloads(
            "node-a",
            WorkloadQuery(),
            datetime.max.replace(tzinfo=UTC),
        )

    assert raised.value.code is WorkloadErrorCode.INVALID
    assert not Path(store.path).exists()


def test_benchmark_workload_lock_contention_is_bounded(tmp_path):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
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


def test_benchmark_workload_busy_storage_is_safe_and_nonmutating(tmp_path, monkeypatch):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(store, [("running", _workload_payload(now))])
    before = Path(store.path).read_bytes()

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database busy seeded-private-detail")

    monkeypatch.setattr(store_module, "_read_snapshot_rows", busy)
    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert Path(store.path).read_bytes() == before


def test_benchmark_workload_projection_never_uses_mutating_store_helpers(
    tmp_path, monkeypatch,
):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    _seed_workloads(store, [("running", _workload_payload(now))])

    def forbidden(*args, **kwargs):
        raise AssertionError("mutating store helper was called")

    monkeypatch.setattr(store, "_connection", forbidden)
    monkeypatch.setattr(store, "status", forbidden)
    result = store.list_workloads("node-a", WorkloadQuery(), now)

    assert len(result.records) == 1


def test_benchmark_workload_snapshot_is_coherent_with_concurrent_writer(
    tmp_path, monkeypatch,
):
    store = _workload_store(tmp_path)
    now = datetime(2026, 8, 3, 8, tzinfo=UTC)
    before = now - timedelta(seconds=1)
    after = now
    _seed_workloads(store, [("running", _workload_payload(before))])
    entered = threading.Event()
    release = threading.Event()
    original = store_module._canonical_store_timestamp

    def blocking_timestamp(value):
        entered.set()
        assert release.wait(1.0)
        return original(value)

    monkeypatch.setattr(store_module, "_canonical_store_timestamp", blocking_timestamp)
    captured: list[object] = []
    reader = threading.Thread(
        target=lambda: captured.append(
            store.list_workloads("node-a", WorkloadQuery(), now)
        )
    )
    reader.start()
    try:
        assert entered.wait(1.0)
        with closing(sqlite3.connect(store.path, timeout=1.0)) as connection, connection:
            connection.execute(
                "UPDATE benchmark_jobs SET record = ? WHERE rowid = 1",
                (_workload_payload(after),),
            )
    finally:
        release.set()
        reader.join(1.0)

    assert not reader.is_alive()
    assert captured[0].records[0].updated_at == before
