"""Reconcile published campaign numbers and inventory with sanitized native evidence."""

import hashlib
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/findings/2026-11-03-rtx5090-campaign-evidence"


def read(name):
    return json.loads((BUNDLE / name).read_text(encoding="utf-8"))


def native(name):
    value = read("raw/" + name)
    while isinstance(value.get("data"), dict):
        value = value["data"]
    return value


def test_campaign_publication_matches_native_evidence():
    summary = read("summary.json")
    assert summary["decision"] == "no-promotion"
    assert not summary["promotion_authorized"]
    assert len(summary["headline_results"]) == 19
    table = (BUNDLE / "coding-results.md").read_text(encoding="utf-8")
    for row in summary["headline_results"]:
        raw, grade = read(row["raw"]), read(row["grade"])
        attempts = [
            attempt
            for suite in raw["suites"].values()
            for check in suite["checks"]
            for attempt in check["attempts"]
        ]
        assert len(attempts) == 21
        latencies = [attempt["latency_ms"] for attempt in attempts]
        assert abs(statistics.median(latencies) - row["median_wall_ms"]) < 1e-6
        assert abs(sum(latencies) / 1000 - row["sum_wall_s"]) < 1e-6
        graded = [a for task in grade["tasks"].values() for a in task["attempts"]]
        assert sum(a["passed"] for a in graded) == row["passed"] == grade["score"]["passed"]
        assert sum(a["total"] for a in graded) == row["total"] == 123
        assert f'{row["passed"]}/123' in table
        assert f'{row["median_wall_ms"] / 1000:.3f}' in table
        assert f'{row["sum_wall_s"]:.3f}' in table

    for prefix, count, maximum in (
        ("ignis-256k-vision", 45, 245646),
        ("qat-text160k", 45, 155526),
        ("coding-candidate-128k", 225, 122771),
        ("reference-restored", 15, 155526),
    ):
        observations = native(prefix + "-context-raw.json")["observations"]
        assert len(observations) == count
        assert all(o["passed"] for o in observations)
        assert max(o["prompt_tokens"] for o in observations) == maximum
    assert native("coding-candidate-128k-context-artifact.json")["status"] == "failed"
    for prefix in ("ignis-256k-vision", "reference-restored"):
        assert native(prefix + "-multimodal.json")["passed_attempts"] == 18
    for filename, completion_tokens in (
        ("ignis-budget4096-capacity16-120.json", 28),
        ("reference-matched-capacity16-120.json", 29),
        ("qat-reverse-capacity16-120.json", 29),
    ):
        cell = native(filename)
        assert cell["completed"] == 120 and cell["failed"] == 0
        assert cell["metrics"]["completion_tokens_min"] == completion_tokens
        assert cell["metrics"]["completion_tokens_max"] == completion_tokens

    for chart in read("capacity-graph-data.json")["charts"]:
        for series in chart["series"]:
            for point in series["points"]:
                raw = (BUNDLE / point["artifact"]).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == point["artifact_sha256"]

    declared = set()
    manifest = read("artifact-manifest.json")
    assert len(manifest["artifact_roles"]) == 10
    for role in manifest["artifact_roles"]:
        assert role["status"] == "retained"
        for entry in role["files"]:
            raw = (BUNDLE / entry["path"]).read_bytes()
            assert len(raw) == entry["bytes"] < 5 * 1024 * 1024
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
            assert entry["path"] not in declared
            declared.add(entry["path"])
    assert declared == {
        f.relative_to(BUNDLE).as_posix()
        for f in BUNDLE.rglob("*")
        if f.is_file() and f.name not in {"artifact-manifest.json", "artifact-manifest-source.json"}
    }
    for relative in (
        "docs/findings/README.md", "docs/benchmarks/runs.md",
        "docs/benchmarks/models/rtx5090-campaign-candidates.md",
        "docs/benchmarks/hardware/rtx-5090.md",
        "docs/BENCHMARKS.md", "docs/benchmarks/index.md",
    ):
        assert "2026-11-03-rtx5090-campaign.md" in (ROOT / relative).read_text(encoding="utf-8")
