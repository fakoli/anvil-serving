from __future__ import annotations

import copy
import json

import pytest

from anvil_serving.benchmarking.artifacts import (
    BenchmarkArtifactError,
    CROSS_SUITE_EVIDENCE_SCHEMA,
    PROMOTION_DISCLAIMER,
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


def test_referenced_file_tampering_is_detected(tmp_path):
    value = measured(tmp_path)
    (tmp_path / "raw" / "stage.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(BenchmarkArtifactError) as exc:
        validate_cross_suite_evidence(value, artifact_root=str(tmp_path))
    assert exc.value.code == "evidence_digest_mismatch"


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
