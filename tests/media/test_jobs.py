import datetime as dt
import sqlite3
import threading

import pytest

from anvil_serving.media import JobState, MediaError, MediaJobStore
from anvil_serving.media.jobs import read_media_workloads
from anvil_serving.control_plane.mcp.security import normalize_caller_context
from anvil_serving.observability.workloads import (
    ResultStatus, WorkloadError, WorkloadErrorCode, WorkloadKind, WorkloadOwner, WorkloadQuery,
    WorkloadState, source_result_to_json, workload_id,
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


def test_restart_preserves_ordered_state_and_backend_prompt(tmp_path):
    target = store(tmp_path)
    accepted, _ = create(target)
    target.set_backend_prompt(accepted.id, "prompt-private-1", principal="hermes")
    target.transition(accepted.id, JobState.QUEUED, principal="hermes", now=NOW)
    reopened = store(tmp_path).get(accepted.id, principal="hermes")
    assert reopened.state == JobState.QUEUED
    assert reopened.backend_prompt_id == "prompt-private-1"
    assert [event.sequence for event in reopened.events] == [1, 2]


def test_restart_preserves_selected_quality_profile(tmp_path):
    target = store(tmp_path)
    accepted, _ = target.create(
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        input_digest="a" * 64,
        idempotency_key="quality-request",
        quality_profile="high",
        now=NOW,
    )
    reopened = store(tmp_path).get(accepted.id, principal="hermes")
    assert reopened.quality_profile == "high"
    assert reopened.as_public_dict()["qualityProfile"] == "high"


def test_v1_job_store_migrates_forward_without_losing_jobs(tmp_path):
    path = tmp_path / "media-jobs.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE media_schema (version INTEGER NOT NULL);
            INSERT INTO media_schema(version) VALUES (1);
            CREATE TABLE media_jobs (
                id TEXT PRIMARY KEY,
                principal TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                workflow_version TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                input_digest TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                backend_prompt_id TEXT NOT NULL DEFAULT '',
                approval_json TEXT,
                UNIQUE(principal, workflow_id, workflow_version, idempotency_key)
            );
            CREATE TABLE media_job_events (
                job_id TEXT NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                state TEXT NOT NULL,
                at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(job_id, sequence)
            );
            CREATE TABLE media_job_artifacts (
                job_id TEXT NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
                artifact_json TEXT NOT NULL,
                PRIMARY KEY(job_id, artifact_json)
            );
            """
        )
        timestamp = NOW.isoformat()
        db.execute(
            "INSERT INTO media_jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "job_0123456789abcdef",
                "hermes",
                "image.test-v1",
                "v1",
                "accepted",
                timestamp,
                timestamp,
                "a" * 64,
                "legacy-request",
                "",
                None,
            ),
        )
        db.execute(
            "INSERT INTO media_job_events VALUES (?,?,?,?,?)",
            ("job_0123456789abcdef", 1, "accepted", timestamp, ""),
        )
    reopened = MediaJobStore(path)
    legacy = reopened.get("job_0123456789abcdef", principal="hermes")
    assert legacy.quality_profile == ""
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM media_schema").fetchone()[0] == 2
        columns = {row[1] for row in db.execute("PRAGMA table_info(media_jobs)")}
    assert "quality_profile" in columns


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


@pytest.mark.parametrize("raw,state,phase,outcome", [
    ("accepted", "queued", "queued", None),
    ("queued", "queued", "queued", None),
    ("awaiting_approval", "queued", "awaiting-approval", None),
    ("preparing", "running", "preparing", None),
    ("submitting", "running", "submitting", None),
    ("running", "running", "running", None),
    ("completed", "terminal", "completed", "success"),
    ("failed", "terminal", "failed", "error"),
    ("canceled", "terminal", "cancelled", "cancelled"),
    ("cancelled", "terminal", "cancelled", "cancelled"),
    ("private-unknown-state", "unsupported", "unsupported", "unknown"),
])
def test_media_workload_fixed_mapping_and_private_fields(tmp_path, raw, state, phase, outcome):
    target = store(tmp_path)
    _workload_rows(target, [raw])
    result = target.list_workloads("node-a", WorkloadQuery(), NOW)
    assert result.status is ResultStatus.COMPLETE
    record, = result.records
    assert record.state.value == state and record.phase.value == phase
    assert (record.outcome.value if record.outcome else None) == outcome
    assert record.created_at == record.updated_at == record.source_timestamp == NOW
    assert record.id == workload_id("node-a", WorkloadKind.MEDIA_JOB, WorkloadOwner.MEDIA, "job_" + "0" * 32)
    assert record.observation_quality.value == "recorded"
    assert record.source_authority.value == "media-store"
    assert "private-" not in source_result_to_json(result)


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


@pytest.mark.parametrize("age,active,expected", [
    (30, True, 1), (30.000001, True, 0), (3600, False, 1), (3600.000001, False, 0),
])
def test_media_workload_freshness_and_recent_boundaries(tmp_path, age, active, expected):
    target = store(tmp_path)
    _workload_rows(target, ["running" if active else "completed"])
    result = target.list_workloads("node-a", WorkloadQuery(active_only=active), NOW + dt.timedelta(seconds=age))
    assert len(result.records) == expected


@pytest.mark.parametrize("field,value,code", [
    ("state", b"private-raw-blob", WorkloadErrorCode.INVALID),
    ("updated_at", "private-oversized-" * 1000, WorkloadErrorCode.INVALID),
    ("updated_at", "private-invalid", WorkloadErrorCode.INVALID),
    ("updated_at", (NOW - dt.timedelta(seconds=1)).isoformat(), WorkloadErrorCode.INVALID),
    ("updated_at", (NOW + dt.timedelta(seconds=30, microseconds=1)).isoformat(), WorkloadErrorCode.FUTURE),
])
def test_bad_media_row_keeps_healthy_peer_partial(tmp_path, field, value, code):
    target = store(tmp_path)
    _workload_rows(target, ["running", "running"])
    with sqlite3.connect(target.path) as db:
        db.execute(f"UPDATE media_jobs SET {field}=? WHERE id=?", (value, "job_" + "0" * 32))
    result = target.list_workloads("node-a", WorkloadQuery(), NOW)
    assert len(result.records) == 1
    assert result.status is ResultStatus.PARTIAL and result.error is code
    assert result.truncation.omitted is None
    assert "private-" not in source_result_to_json(result)


def test_media_projection_reads_only_four_metadata_columns_and_no_lifecycle(tmp_path, monkeypatch):
    target = store(tmp_path)
    _workload_rows(target, ["running"])
    original_connect = sqlite3.connect
    columns, statements = set(), []

    def connect(*args, **kwargs):
        db = original_connect(*args, **kwargs)

        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ:
                columns.add((table, column))
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        db.set_trace_callback(statements.append)
        return db

    def forbidden(*args, **kwargs):
        raise AssertionError("lifecycle/full record must not be accessed")

    monkeypatch.setattr(sqlite3, "connect", connect)
    for method in ("_connect", "get", "nonterminal", "transition"):
        monkeypatch.setattr(target, method, forbidden)
    assert len(target.list_workloads("node-a", WorkloadQuery(), NOW).records) == 1
    assert columns == {("media_jobs", name) for name in ("id", "state", "created_at", "updated_at")}
    assert len([s for s in statements if s.startswith("SELECT")]) == 1
    assert not any(s.startswith(("UPDATE", "INSERT", "CREATE", "DELETE")) for s in statements)


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


def test_standalone_media_workload_read_matches_instance_and_sees_wal_commit(tmp_path):
    target = store(tmp_path)
    _workload_rows(target, ["running", "completed"])
    query = WorkloadQuery(limit=1)
    expected = target.list_workloads("node-a", query, NOW)
    standalone = read_media_workloads(target.path, "node-a", query, NOW)
    assert source_result_to_json(standalone) == source_result_to_json(expected)

    later = NOW + dt.timedelta(seconds=1)
    with sqlite3.connect(target.path) as db:
        db.execute(
            "UPDATE media_jobs SET updated_at=? WHERE id=?",
            (later.isoformat(), "job_" + "0" * 32),
        )
    refreshed = read_media_workloads(target.path, "node-a", WorkloadQuery(limit=1), later)
    assert refreshed.records[0].updated_at == later


def test_standalone_media_workload_read_never_initializes_missing_database(tmp_path, monkeypatch):
    missing = tmp_path / "missing-parent" / "media-jobs.sqlite3"

    def forbidden(*args, **kwargs):
        raise AssertionError("standalone read must not construct MediaJobStore")

    monkeypatch.setattr(MediaJobStore, "__init__", forbidden)
    result = read_media_workloads(missing, "node-a", WorkloadQuery(), NOW)
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert not missing.parent.exists()


def test_standalone_media_workload_read_keeps_bounded_lock_contention(tmp_path):
    target = store(tmp_path)

    class ContendedLock:
        def __init__(self):
            self.timeout = None

        def acquire(self, *, timeout):
            self.timeout = timeout
            return False

        def release(self):
            raise AssertionError("an unacquired lock must not be released")

    lock = ContendedLock()
    result = read_media_workloads(target.path, "node-a", WorkloadQuery(), NOW, _lock=lock)
    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert 0 < lock.timeout <= 1.0


def test_standalone_media_workload_read_validates_before_path_or_lock_access(tmp_path):
    class PoisonPath:
        def __fspath__(self):
            raise AssertionError("path must not be accessed")

    class PoisonLock:
        def acquire(self, *args, **kwargs):
            raise AssertionError("lock must not be accessed")

    with pytest.raises(WorkloadError):
        read_media_workloads(PoisonPath(), "node-a", object(), NOW, _lock=PoisonLock())
    with pytest.raises(WorkloadError):
        read_media_workloads(PoisonPath(), "", WorkloadQuery(), NOW, _lock=PoisonLock())
    with pytest.raises(WorkloadError):
        read_media_workloads(
            PoisonPath(),
            "node-a",
            WorkloadQuery(),
            dt.datetime(2026, 8, 27),
            _lock=PoisonLock(),
        )


def test_media_workload_instance_delegates_exact_owner_inputs(tmp_path, monkeypatch):
    from anvil_serving.media import jobs

    target = store(tmp_path)

    def clock():
        return 0.0

    observed = {}
    sentinel = object()

    def delegated(path, host, query, now, *, _monotonic, _lock):
        observed.update(
            path=path,
            host=host,
            query=query,
            now=now,
            monotonic=_monotonic,
            lock=_lock,
        )
        return sentinel

    monkeypatch.setattr(jobs, "read_media_workloads", delegated)
    assert target.list_workloads("node-a", WorkloadQuery(), NOW, _monotonic=clock) is sentinel
    assert observed == {
        "path": target.path,
        "host": "node-a",
        "query": WorkloadQuery(),
        "now": NOW,
        "monotonic": clock,
        "lock": target._lock,
    }
