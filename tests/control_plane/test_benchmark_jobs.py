from __future__ import annotations

import json
from pathlib import Path

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.control_plane.controller.store import BenchmarkJobStore


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
