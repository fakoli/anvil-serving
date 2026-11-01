from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking.jobs import canonical_json_bytes
from anvil_serving.benchmarking import swe_regrade
from anvil_serving.benchmarking.swe_grader_adapter import GRADER_REVISION, SOURCE_HASHES
from anvil_serving import transports


INSTANCE = "astropy__astropy-12907"


class Store:
    def __init__(self, root, source):
        self.run_root, self.source = str(root), source

    def status(self, run_id):
        return self.source if run_id == "source" else None

    def artifact(self, run_id):
        stage = Path(self.run_root) / "campaign" / run_id / "evidence" / "retention-stage.json"
        return {"results": {"evidence": {"stages": [{"name": "swe_canonical_retention", "evidence": [{
            "path": "evidence/retention-stage.json", "sha256": hashlib.sha256(stage.read_bytes()).hexdigest()
        }]}]}}}


def _record(run_id, owner="campaign", **parameters):
    return {"spec": {"run_id": run_id, "ownership_id": owner, "suite": "swe", "profile": "smoke",
                     "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "candidate"}, "parameters": parameters}}


def _retained(tmp_path, source, *, v2=False, timeout_s=None):
    root = tmp_path / "campaign" / "source" / "evidence" / "swe-canonical"
    root.mkdir(parents=True)
    grader_root = tmp_path / "grader"
    grader_root.mkdir()
    (tmp_path / "campaign" / "source" / "assets.json").write_text("{}")
    adapter = Path(swe_regrade.__file__).with_name("swe_grader_adapter.py")
    plan = {"schema": swe_regrade.SWE_PLAN_SCHEMA, "run_id": "source", "ownership_id": "campaign", "profile": "smoke",
            "dataset": swe_regrade.SWE_DATASET, "dataset_snapshot": {"root": str(tmp_path), "files": {}}, "split": "test",
            "selection": {"instance_ids": [INSTANCE]}, "paths": {"grader_root": str(grader_root)},
            "commands": {"grader": [sys.executable]},
            "harnesses": {"grader": {"revision": GRADER_REVISION},
                          "grader_containment": {"source_hashes": SOURCE_HASHES, "adapter_sha256": hashlib.sha256(adapter.read_bytes()).hexdigest()}}}
    if v2:
        profile = swe_regrade.load_profile("pilot-v2")
        source["spec"]["profile"] = "pilot-v2"
        plan.update(schema=swe_regrade.SWE_PLAN_SCHEMA_V2, profile="pilot-v2",
                    profile_sha256=profile["content_sha256"], budgets=profile["suites"]["swe"]["budgets"])
    if timeout_s is not None:
        plan["timeout_s"] = timeout_s
    plan["plan_sha256"] = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    payloads = {"swe-plan.json": canonical_json_bytes(plan), "predictions.jsonl": b'{"instance_id":"astropy__astropy-12907","model_patch":"diff"}\n'}
    rows = []
    for name, payload in payloads.items():
        (root / name).write_bytes(payload)
        rows.append({"path": name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    (root / "manifest.json").write_text(json.dumps({"schema": swe_regrade.RETAINED_SCHEMA, "state": "retained", "files": rows,
                                                     "missing": [], "bytes": sum(row["bytes"] for row in rows), "source": "work"}))
    manifest = root / "manifest.json"
    stage = root.parent / "retention-stage.json"
    stage.write_text(json.dumps({"manifest_reference": {"path": "evidence/swe-canonical/manifest.json",
                                                       "bytes": manifest.stat().st_size,
                                                       "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}}))
    return root, Store(tmp_path, source)


def _prepared_regrade(tmp_path, monkeypatch, *, v2=False, timeout_s=None):
    source = {**_record("source"), "state": "completed"}
    retained, store = _retained(tmp_path, source, v2=v2, timeout_s=timeout_s)
    monkeypatch.setattr(swe_regrade, "_verify_dataset_snapshot", lambda _: None)
    monkeypatch.setattr(swe_regrade, "_validate_assets", lambda *_: {"swe-bench": {"cache_key": "grader"}})
    monkeypatch.setattr(swe_regrade, "_python_environment", lambda *_args, **_kwargs: (sys.executable, {}))
    monkeypatch.setenv("ANVIL_BENCHMARK_CACHE_ROOT", str(tmp_path))
    target = _record("target", source_run_id="source")
    target["spec"]["profile"] = source["spec"]["profile"]
    return swe_regrade.prepare_swe_regrade(store, target), retained


def test_regrade_retains_group_ownership_failure_without_launch(tmp_path, monkeypatch):
    plan, _ = _prepared_regrade(tmp_path, monkeypatch)
    monkeypatch.setattr(transports.os, "getpgrp", lambda: -1, raising=False)
    monkeypatch.setattr(transports.subprocess, "Popen", lambda *_a, **_k: pytest.fail("grader launched"))
    result = swe_regrade.regrade_swe_predictions(plan)
    assert result["state"] == "incomplete"
    assert result["failure"]["code"] == "_ProcessGroupError"


@pytest.mark.parametrize("v2", [False, True])
def test_regrade_accepts_longer_source_suite_but_caps_grader_replay(tmp_path, monkeypatch, v2):
    plan, retained = _prepared_regrade(tmp_path, monkeypatch, v2=v2, timeout_s=7200)
    assert plan["timeout_s"] == 1800
    assert plan["source_plan_sha256"] == json.loads((retained / "swe-plan.json").read_text())["plan_sha256"]
    calls = []
    def grader(argv, cwd, timeout, env):
        calls.append((argv, timeout))
        (Path(cwd) / "fake.target.json").write_text(json.dumps({"completed_ids": [INSTANCE], "resolved_ids": [INSTANCE], "error_ids": []}))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    assert swe_regrade.regrade_swe_predictions(plan, runner=grader)["state"] == "completed"
    assert len(calls) == 1 and calls[0][1] == 1800
    assert not any("swe_agent_adapter.py" in item for item in calls[0][0])
