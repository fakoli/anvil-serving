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
    synthetic_tailnet_address = ".".join(str(part) for part in (100, 127, 255, 254))
    value["operator"] = {
        "headers": {"Authorization": "Bearer top-secret-token"},
        "env": {"ANVIL_ROUTER_TOKEN": "top-secret-token"},
        "endpoint": f"http://{synthetic_tailnet_address}:8000/v1",
        "magicdns": "private-host.example.ts.net",
    }
    redacted = sanitize_publishable_evidence(value)
    encoded = json.dumps(redacted)
    assert "top-secret-token" not in encoded
    assert synthetic_tailnet_address not in encoded
    assert "example.ts.net" not in encoded
    assert "100.64.0.10" in encoded
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path), publishable=True)
    assert exc.value.code == "unsafe_publishable_evidence"
    validate_cross_suite_evidence(redacted, artifact_root=str(tmp_path), publishable=True)


@pytest.mark.parametrize(
    "value",
    (
        {
            "nested": {
                "http://10.0.0.1:8000": "first",
                "http://10.0.0.2:8000": "second",
            }
        },
        {
            "payload": json.dumps(
                {
                    "http://10.0.0.1:8000": "first",
                    "http://10.0.0.2:8000": "second",
                }
            )
        },
    ),
)
def test_publication_redaction_rejects_nested_and_serialized_key_collisions(value):
    with pytest.raises(BenchmarkArtifactError) as exc:
        sanitize_publishable_evidence(value)
    assert exc.value.code == "redaction_key_collision"


def test_promotion_disclaimer_is_mandatory(tmp_path):
    value = measured(tmp_path)
    value["promotion"]["authorized"] = True
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert exc.value.code == "missing_promotion_boundary"


def _serving_coverage(tmp_path, *, status="complete", measured=True):
    reference = evidence_file_reference(str(tmp_path / "raw" / "stage.json"), root=str(tmp_path))
    plan = tmp_path / "raw" / "plan.json"
    plan.write_text(json.dumps({
        "profile": "serving-comparison/v1",
        "frozen_before_requests": True,
        "source_revision": "a" * 64,
        "source_file_hashes": [reference],
        "arms": {name: {key: name for key in ("model", "revision", "image", "runtime", "hardware")}
                 for name in ("baseline", "candidate")},
        "required_cells": {
            "baseline:c1": {"arm": "baseline", "workload": "short", "context_tokens": 1, "concurrency": 1, "requests": 1},
            "candidate:c1": {"arm": "candidate", "workload": "short", "context_tokens": 1, "concurrency": 1, "requests": 1},
        },
    }), encoding="utf-8")
    metrics = {
        name: {"status": "measured", "value": 0, "evidence": [reference]}
        for name in (
            "effective_prefill_tok_s", "time_to_first_output_ms", "ttft_ms",
            "decode_tok_s", "tpot_ms", "e2e_ms", "throughput_tok_s",
            "prompt_tokens", "completion_tokens", "kv_cache_bytes", "kv_cache_tokens",
            "context_tokens", "concurrency", "gpu_memory", "host_memory", "oom_status", "admission_status",
        )
    }
    for name in (
        "effective_prefill_tok_s", "time_to_first_output_ms", "ttft_ms", "decode_tok_s",
        "tpot_ms", "e2e_ms", "prompt_tokens", "completion_tokens",
    ):
        metrics[name].pop("value")
        metrics[name]["observation"] = {"samples": 1, "p50": 0, "p95": 0}
    metrics["concurrency"]["observation"] = {"requested": 1, "client_max": 1, "engine_max": 1}
    if not measured:
        metrics["decode_tok_s"] = {"status": "not_collected", "reason": "request failed"}
    metrics["gpu_memory"]["observation"] = {"accounting": "GPU allocator", "peak_used_bytes": 0, "min_free_bytes": 0}
    metrics["host_memory"]["observation"] = {"accounting": "cgroup and host", "cgroup_current_bytes": 0, "cgroup_peak_bytes": 0, "cgroup_limit_bytes": 0, "host_available_bytes": 0}
    metrics["admission_status"]["observation"] = {"refused_requests": 0, "preemptions": 0, "ooms": 0}
    plan_data = json.loads(plan.read_text(encoding="utf-8"))
    cells = {}
    for arm in ("baseline", "candidate"):
        raw = tmp_path / "raw" / f"{arm}.json"
        raw.write_text(json.dumps({"schema": "anvil-serving.benchmark/v1", "model": arm, "context_tokens": 1, "concurrency": 1, "requests": 1, "completed": 1, "failed": 0, "request_timings": [{}], "workload_identity": {"workload_kind": "short"}}), encoding="utf-8")
        receipt = tmp_path / "raw" / f"{arm}-receipt.json"
        receipt.write_text(json.dumps({"profile": "serving-comparison/v1", "arm": arm,
            "identity": plan_data["arms"][arm], "cell": plan_data["required_cells"][f"{arm}:c1"],
            "raw": evidence_file_reference(str(raw), root=str(tmp_path))}), encoding="utf-8")
        raw_ref = evidence_file_reference(str(raw), root=str(tmp_path))
        cell_metrics = copy.deepcopy(metrics)
        for metric in cell_metrics.values():
            if metric.get("status") == "measured":
                metric["evidence"] = [raw_ref]
        cells[f"{arm}:c1"] = {"measurement": evidence_file_reference(str(receipt), root=str(tmp_path)), "metrics": cell_metrics}
    return {
        "profile": "serving-comparison/v1", "status": status,
        "frozen_plan": evidence_file_reference(str(plan), root=str(tmp_path)),
        "required_cells": ["baseline:c1", "candidate:c1"],
        "cells": cells,
    }


