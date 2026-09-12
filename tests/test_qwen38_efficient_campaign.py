"""Reconcile the bounded RTX 5090 decision with retained native evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/findings/2026-09-12-qwen38-efficient-variants-rtx5090-evidence"


def read(name):
    return json.loads((BUNDLE / name).read_text(encoding="utf-8"))


def test_final_summary_reconciles_quality_and_capacity_without_hiding_failures():
    summary = read("summary.json")
    assert summary["complete"] is True
    assert summary["decision"]["selected_profile"] == "incumbent"
    assert summary["decision"]["challenger_qualified"] is False
    assert summary["decision"]["promotion_authorized"] is True
    assert summary["decision"]["promoted"] is False
    for lane in ("thinking_enabled", "thinking_disabled"):
        for result in summary["results"][lane].values():
            raw = read(result["source"])
            attempts = [a for suite in raw["suites"].values()
                        for check in suite["checks"] for a in check["attempts"]]
            assert result["attempts"] == len(attempts)
            assert result["passed"] == sum(a["status"] == "passed" for a in attempts)
            assert result["completion_tokens"] == sum(a["usage"]["completion_tokens"] for a in attempts)
            assert result["source_sha256"] == hashlib.sha256((BUNDLE / result["source"]).read_bytes()).hexdigest()
    for result in summary["results"]["capacity_32"].values():
        raw = read(result["source"])
        assert raw["response_words"] == 32
        assert raw["controlled_output_policy"] == "strict"
        assert result["completed"] == raw["completed"]
        assert result["requests"] == raw["requests"]
    for name in ("incumbent-capacity-thinking-disabled.json", "signal-capacity-strict.json",
                 "swift-capacity-strict.json", "signal-nospec-capacity-strict.json",
                 "swift-nospec-capacity-strict.json"):
        raw = read(name)
        assert raw["completed"] == 0 and raw["failed"] == 5


def test_restoration_and_four_original_functional_gates_are_retained():
    for name in ("signal-preflight.json", "swift-preflight.json", "qwopus-preflight.json",
                 "minitron-preflight.json", "incumbent-restored-preflight.json"):
        raw = read(name)
        assert set(raw["checks"]) == {"smoke", "json", "tools", "streaming-tools", "tool-result", "needle"}
        assert raw["observations"] and all(o["passed"] for o in raw["observations"])
    restoration = read("restoration.json")
    assert restoration["verified"] is True
    assert restoration["ending_state"]["incumbent_identity_unchanged"] is True
    assert restoration["ending_state"]["candidate_containers_remaining"] == 0
    assert (BUNDLE / "recipes.toml").read_bytes() == (ROOT / "configs/qwen38-efficient-rtx5090-recipes.toml").read_bytes()
    assert (BUNDLE / "nospec-controls.toml").read_bytes() == (ROOT / "configs/qwen38-efficient-rtx5090-nospec-controls.toml").read_bytes()


def test_final_artifact_set_hashes_and_publication_reachability():
    manifest = read("artifact-manifest.json")
    roles = manifest["artifact_roles"]
    assert len(roles) == len({r["role"] for r in roles}) == 10
    for role in roles:
        assert role["status"] in {"retained", "missing", "not-applicable"}
        for item in role["files"]:
            path = (BUNDLE / item["path"]).resolve()
            assert path.is_relative_to(BUNDLE.resolve())
            payload = path.read_bytes()
            assert len(payload) == item["bytes"]
            assert hashlib.sha256(payload).hexdigest() == item["sha256"]
    finding = "2026-09-12-qwen38-efficient-variants-rtx5090.md"
    for relative in ("docs/findings/README.md", "docs/benchmarks/runs.md",
                     "docs/benchmarks/hardware/rtx-5090.md", "docs/benchmarks/models/qwen38-efficient-variants.md"):
        assert finding in (ROOT / relative).read_text(encoding="utf-8")
