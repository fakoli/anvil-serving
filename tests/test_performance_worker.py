"""Performance jobs share durable dispatch and preserve partial native evidence."""
import json

import pytest

from anvil_serving.benchmarking import worker
from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.control_plane.controller.store import BenchmarkJobStore


def spec():
    return {
        "schema": JOB_SPEC_SCHEMA, "run_id": "perf-run", "ownership_id": "pilot",
        "suite": "performance", "profile": "pilot-v2", "timeout_s": 600,
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        "worker": {"id": "benchmark-worker"}, "submitted_at": "2026-10-24T12:00:00Z",
        "parameters": {"runner": "lil", "workload": {"input_tokens": 0, "output_tokens": 256},
                       "concurrency": 4, "request_count": 16, "temperature": 0, "top_p": 1},
    }


@pytest.mark.parametrize("stopped,corrupt", [(False, False), (True, True), (True, False)])
def test_cancel_retains_native_bytes_and_requires_verified_stop(monkeypatch, tmp_path, stopped, corrupt):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    store.claim("perf-run")
    run = tmp_path / "runs" / "pilot" / "perf-run"
    work = run / "work" / "performance"
    work.mkdir(parents=True)
    canonical = run / "performance"
    canonical.mkdir()
    events = canonical / "requests.jsonl"
    events.write_text('{"attempt":1}\n')
    if corrupt:
        (canonical / "unsafe").symlink_to(tmp_path)
    monkeypatch.setattr(worker, "_stop_owned_worker", lambda *_a, **_k: {"stopped": stopped})
    cancelled = worker.cancel_benchmark_job(store, "perf-run")
    assert cancelled["state"] == "cancelled"
    assert cancelled["cleanup_deferred"] is not (stopped and not corrupt)
    assert work.exists() is not (stopped and not corrupt)
    assert json.loads(events.read_text()) == {"attempt": 1}
    assert store.artifact("perf-run")["results"]["evidence"]["completeness"] == "cancelled"
