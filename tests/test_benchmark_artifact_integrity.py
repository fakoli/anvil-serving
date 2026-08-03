from __future__ import annotations

import copy
import json

import pytest

from anvil_serving.benchmarking.artifacts import (
    BenchmarkArtifactError,
    CROSS_SUITE_EVIDENCE_SCHEMA,
    PROMOTION_DISCLAIMER,
    build_external_prior_record,
    build_measured_evidence,
    evidence_file_reference,
    sanitize_publishable_evidence,
    validate_cross_suite_evidence,
)
from anvil_serving import benchmark_evidence


def measured(tmp_path):
    raw = tmp_path / "raw" / "stage.json"
    raw.parent.mkdir()
    raw.write_text(json.dumps({"request_id": "req-1", "score": 1}), encoding="utf-8")
    return {
        "schema": CROSS_SUITE_EVIDENCE_SCHEMA,
        "evidence_kind": "measured",
        "completeness": "completed",
        "created_at": "2026-08-03T10:00:00Z",
        "run": {
            "run_id": "run-1",
            "ownership_id": "campaign",
            "suite": "swe",
            "profile": "smoke",
            "spec_sha256": "a" * 64,
        },
        "identities": {
            "model": {"alias": "llm.primary"},
            "served_model": {"id": "deepseek"},
            "runtime": {"name": "vllm", "revision": "pinned"},
            "image": {"digest": "sha256:" + "b" * 64},
            "hardware": {"worker": "benchmark-worker", "architecture": "x86_64"},
            "topology": {"kind": "remote-worker"},
            "context": {"configured_tokens": 650000},
            "concurrency": {"requests": 1},
            "harnesses": {"agent": "mini-swe-agent", "grader": "swe-bench"},
            "dataset": {"name": "SWE-bench_Verified", "revision": "pinned"},
        },
        "stages": [{
            "sequence": 0,
            "name": "official_grader",
            "status": "completed",
            "evidence": [evidence_file_reference(str(raw), root=str(tmp_path))],
        }],
        "summary": {"completed": True, "resolved": 1},
        "failure": None,
        "promotion": {"authorized": False, "message": PROMOTION_DISCLAIMER},
    }


def test_measured_integrity_verifies_identities_stage_order_and_hashes(tmp_path):
    value = measured(tmp_path)
    assert validate_cross_suite_evidence(value, artifact_root=str(tmp_path))["completeness"] == "completed"
    value["stages"][0]["sequence"] = 1
    with pytest.raises(BenchmarkArtifactError, match="ordered"):
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))


def test_referenced_file_tampering_is_detected(tmp_path):
    value = measured(tmp_path)
    (tmp_path / "raw" / "stage.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert exc.value.code == "evidence_digest_mismatch"


def test_partial_evidence_survives_without_claiming_completion(tmp_path):
    value = measured(tmp_path)
    value["completeness"] = "failed"
    value["stages"][0]["status"] = "failed"
    value["summary"] = {"attempted": 1, "completed": False}
    value["failure"] = {"class": "grading", "message": "official report absent"}
    checked = validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert checked["stages"][0]["evidence"]
    assert checked["summary"]["attempted"] == 1
    value["summary"] = {"status": "passed"}
    with pytest.raises(BenchmarkArtifactError, match="completed-run"):
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))


def test_builder_normalizes_suite_failure_taxonomy(tmp_path):
    value = measured(tmp_path)
    value["completeness"] = "failed"
    value["stages"][0]["status"] = "failed"
    value["summary"] = {"attempted": 1, "completed": False}
    built = build_measured_evidence(
        run=value["run"],
        identities=value["identities"],
        stages=value["stages"],
        completeness="failed",
        summary=value["summary"],
        failure={"class": "test_failure", "message": "upstream grader failed"},
        artifact_root=str(tmp_path),
    )
    assert built["failure"]["class"] == "grading"


def test_priors_are_structurally_distinct_from_local_measurements():
    prior = build_external_prior_record(
        source={"url": "https://example.com/benchmark", "observed_at": "2026-08-03"},
        claims={"score": 42, "hardware": "external"},
    )
    assert prior["locally_measured"] is False
    assert "run" not in prior
    forged = copy.deepcopy(prior)
    forged["run"] = {"run_id": "fake"}
    with pytest.raises(BenchmarkArtifactError, match="cannot carry"):
        validate_cross_suite_evidence(forged, verify_files=False)


def test_evidence_discovery_keeps_measured_and_prior_labels(tmp_path):
    local = measured(tmp_path)
    local_path = tmp_path / "local.json"
    local_path.write_text(json.dumps(local), encoding="utf-8")
    prior_path = tmp_path / "prior.json"
    prior_path.write_text(json.dumps(build_external_prior_record(
        source={"url": "https://example.com/benchmark", "observed_at": "2026-08-03"},
        claims={"score": 42},
    )), encoding="utf-8")
    assert benchmark_evidence.summarize_artifact(local_path)["locally_measured"] is True
    prior_summary = benchmark_evidence.summarize_artifact(prior_path)
    assert prior_summary["kind"] == "external_prior"
    assert prior_summary["locally_measured"] is False


def test_publication_redacts_secrets_and_real_private_topology(tmp_path):
    value = measured(tmp_path)
    value["operator"] = {
        "headers": {"Authorization": "Bearer top-secret-token"},
        "env": {"ANVIL_ROUTER_TOKEN": "top-secret-token"},
        "endpoint": "http://100.87.34.66:8000/v1",
        "magicdns": "private-host.example.ts.net",
    }
    redacted = sanitize_publishable_evidence(value)
    encoded = json.dumps(redacted)
    assert "top-secret-token" not in encoded
    assert "100.87.34.66" not in encoded
    assert "example.ts.net" not in encoded
    assert "100.64.0.10" in encoded
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path), publishable=True)
    assert exc.value.code == "unsafe_publishable_evidence"
    validate_cross_suite_evidence(redacted, artifact_root=str(tmp_path), publishable=True)


def test_promotion_disclaimer_is_mandatory(tmp_path):
    value = measured(tmp_path)
    value["promotion"]["authorized"] = True
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert exc.value.code == "missing_promotion_boundary"
