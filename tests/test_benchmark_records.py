import json
from pathlib import Path

from anvil_serving.benchmark_evidence import discover_artifacts
from anvil_serving.benchmarking import records

REPO = Path(__file__).resolve().parents[1]


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")


def _capacity(concurrency: int, throughput: float) -> dict:
    return {
        "schema": "anvil-serving.benchmark/v1",
        "run_id": f"run-c{concurrency}",
        "model": "example-model",
        "engine": "vllm",
        "gpu": "lab-rtx-pro-6000",
        "requests": 8,
        "completed": 7,
        "concurrency": concurrency,
        "context_tokens": 8192,
        "max_tokens": 256,
        "performance_eligible": True,
        "metrics": {
            "ttft_p50_ms": 120.0, "ttft_p95_ms": 180.0, "e2e_p50_ms": 2400.0,
            "output_tokens": 1792, "throughput_tok_s": throughput, "decode_tok_s_p50": 95.5,
        },
    }


def _findings(tmp_path: Path) -> Path:
    root = tmp_path / "findings"
    _write(root / "2026-11-01-example.md", "# Example result\n\n**Decision:** no promotion; retained baseline.\n")
    evidence = root / "2026-11-01-example-evidence"
    _write(evidence / "README.md", "# Evidence\n")
    _write(evidence / "artifact-manifest.json", {
        "schema": "anvil-serving.benchmark-artifact-set/v1",
        "campaign": {"id": "2026-11-01-example", "evidence_labels": ["capacity", "local-quality"],
                     "decision_labels": ["challenger"], "promotion_authorized": False},
        "artifact_roles": [{"role": "raw-run-evidence", "status": "retained"}],
    })
    _write(evidence / "summary.json", {
        "schema": "anvil-serving.benchmark-decision-summary/v1",
        "identity": {"quantization": "NVFP4"},
        "headline_results": [{"metric": "decode p50", "value": 95.5, "unit": "tok/s"}],
        "limitations": ["Single repetition."],
    })
    _write(evidence / "raw" / "c1.json", _capacity(1, 95.0))
    _write(evidence / "raw" / "c4.json", _capacity(4, 310.0))
    _write(root / "2026-10-01-prose-only.md", "# Prose only\n\n**Decision:** rejected.\n")
    return root


def test_record_joins_finding_bundle_and_artifacts(tmp_path):
    index = records.build_index(_findings(tmp_path))
    by_id = {record["id"]: record for record in index["records"]}
    record = by_id["2026-11-01-example"]

    assert record["schema"] == records.RECORD_SCHEMA
    assert record["title"] == "Example result"
    assert record["categories"] == ["capacity", "quality"]
    assert record["decision"]["labels"] == ["challenger"]  # manifest wins over prose
    assert record["subject"] == {"model": "example-model", "engine": "vllm",
                                 "quantization": "NVFP4", "hardware": "RTX PRO 6000"}
    assert record["workload"] == {"context_tokens": [8192], "concurrency": [1, 4]}
    assert record["failures"]["failed_requests"] == 2
    assert record["gaps"] == []
    assert record["links"]["evidence_index"] == "2026-11-01-example-evidence/README.md"
    assert [point["aggregate_output_tok_s"] for point in index["capacity_points"]] == [95.0, 310.0]
    assert index["capacity_points"][0]["per_user_decode_tok_s_p50"] == 95.5


def test_missing_values_are_explicit_gaps(tmp_path):
    index = records.build_index(_findings(tmp_path))
    record = {item["id"]: item for item in index["records"]}["2026-10-01-prose-only"]

    assert record["decision"]["labels"] == ["rejected"]  # from the finding's Decision line
    assert record["subject"]["quantization"] == records.NOT_RECORDED
    assert record["gaps"] == ["model", "engine", "quantization", "hardware",
                              "context_tokens", "concurrency", "evidence bundle"]
    assert [item["id"] for item in index["records"]] == ["2026-11-01-example", "2026-10-01-prose-only"]


def test_renderers_show_card_and_table(tmp_path):
    index = records.build_index(_findings(tmp_path))
    card = records.render_card(index["records"][0])
    table = records.render_table(index, link_prefix="../findings/")

    assert card.startswith('??? abstract "Benchmark record')
    assert "| Quantization | NVFP4 |" in card and "decode p50: 95.5 tok/s" in card
    assert "[Example result](../findings/2026-11-01-example.md)" in table
    assert "evidence bundle" in table


def test_hardware_labels_are_canonical():
    assert records.hardware_label("dark-auxiliary-rtx5090") == "RTX 5090"
    assert records.hardware_label("2x RTX PRO 6000 Blackwell Max-Q") == "RTX PRO 6000"
    assert records.hardware_label("lab-host-tp2") == records.UNIDENTIFIED_HARDWARE


def test_repository_findings_build_and_discover():
    index = records.build_index(REPO / "docs" / "findings")
    assert index["counts"]["records"] > 100
    derivative_record = {record["id"]: record for record in index["records"]}[
        "2026-11-21-glm-derivative-survey"
    ]
    assert derivative_record["title"] == "AZ-Focus and Mia GLM-5.3-Flash derivatives: matched fixed-set result"
    assert derivative_record["links"]["finding"] == "2026-11-21-glm-derivative-survey.md"
    for record in index["records"]:
        assert set(records.CORE_FIELDS) - {"decision", "context_tokens", "concurrency"} <= set(record["subject"])
    # Campaign summaries have no quality block; discovery must not crash on them.
    assert discover_artifacts(REPO / "docs" / "findings", kind="campaign", limit=1000)["matched"] > 0
