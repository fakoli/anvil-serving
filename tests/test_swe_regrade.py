from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, canonical_json_bytes
from anvil_serving.benchmarking import swe_regrade
from anvil_serving.benchmarking.swe_grader_adapter import GRADER_REVISION, SOURCE_HASHES


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


def _retained(tmp_path, source):
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


def test_prepare_regrade_rejects_other_owner_without_model_calls(tmp_path, monkeypatch):
    source = {**_record("source", "other"), "state": "completed"}
    _retained(tmp_path, source)
    monkeypatch.setattr(swe_regrade, "_verify_dataset_snapshot", lambda _: pytest.fail("dataset checked"))
    with pytest.raises(BenchmarkJobError) as exc:
        swe_regrade.prepare_swe_regrade(Store(tmp_path, source), _record("target", source_run_id="source"))
    assert exc.value.code == "source_run_owner_mismatch"


def test_regrade_only_runs_regenerated_grader_and_preserves_source(tmp_path, monkeypatch):
    source = {**_record("source"), "state": "completed"}
    retained, store = _retained(tmp_path, source)
    original = (retained / "predictions.jsonl").read_bytes()
    monkeypatch.setattr(swe_regrade, "_verify_dataset_snapshot", lambda _: None)
    monkeypatch.setattr(swe_regrade, "_validate_assets", lambda *_: {"swe-bench": {"cache_key": "grader"}})
    monkeypatch.setattr(swe_regrade, "_python_environment", lambda *_args, **_kwargs: (sys.executable, {}))
    monkeypatch.setenv("ANVIL_BENCHMARK_CACHE_ROOT", str(tmp_path))
    plan = swe_regrade.prepare_swe_regrade(store, _record("target", source_run_id="source"))
    calls = []

    def grader(argv, cwd, timeout, env):
        calls.append((argv, env))
        assert "OPENAI_API_KEY" not in env
        assert not any("swe_agent_adapter.py" in value for value in argv)
        (Path(cwd) / "fake.target.json").write_text(json.dumps({"completed_ids": [INSTANCE], "resolved_ids": [INSTANCE], "error_ids": []}))
        return SimpleNamespace(returncode=0, stdout=b"graded", stderr=b"")

    result = swe_regrade.regrade_swe_predictions(plan, runner=grader)
    assert result["state"] == "completed"
    assert result["summary"] == {"attempted": 1, "graded": 1, "resolved": 1, "resolve_rate": 1.0}
    assert len(calls) == 1
    assert (retained / "predictions.jsonl").read_bytes() == original


def test_prepare_regrade_rejects_tampered_retained_hash(tmp_path, monkeypatch):
    source = {**_record("source"), "state": "completed"}
    retained, store = _retained(tmp_path, source)
    (retained / "predictions.jsonl").write_text("changed")
    monkeypatch.setattr(swe_regrade, "_verify_dataset_snapshot", lambda _: None)
    with pytest.raises(BenchmarkJobError) as exc:
        swe_regrade.prepare_swe_regrade(store, _record("target", source_run_id="source"))
    assert exc.value.code == "bad_swe_canonical_manifest"


def test_prepare_regrade_requires_terminal_manifest_anchor(tmp_path):
    source = {**_record("source"), "state": "completed"}
    retained, store = _retained(tmp_path, source)
    stage = retained.parent / "retention-stage.json"
    value = json.loads(stage.read_text())
    value["manifest_reference"]["sha256"] = "0" * 64
    stage.write_text(json.dumps(value))
    with pytest.raises(BenchmarkJobError) as exc:
        swe_regrade.prepare_swe_regrade(store, _record("target", source_run_id="source"))
    assert exc.value.code == "swe_canonical_evidence_mismatch"
