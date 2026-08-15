import pytest

from anvil_serving.benchmarking.agentic import (
    RECOVERY_RESULT_TYPES,
    build_agentic_scenario,
    build_recovery_matrix,
    score_agentic_trace,
)


def passing_trace(expected):
    final = expected["final"]
    if expected.get("final_kind") == "exact_json":
        import json

        final = json.dumps(final)
    return {
        "tool_calls": expected["tool_calls"],
        "final_answer": final,
        "history": expected.get("history", []),
        "history_prompt_tokens": [100, 200] if expected.get("history") else [],
    }


@pytest.mark.parametrize(
    "scenario_type",
    [
        "planning",
        "reasoning",
        "structured-edit",
        "tool-sequence",
        "parallel-tools",
        "dependent-result",
        "debug-loop",
    ],
)
def test_deterministic_scenarios_pass_without_model_judge(scenario_type):
    scenario, expected = build_agentic_scenario(scenario_type)
    result = score_agentic_trace(scenario, expected, passing_trace(expected))
    assert result["passed"] is True
    assert result["failure_class"] is None


def test_recovery_matrix_covers_all_required_result_failures():
    matrix = build_recovery_matrix()
    assert [expected["recovery_result"] for _scenario, expected in matrix] == list(
        RECOVERY_RESULT_TYPES
    )
    for scenario, expected in matrix:
        result = score_agentic_trace(scenario, expected, passing_trace(expected))
        assert result["stages"]["recovery"]["passed"] is True


def test_protocol_recovery_and_final_failures_are_distinct():
    scenario, expected = build_agentic_scenario("tool-recovery")
    protocol = passing_trace(expected)
    protocol["tool_calls"] = []
    assert score_agentic_trace(scenario, expected, protocol)["failure_class"] == "protocol_failure"

    final = passing_trace(expected)
    final["final_answer"] = "wrong"
    assert score_agentic_trace(scenario, expected, final)["failure_class"] == "recovery_failure"


def test_tool_result_marker_allows_natural_language_final_answer():
    scenario, expected = build_agentic_scenario("tool-sequence")
    trace = passing_trace(expected)
    trace["final_answer"] = "The requested file is ready: FILE-READY."

    result = score_agentic_trace(scenario, expected, trace)

    assert result["passed"] is True
    assert result["stages"]["reasoning"] == {"applicable": False, "passed": True}
    assert result["visible_answer"] == trace["final_answer"]
    assert len(result["answer_sha256"]) == 64


def test_tool_final_mismatch_is_not_mislabeled_as_reasoning_failure():
    scenario, expected = build_agentic_scenario("tool-sequence")
    trace = passing_trace(expected)
    trace["final_answer"] = "The file was read successfully."

    result = score_agentic_trace(scenario, expected, trace)

    assert result["failure_class"] == "final_answer_failure"
    assert result["stages"]["reasoning"] == {"applicable": False, "passed": True}


@pytest.mark.parametrize(
    ("code", "classification"),
    [
        ("reasoning_budget_exhausted", "reasoning_budget_exhausted"),
        ("endpoint_error", "infrastructure_failure"),
        ("parser_error", "parser_failure"),
    ],
)
def test_exhaustion_infrastructure_and_parser_failures_stay_distinct(code, classification):
    scenario, expected = build_agentic_scenario("reasoning")
    trace = passing_trace(expected)
    trace["failure"] = {"code": code}
    trace["final_answer"] = ""
    assert score_agentic_trace(scenario, expected, trace)["failure_class"] == classification


def test_long_session_preserves_full_history_and_token_growth():
    scenario, expected = build_agentic_scenario("long-session", session_turns=8)
    trace = passing_trace(expected)
    trace["history_prompt_tokens"] = [100, 250, 500, 900]
    result = score_agentic_trace(scenario, expected, trace)
    assert result["passed"] is True
    assert result["full_history"] == scenario["messages"]
    assert result["history_prompt_tokens"] == [100, 250, 500, 900]


def test_context_recovery_accepts_one_measured_long_history_request():
    scenario, expected = build_agentic_scenario("context-recovery", session_turns=8)
    trace = passing_trace(expected)
    trace["history_prompt_tokens"] = [900]

    result = score_agentic_trace(scenario, expected, trace)

    assert result["passed"] is True
    assert result["stages"]["history"] == {"passed": True}
