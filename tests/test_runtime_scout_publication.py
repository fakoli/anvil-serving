"""Reconcile the public scout claims with retained request-level evidence."""

import hashlib
import json
from pathlib import Path
import re
import statistics


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/findings/2026-11-01-runtime-scouts-evidence"


def test_runtime_scout_publication_matches_native_evidence():
    finding = (ROOT / "docs/findings/2026-11-01-runtime-scouts.md").read_text()
    for trial in ("cache-layout", "dflash"):
        directory = EVIDENCE / trial
        summary = json.loads((directory / "summary.json").read_text())
        request_count = 0
        for cell in summary["cells"]:
            displayed = []
            for arm in ("baseline-before", "candidate", "baseline-after"):
                documents = [
                    json.loads((directory / f"{arm}-{index:02}.json").read_text())
                    for index in range(12)
                ]
                matching = [
                    document for document in documents
                    if document["context_tokens"] == cell["nominal_context"]
                    and document["concurrency"] == cell["concurrency"]
                ]
                rows = [row for document in matching for row in document["request_timings"]]
                assert len(rows) == 12
                assert all(document["completed"] == 4 and document["failed"] == 0 for document in matching)
                median = statistics.median(row["e2e_ms"] for row in rows)
                assert median == cell["arms"][arm]["e2e_ms"]
                displayed.append(f"{median / 1000:.3f}")
                request_count += len(rows)
            assert " | ".join(displayed) in finding
            assert not all(
                cell["arms"]["candidate"]["e2e_ms"] <= cell["arms"][control]["e2e_ms"] * 0.9
                for control in ("baseline-before", "baseline-after")
            )
        assert request_count == summary["measured_requests"] == 144
        assert summary["restored"] and not summary["promoted"]
        for arm in ("baseline-before", "candidate", "baseline-after"):
            allocation = json.loads((directory / f"{arm}-allocation.json").read_text())
            cache_tokens = int(re.search(r'kv_cache_size_tokens="(\d+)"', allocation["engine_metrics"])[1])
            assert cache_tokens == summary["resources"][arm]["engine_reported_cache_tokens"]
        assert all(summary["restoration_matches"].values())
        assert all(summary["preflights"].values())

    manifest = json.loads((EVIDENCE / "artifact-manifest.json").read_text())
    for role in manifest["artifact_roles"]:
        assert role["status"] != "pending"
        for entry in role["files"]:
            payload = (EVIDENCE / entry["path"]).read_bytes()
            assert len(payload) == entry["bytes"]
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]

    for relative in (
        "docs/findings/README.md", "docs/benchmarks/runs.md",
        "docs/benchmarks/models/private-runtime-scouts.md",
        "docs/benchmarks/hardware/rtx-pro-6000.md", "docs/benchmarks/index.md",
    ):
        assert "2026-11-01-runtime-scouts.md" in (ROOT / relative).read_text()