def test_serving_comparison_requires_complete_coverage_but_quality_failure_is_independent(tmp_path):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert exc.value.code == "missing_metrics_coverage"

    value["metrics_coverage"] = _serving_coverage(tmp_path)
    value["completeness"] = "failed"
    value["stages"][0]["status"] = "failed"
    value["summary"] = {"completed": False, "quality": "failed"}
    value["failure"] = {"class": "model_behavior"}
    assert validate_cross_suite_evidence(value, artifact_root=str(tmp_path))["metrics_coverage"]["status"] == "complete"


@pytest.mark.parametrize("measured_metrics", (False, True))
def test_completed_serving_comparison_rejects_partial_coverage(tmp_path, measured_metrics):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    value["metrics_coverage"] = _serving_coverage(
        tmp_path, status="partial", measured=measured_metrics,
    )
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert exc.value.code == "false_metrics_completion"


def test_serving_metrics_coverage_retains_gaps_and_rejects_false_completion(tmp_path):
    value = measured(tmp_path)
    value["completeness"] = "incomplete"
    value["summary"] = {"completed": False, "status": "closed_partial"}
    value["run"]["profile"] = "serving-comparison/v1"
    value["metrics_coverage"] = _serving_coverage(tmp_path, status="partial", measured=False)
    checked = validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert checked["metrics_coverage"]["missing_required_metrics"] == [
        "baseline:c1.decode_tok_s", "candidate:c1.decode_tok_s",
    ]
    value["metrics_coverage"]["status"] = "complete"
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert exc.value.code == "false_metrics_completion"


@pytest.mark.parametrize("mutation", (
    lambda coverage: coverage["required_cells"].append("baseline:c1"),
    lambda coverage: coverage["cells"]["baseline:c1"]["metrics"].pop("ttft_ms"),
    lambda coverage: coverage["cells"]["baseline:c1"]["metrics"].__setitem__(
        "ttft_ms", {"status": "configured", "value": 0, "reason": "manifest", "evidence": coverage["cells"]["baseline:c1"]["metrics"]["ttft_ms"]["evidence"]}),
))
def test_serving_metrics_coverage_rejects_malformed_or_nonmeasured_complete_cells(tmp_path, mutation):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    value["metrics_coverage"] = _serving_coverage(tmp_path)
    mutation(value["metrics_coverage"])
    with pytest.raises(BenchmarkArtifactError):
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))


