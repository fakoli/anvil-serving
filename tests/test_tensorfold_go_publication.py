"""Keep the bounded Go publication tied to independent grades and wire usage."""
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "docs/findings/2026-11-08-tensorfold-go-validation-evidence"


def test_tensorfold_go_publication_matches_retained_results():
    observations = json.loads((BUNDLE / "case-diagnostics.json").read_text())
    summary = json.loads((BUNDLE / "summary.json").read_text())
    finding = (ROOT / "docs/findings/2026-11-08-tensorfold-go-validation.md").read_text()
    totals = {"responses": 0, "prompt_tokens": 0, "completion_tokens": 0}
    for cell in observations["cells"]:
        cap = cell["completion_cap_tokens_per_response_including_reasoning"]
        result = json.loads((BUNDLE / cell["native_result"]).read_text())
        events = [json.loads(line) for line in (BUNDLE / cell["native_events"]).read_text().splitlines()]
        responses = [row for row in events if row["event"] == "aider_transport_response"]
        tests = [row for row in events if row["event"] == "aider_contained_command_finished"]
        assert cell["response_generation_limit"] == result["output_budget"]
        assert cell["whole_job_budget"] == result["budget"]
        assert cell["contained_tests"] == tests
        assert cell["request_accounting"] == result["request_accounting"] == {
            "schema": "anvil-serving.aider-request-accounting/v1", "status": "complete",
            "started": 2, "responses": 2, "errors": 0, "pending": 0,
        }
        native = json.loads((BUNDLE / f"go-{cap}/aider/results/go/exercises/practice/alphametics/.aider.results.json").read_text())
        assert native["tests_outcomes"] == ([False, False] if cap == 16384 else [True])
        assert result["summary"]["resolved"] == int(any(native["tests_outcomes"]))
        assert [row["returncode"] for row in tests] == ([1, 1] if cap == 16384 else [0])
        assert all(not row["timed_out"] for row in tests)
        assert not result["budget"]["exhausted"]
        for raw, projected in zip(responses, result["output_budget"]["responses"], strict=True):
            assert raw["usage"] == projected["usage"]
            assert raw["finish_reason"] == projected["finish_reason"] == ("length" if cap == 16384 else "stop")
            assert projected["non_reasoning_tokens"] == raw["usage"]["completion_tokens"] - raw["usage"]["reasoning_tokens"]
            assert projected["visible_tokens"] is None
            totals["responses"] += 1
            for key in ("prompt_tokens", "completion_tokens"):
                totals[key] += raw["usage"][key]
    assert totals == json.loads((BUNDLE / "counter-reconciliation.json").read_text())["native_totals"]
    assert observations["interpretation"]["unique_tasks"] == summary["workload"]["unique_tasks"] == 1
    assert not summary["decision"]["promotion_authorized"]
    assert "1 / 2" in finding and "0 / 2 passed" in finding and "1 / 1 passed" in finding
    for page in ("docs/findings/README.md", "docs/benchmarks/index.md", "docs/benchmarks/runs.md",
                 "docs/benchmarks/models/glm53-flash.md", "docs/benchmarks/hardware/rtx-pro-6000.md"):
        assert "2026-11-08-tensorfold-go-validation.md" in (ROOT / page).read_text()
    manifest = json.loads((BUNDLE / "artifact-manifest.json").read_text())
    for role in manifest["artifact_roles"]:
        assert role["status"] != "pending"
        for entry in role["files"]:
            payload = (BUNDLE / entry["path"]).read_bytes()
            assert len(payload) == entry["bytes"]
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
