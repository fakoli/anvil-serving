"""Aider dispatch and evidence custody use the existing durable job lifecycle."""
import json

import pytest

from anvil_serving.benchmarking import aider, aider_containment, worker
from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.control_plane.controller.store import BenchmarkJobStore


def spec():
    return {
        "schema": JOB_SPEC_SCHEMA, "run_id": "aider-run", "ownership_id": "pilot",
        "suite": "aider", "profile": "pilot-v2", "timeout_s": 600,
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        "worker": {"id": "benchmark-worker"}, "submitted_at": "2026-10-24T12:00:00Z",
        "parameters": {"instance_ids": ["python/exercises/practice/add"], "temperature": 0, "top_p": 1},
    }


@pytest.mark.parametrize("stopped,containers_ok", [(False, True), (True, False), (True, True)])
def test_aider_cancel_requires_worker_and_container_stop_before_cleanup(monkeypatch, tmp_path, stopped, containers_ok):
    store = BenchmarkJobStore(str(tmp_path / "jobs.db"), run_root=str(tmp_path / "runs"))
    store.submit(spec())
    store.claim("aider-run")
    run = tmp_path / "runs" / "pilot" / "aider-run"
    work = run / "work"
    work.mkdir(parents=True)
    canonical = run / "aider"
    canonical.mkdir()
    (canonical / "requests.jsonl").write_text('{"attempt":1}\n')
    order = []
    monkeypatch.setattr(worker, "_stop_owned_worker", lambda *_a, **_k: order.append("worker") or {"stopped": stopped})
    monkeypatch.setattr(aider_containment, "cleanup_aider_containers", lambda **_k: order.append("containers") or {"status": "completed" if containers_ok else "incomplete", "removed": [], "errors": []})
    monkeypatch.setattr(aider, "retain_aider_artifacts", lambda **_k: order.append("retention") or {"state": "incomplete", "missing": ["result.json"]})
    cancelled = worker.cancel_benchmark_job(store, "aider-run")
    assert cancelled["state"] == "cancelled"
    assert cancelled["worker_terminated"] is stopped
    assert cancelled["cleanup_deferred"] is not (stopped and containers_ok)
    assert work.exists() is not (stopped and containers_ok)
    assert order == (["worker"] if not stopped else ["worker", "containers"] if not containers_ok else ["worker", "containers", "retention"])
    assert json.loads((canonical / "requests.jsonl").read_text()) == {"attempt": 1}
    assert store.artifact("aider-run")["results"]["evidence"]["completeness"] == "cancelled"
