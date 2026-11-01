from __future__ import annotations

import hashlib
import json
from pathlib import Path


from anvil_serving.benchmarking.evidence_reader import read_referenced_job_evidence
from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA
from anvil_serving.control_plane.controller.store import BenchmarkJobStore


def _store_with_evidence(tmp_path: Path):
    store = BenchmarkJobStore(
        str(tmp_path / "jobs.sqlite3"), run_root=str(tmp_path / "runs")
    )
    spec = {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": "run-001",
        "ownership_id": "campaign-001",
        "suite": "agentic",
        "profile": "smoke",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"},
        "worker": {"id": "worker"},
        "submitted_at": "2026-08-03T12:00:00Z",
        "timeout_s": 600,
        "parameters": {},
    }
    store.submit(spec)
    store.transition("run-001", "running")
    path = tmp_path / "runs" / "campaign-001" / "run-001" / "evidence" / "0-agentic.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps({"summary": {"passed": False}}).encode()
    path.write_bytes(raw)
    reference = {
        "path": "evidence/0-agentic.json",
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    store.transition(
        "run-001",
        "completed",
        results={"evidence": {"stages": [{"evidence": [reference]}]}},
    )
    return store, store.status("run-001"), path


def test_reads_only_digest_bound_referenced_stage(tmp_path):
    store, record, _path = _store_with_evidence(tmp_path)

    result = read_referenced_job_evidence(store, record, "evidence/0-agentic.json")

    assert result["data"] == {"summary": {"passed": False}}
    assert len(result["sha256"]) == 64
