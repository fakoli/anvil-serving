"""Critical external-evidence admission and verdict guards."""
import pytest

from anvil_serving.external_benchmarks import notebook, store


def _evidence(candidate="candidate", *, run_id="run"):
    return {
        "schema": "anvil-serving.fast-tier-bakeoff/v1",
        "run_id": run_id,
        "identity": {"candidate_id": candidate, "config_id": "cfg", "model": "m", "base_url": "http://x"},
        "source_recipe": {"ref": "recipe", "serve_command": "serve"},
        "evaluation_protocol": {"version": 3, "repetitions": 3},
        "suites": {"ranking": {"status": "passed", "evidence_use": "ranking", "validator_strength": "exact_choice",
                                  "checks": [{"status": "passed", "attempt_count": 3, "pass_count": 3}]}},
        "score_inputs": {"voice_latency_ms": 800.0, "intelligence_pass_rate": 1.0,
                          "tool_call_passed": True, "session_recall_passed": True,
                          "usable_context_tokens": 65536},
        "failures": [],
    }


@pytest.mark.parametrize("mutation", ["legacy", "diagnostic", "weak", "single"])
def test_record_rejects_non_ranking_protocol_evidence(mutation, tmp_path):
    evidence = _evidence()
    if mutation == "legacy":
        evidence.pop("evaluation_protocol")
    elif mutation == "diagnostic":
        evidence["suites"]["ranking"]["evidence_use"] = "diagnostic"
    elif mutation == "weak":
        evidence["suites"]["ranking"]["validator_strength"] = "deterministic_marker"
    else:
        evidence["suites"]["ranking"]["checks"][0].update(attempt_count=1, pass_count=1)
    with pytest.raises(ValueError):
        store.record_bakeoff_run(tmp_path / "notebook.sqlite", evidence, task="fast", hardware="fixture")


def test_verdict_hard_gate_beats_score():
    row = {"candidate_id": "candidate", "config_id": "cfg", "task": "fast", "hardware": "fixture",
           **_evidence()["score_inputs"], "tool_call_passed": False, "failures_json": "[]"}
    verdict = notebook.verdict(row)
    assert verdict["result"] == "lose" and "tool_call_passed" in verdict["reason"]
