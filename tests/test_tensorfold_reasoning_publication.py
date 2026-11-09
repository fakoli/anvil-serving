"""Reconcile effort-control claims with native grades and transport metadata."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/findings/2026-11-09-tensorfold-reasoning-budget-evidence"


def test_tensorfold_reasoning_publication_matches_retained_evidence():
    observations = json.loads((BUNDLE / "case-diagnostics.json").read_text())
    summary = json.loads((BUNDLE / "summary.json").read_text())
    finding = (ROOT / "docs/findings/2026-11-09-tensorfold-reasoning-budget.md").read_text()
    counts = {effort: {"selected": 0, "resolved": 0} for effort in ("max", "low")}
    assert [cell["id"] for cell in observations["cells"]] == ["max-1", "low-1", "low-2", "max-2"]
    for cell in observations["cells"]:
        directory = BUNDLE / "jobs" / cell["id"]
        result = json.loads((directory / "aider/result.json").read_text())
        events = [json.loads(line) for line in (directory / "aider/requests.jsonl").read_text().splitlines()]
        responses = [row for row in events if row["event"] == "aider_transport_response"]
        tests = [row for row in events if row["event"] == "aider_contained_command_finished"]
        native = json.loads((directory / "aider/results/go/exercises/practice/alphametics/.aider.results.json").read_text())
        assert cell["effort"] == result["request_controls"]["reasoning_effort"]
        assert cell["summary"] == result["summary"]
        assert cell["output_budget"] == result["output_budget"]
        assert cell["whole_job_budget"] == result["budget"]
        assert cell["test_outcomes"] == native["tests_outcomes"] == [row["returncode"] == 0 for row in tests]
        assert all(not row["timed_out"] for row in tests)
        assert result["summary"]["resolved"] == int(any(native["tests_outcomes"]))
        assert result["request_accounting"]["status"] == "complete"
        assert result["request_accounting"]["errors"] == result["request_accounting"]["pending"] == 0
        assert result["request_accounting"]["started"] == result["request_accounting"]["responses"] == len(responses)
        assert result["budget"]["configured"]["deadline_s"] == 600
        assert result["budget"]["configured"]["max_completion_tokens"] == 16384
        assert not result["budget"]["exhausted"]
        for event, projected in zip(responses, result["output_budget"]["responses"], strict=True):
            assert event["usage"] == projected["usage"]
            assert event["finish_reason"] == projected["finish_reason"]
            assert projected["non_reasoning_tokens"] == event["usage"]["completion_tokens"] - event["usage"]["reasoning_tokens"]
            assert projected["visible_tokens"] is None
        first = responses[0]["usage"]
        assert f'{first["reasoning_tokens"]:,} / {first["completion_tokens"]:,}' in finding
        for field in ("selected", "resolved"):
            counts[cell["effort"]][field] += result["summary"][field]
    assert len(observations["cells"]) == 4 and counts == summary["results_by_effort"]
    assert counts["low"] == {"selected": 2, "resolved": 2}
    assert summary["workload"]["unique_tasks"] == 1
    assert not summary["decision"]["promotion_authorized"]
    for page in ("docs/findings/README.md", "docs/benchmarks/index.md", "docs/benchmarks/runs.md",
                 "docs/benchmarks/models/glm53-flash.md", "docs/benchmarks/hardware/rtx-pro-6000.md"):
        assert "2026-11-09-tensorfold-reasoning-budget.md" in (ROOT / page).read_text()
    manifest = json.loads((BUNDLE / "artifact-manifest.json").read_text())
    for role in manifest["artifact_roles"]:
        assert role["status"] != "pending"
        for entry in role["files"]:
            payload = (BUNDLE / entry["path"]).read_bytes()
            assert len(payload) == entry["bytes"]
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