def test_serving_metrics_evidence_hash_is_verified(tmp_path):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    value["metrics_coverage"] = _serving_coverage(tmp_path)
    (tmp_path / "raw" / "stage.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert exc.value.code == "evidence_digest_mismatch"


def test_path_summary_marks_tampered_serving_coverage_unverified(tmp_path):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    value["metrics_coverage"] = _serving_coverage(tmp_path)
    artifact = tmp_path / "campaign.json"
    artifact.write_text(json.dumps(value), encoding="utf-8")
    (tmp_path / "raw" / "stage.json").write_text("tampered", encoding="utf-8")
    summary = benchmark_evidence.summarize_artifact(artifact)
    assert summary["metrics_coverage"]["verified"] is False
    assert summary["validation_errors"][0].startswith("evidence_digest_mismatch:")


def test_serving_coverage_accepts_two_arms_two_contexts_two_concurrencies(tmp_path):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    coverage = _serving_coverage(tmp_path)
    plan_path = tmp_path / coverage["frozen_plan"]["path"]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    cells = {}
    for arm in ("baseline", "candidate"):
        for context in (8192, 32768):
            for concurrency in (1, 2):
                cell_id = f"{arm}:{context}:c{concurrency}"
                cells[cell_id] = {"arm": arm, "workload": f"natural-{context}", "context_tokens": context, "concurrency": concurrency, "requests": 1}
    plan["required_cells"] = cells
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    coverage["frozen_plan"] = evidence_file_reference(str(plan_path), root=str(tmp_path))
    coverage["required_cells"] = list(cells)
    coverage["cells"] = {cell_id: copy.deepcopy(coverage["cells"]["baseline:c1"]) for cell_id in cells}
    for cell_id, cell in coverage["cells"].items():
        raw = tmp_path / "raw" / f"{cell_id}.json"
        raw.write_text(json.dumps({"schema": "anvil-serving.benchmark/v1", "model": cells[cell_id]["arm"], "context_tokens": cells[cell_id]["context_tokens"], "concurrency": cells[cell_id]["concurrency"], "requests": cells[cell_id]["requests"], "completed": cells[cell_id]["requests"], "failed": 0, "request_timings": [{}] * cells[cell_id]["requests"], "workload_identity": {"workload_kind": cells[cell_id]["workload"]}}), encoding="utf-8")
        receipt = tmp_path / "raw" / f"{cell_id}-receipt.json"
        receipt.write_text(json.dumps({"profile": "serving-comparison/v1", "arm": cells[cell_id]["arm"], "identity": plan["arms"][cells[cell_id]["arm"]], "cell": cells[cell_id], "raw": evidence_file_reference(str(raw), root=str(tmp_path))}), encoding="utf-8")
        cell["measurement"] = evidence_file_reference(str(receipt), root=str(tmp_path))
        raw_ref = evidence_file_reference(str(raw), root=str(tmp_path))
        for metric in cell["metrics"].values():
            if metric.get("status") == "measured":
                metric["evidence"] = [raw_ref]
        cell["metrics"]["concurrency"]["observation"]["requested"] = cells[cell_id]["concurrency"]
        cell["metrics"]["concurrency"]["observation"]["client_max"] = cells[cell_id]["concurrency"]
        cell["metrics"]["concurrency"]["observation"]["engine_max"] = cells[cell_id]["concurrency"]
    value["metrics_coverage"] = coverage
    assert validate_cross_suite_evidence(value, artifact_root=str(tmp_path))["metrics_coverage"]["status"] == "complete"


@pytest.mark.parametrize("mutation", (
    lambda plan: plan["required_cells"].pop("candidate:c1"),
    lambda plan: plan["required_cells"].__setitem__("candidate:c1", {"arm": "candidate", "workload": "short", "context_tokens": 2, "concurrency": 1, "requests": 1}),
    lambda plan: plan.__setitem__("source_revision", "a" * 40),
    lambda plan: plan.__setitem__("arms", []),
))
def test_serving_plan_binding_rejects_missing_or_mismatched_matrix(tmp_path, mutation):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    coverage = _serving_coverage(tmp_path)
    plan_path = tmp_path / coverage["frozen_plan"]["path"]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mutation(plan)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    coverage["frozen_plan"] = evidence_file_reference(str(plan_path), root=str(tmp_path))
    value["metrics_coverage"] = coverage
    if plan.get("source_revision") == "a" * 40:
        assert validate_cross_suite_evidence(value, artifact_root=str(tmp_path))["metrics_coverage"]["status"] == "complete"
    else:
        with pytest.raises(BenchmarkArtifactError):
            validate_cross_suite_evidence(value, artifact_root=str(tmp_path))


@pytest.mark.parametrize("metric,record", (
    ("ttft_ms", {"status": "measured", "value": 0, "evidence": []}),
    ("ttft_ms", {"status": "measured", "observation": {"samples": 1, "p50": float("nan"), "p95": 1}, "evidence": []}),
    ("gpu_memory", {"status": "measured", "observation": {"accounting": "only"}, "evidence": []}),
))
def test_serving_measurement_type_guards(tmp_path, metric, record):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    value["metrics_coverage"] = _serving_coverage(tmp_path)
    record["evidence"] = value["metrics_coverage"]["cells"]["baseline:c1"]["metrics"][metric]["evidence"]
    value["metrics_coverage"]["cells"]["baseline:c1"]["metrics"][metric] = record
    with pytest.raises(BenchmarkArtifactError):
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))


