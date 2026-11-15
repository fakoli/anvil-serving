"""Tests for the portable benchmark-job and artifact contracts."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

import pytest

from anvil_serving.benchmarking import jobs
from anvil_serving.control_plane.controller import store as store_module
from anvil_serving.control_plane.controller.store import BenchmarkJobStore
from anvil_serving.observability.workloads import (
    ResultStatus,
    WorkloadKind,
    WorkloadOwner,
    WorkloadQuery,
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


def test_optional_profile_pin_changes_digest_without_rewriting_legacy_records():
    legacy = jobs.new_job_record(_spec())
    original = jobs.canonical_json_bytes(legacy)
    assert "profile_sha256" not in legacy["spec"]
    assert jobs.canonical_json_bytes(jobs.validate_job_record(legacy)) == original
    pinned = jobs.new_job_record(_spec(profile_sha256="a" * 64))
    assert pinned["spec"]["profile_sha256"] == "a" * 64
    assert pinned["spec_sha256"] != legacy["spec_sha256"]


def test_spec_file_and_inline_share_canonical_digest(tmp_path):
    raw = json.dumps(_spec(), indent=2)
    path = tmp_path / "job.json"
    path.write_text(raw, encoding="utf-8")
    file_spec = jobs.load_job_spec(spec_path=str(path), suite="context")
    inline_spec = jobs.load_job_spec(spec_json=raw, suite="context")
    assert file_spec == inline_spec
    assert jobs.job_spec_sha256(file_spec) == jobs.job_spec_sha256(inline_spec)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_spec_file_rejects_fifo_without_waiting_for_a_writer(tmp_path):
    path = tmp_path / "job.fifo"
    os.mkfifo(path)
    with pytest.raises(jobs.BenchmarkJobError) as exc:
        jobs.load_job_spec(spec_path=str(path))
    assert exc.value.code == "bad_spec_file"
