
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


def test_debug_loop_discloses_unit_scope_and_accepts_semantic_pass_report():
    scenario, expected = build_agentic_scenario("debug-loop")
    assert "unit tests" in scenario["messages"][0]["content"]
    edit = next(tool["function"] for tool in scenario["tools"]
                if tool["function"]["name"] == "apply_edit")
    contract = edit["parameters"]["properties"]["edit"]["description"]
    assert "complete replacement source line" in contract
    assert "literal text without a trailing newline" in contract
    assert "description of the change" in contract
    trace = passing_trace(expected)
    trace["final_answer"] = "The requested fix is applied and the unit tests now pass."

    result = score_agentic_trace(scenario, expected, trace)

    assert result["passed"] is True
    assert result["stages"]["result_incorporation"] == {"passed": True}

    trace["tool_calls"] = [dict(call) for call in expected["tool_calls"]]
    trace["tool_calls"][2] = {
        "name": "apply_edit",
        "arguments": {"path": "calc.py", "edit": "Replace subtraction with addition"},
    }
    assert score_agentic_trace(scenario, expected, trace)["failure_class"] == "protocol_failure"


def test_debug_loop_v2_is_a_distinct_explicit_unit_scope_contract():
    legacy, legacy_expected = build_agentic_scenario("debug-loop")
    scenario, expected = build_agentic_scenario("debug-loop-v2")

    assert legacy["scenario_id"] == "debug-loop"
    assert "oracle_revision" not in legacy
    assert scenario["scenario_id"] == "debug-loop-v2"
    assert scenario["oracle_revision"] == "9"
    assert expected["oracle_revision"] == "9"
    assert "scope `unit`" in scenario["messages"][0]["content"]
    run_tests = next(tool["function"] for tool in scenario["tools"]
                     if tool["function"]["name"] == "run_tests")
    assert run_tests["parameters"]["properties"]["scope"]["enum"] == ["unit"]

    assert score_agentic_trace(legacy, legacy_expected, passing_trace(legacy_expected))["oracle_revision"] == "8"
    assert score_agentic_trace(scenario, expected, passing_trace(expected))["oracle_revision"] == "9"
    wrong_scope = passing_trace(expected)
    wrong_scope["tool_calls"] = [dict(call) for call in expected["tool_calls"]]
    wrong_scope["tool_calls"][0] = {"name": "run_tests", "arguments": {"scope": "all"}}
    assert score_agentic_trace(scenario, expected, wrong_scope)["failure_class"] == "protocol_failure"


def test_context_recovery_accepts_one_measured_long_history_request():
    scenario, expected = build_agentic_scenario("context-recovery", session_turns=8)
    trace = passing_trace(expected)
    trace["history_prompt_tokens"] = [900]

    result = score_agentic_trace(scenario, expected, trace)

    assert result["passed"] is True
    assert result["stages"]["history"] == {"passed": True}
