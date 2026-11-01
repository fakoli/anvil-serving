from __future__ import annotations

import json

import pytest

from anvil_serving.benchmarking import jobs_cli
from anvil_serving.benchmarking.jobs import JOB_SPEC_SCHEMA, job_spec_sha256
from anvil_serving.benchmarking.profiles import load_profile


def test_submit_preview_returns_frozen_profile_spec_without_store(monkeypatch):
    monkeypatch.setattr(jobs_cli, "_store", lambda: pytest.fail("preview opened store"))
    spec = {
        "schema": JOB_SPEC_SCHEMA, "run_id": "preview", "ownership_id": "owner",
        "suite": "aider", "profile": "pilot-v2",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.primary"},
        "worker": {"id": "worker"}, "submitted_at": "2026-10-24T12:00:00Z",
        "timeout_s": 600, "parameters": {},
    }
    result = jobs_cli.run(["aider", "submit", "--spec-json", json.dumps(spec), "--dry-run"])
    assert result["spec"]["profile_sha256"] == load_profile("pilot-v2")["content_sha256"]
    assert result["spec_sha256"] == job_spec_sha256(result["spec"])
