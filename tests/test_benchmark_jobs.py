"""Tests for the portable benchmark-job and artifact contracts."""

from __future__ import annotations

import copy

import pytest

from anvil_serving.benchmarking import jobs


NOW = "2026-08-03T08:00:00Z"


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