def test_serving_coverage_rejects_baseline_receipt_reused_for_candidate(tmp_path):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    value["metrics_coverage"] = _serving_coverage(tmp_path)
    value["metrics_coverage"]["cells"]["candidate:c1"] = copy.deepcopy(
        value["metrics_coverage"]["cells"]["baseline:c1"]
    )
    with pytest.raises(BenchmarkArtifactError, match="does not match frozen plan"):
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))


def test_serving_evidence_cli_rejects_tampered_references(tmp_path, capsys):
    value = measured(tmp_path)
    value["run"]["profile"] = "serving-comparison/v1"
    value["metrics_coverage"] = _serving_coverage(tmp_path)
    path = tmp_path / "campaign.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert benchmark_evidence.main(["show", str(path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["metrics_coverage"]["verified"] is True
    (tmp_path / "raw" / "baseline.json").write_text("tampered", encoding="utf-8")
    assert benchmark_evidence.main(["show", str(path), "--format", "json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["metrics_coverage"]["verified"] is False
    assert result["validation_errors"][0].startswith("evidence_digest_mismatch:")
    assert benchmark_evidence.main(["compare", str(path), str(path), "--format", "json"]) == 1


def test_partial_cell_preserves_failed_native_population(tmp_path):
    value = measured(tmp_path)
    value["completeness"] = "incomplete"
    value["summary"] = {"completed": False}
    value["run"]["profile"] = "serving-comparison/v1"
    coverage = _serving_coverage(tmp_path, status="partial", measured=False)
    cell = coverage["cells"]["baseline:c1"]
    raw_path = tmp_path / "raw" / "baseline.json"
    raw = json.loads(raw_path.read_text())
    raw.update(completed=0, failed=1, request_timings=[])
    raw_path.write_text(json.dumps(raw))
    receipt_path = tmp_path / cell["measurement"]["path"]
    receipt = json.loads(receipt_path.read_text())
    receipt["raw"] = evidence_file_reference(str(raw_path), root=str(tmp_path))
    receipt_path.write_text(json.dumps(receipt))
    cell["measurement"] = evidence_file_reference(str(receipt_path), root=str(tmp_path))
    cell["metrics"] = {name: {"status": "blocked", "reason": "request failed"}
                       for name in cell["metrics"]}
    value["metrics_coverage"] = coverage
    assert validate_cross_suite_evidence(value, artifact_root=str(tmp_path))["metrics_coverage"]["status"] == "partial"


def test_unrun_cell_retains_blocked_reasons_without_measurement(tmp_path):
    value = measured(tmp_path)
    value["completeness"] = "incomplete"
    value["summary"] = {"completed": False}
    value["run"]["profile"] = "serving-comparison/v1"
    coverage = _serving_coverage(tmp_path, status="partial", measured=False)
    cell = coverage["cells"]["baseline:c1"]
    cell["measurement"] = None
    cell["metrics"] = {name: {"status": "blocked", "reason": "prior OOM stopped this cell"}
                       for name in cell["metrics"]}
    value["metrics_coverage"] = coverage
    checked = validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert checked["metrics_coverage"]["cells"]["baseline:c1"]["metrics"]["ttft_ms"]["reason"] == "prior OOM stopped this cell"
    cell["metrics"]["ttft_ms"] = copy.deepcopy(coverage["cells"]["candidate:c1"]["metrics"]["ttft_ms"])
    with pytest.raises(BenchmarkArtifactError):
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
