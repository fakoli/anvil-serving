"""Deterministic stage-scored agentic and software-solving scenarios."""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping

from .jobs import BenchmarkJobError


AGENTIC_SCENARIO_SCHEMA = "anvil-serving.agentic-scenario/v1"
AGENTIC_OBSERVATION_SCHEMA = "anvil-serving.agentic-observation/v1"
SCENARIO_TYPES = frozenset({
    "planning",
    "reasoning",
    "structured-edit",
    "tool-sequence",
    "parallel-tools",
    "dependent-result",
    "tool-recovery",
    "debug-loop",
    "context-recovery",
    "long-session",
})
RECOVERY_RESULT_TYPES = (
    "malformed",
    "partial",
    "error",
    "timeout",
    "contradictory",
)


def _tool(name: str, required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Deterministic fixture tool {name}.",
            "parameters": {
                "type": "object",
                "properties": {item: {"type": "string"} for item in required},
                "required": required,
                "additionalProperties": False,
            },
        },
    }


def build_agentic_scenario(
    scenario_type: str,
    *,
    recovery_result: str = "error",
    session_turns: int = 12,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return model-visible fixtures and a separate deterministic grading record."""
    if scenario_type not in SCENARIO_TYPES:
        raise BenchmarkJobError("unknown_agentic_scenario", "agentic scenario is unknown")
    if recovery_result not in RECOVERY_RESULT_TYPES:
        raise BenchmarkJobError("bad_recovery_fixture", "recovery result type is unknown")
    if not isinstance(session_turns, int) or not 2 <= session_turns <= 100:
        raise BenchmarkJobError("bad_session_turns", "session_turns must be from 2 through 100")
    scenario = {
        "schema": AGENTIC_SCENARIO_SCHEMA,
        "scenario_id": scenario_type,
        "scenario_type": scenario_type,
        "messages": [],
        "tools": [],
        "injected_results": [],
    }
    expected: dict[str, Any] = {
        "scenario_id": scenario_type,
        "tool_mode": "none",
        "tool_calls": [],
        "result_markers": [],
        "final": None,
        "final_kind": "normalized_exact",
        "recovery_required": False,
    }
    if scenario_type == "planning":
        scenario["messages"] = [{
            "role": "user",
            "content": "Return only the safe three-step workflow for changing code: inspect, patch, test.",
        }]
        expected["final"] = "inspect -> patch -> test"
    elif scenario_type == "reasoning":
        scenario["messages"] = [{
            "role": "user",
            "content": "A build has 7 groups of 6 tests. Return only the total number of tests.",
        }]
        expected["final"] = "42"
    elif scenario_type == "structured-edit":
        scenario["messages"] = [{
            "role": "user",
            "content": 'Return only JSON describing an edit: {"file":"app.py","line":12,"value":45}.',
        }]
        expected["final"] = {"file": "app.py", "line": 12, "value": 45}
        expected["final_kind"] = "exact_json"
    elif scenario_type == "tool-sequence":
        scenario["messages"] = [{
            "role": "user", "content": "Read ticket T-17, then read the file named by the ticket."
        }]
        scenario["tools"] = [_tool("read_ticket", ["id"]), _tool("read_file", ["path"])]
        scenario["injected_results"] = [
            {"tool": "read_ticket", "result": {"path": "src/app.py"}},
            {"tool": "read_file", "result": {"marker": "FILE-READY"}},
        ]
        expected.update({
            "tool_mode": "sequential",
            "tool_calls": [
                {"name": "read_ticket", "arguments": {"id": "T-17"}},
                {"name": "read_file", "arguments": {"path": "src/app.py"}},
            ],
            "result_markers": ["FILE-READY"],
            "final": "FILE-READY",
        })
    elif scenario_type == "parallel-tools":
        scenario["messages"] = [{
            "role": "user", "content": "Read a.py and b.py independently, then report both markers."
        }]
        scenario["tools"] = [_tool("read_file", ["path"])]
        scenario["injected_results"] = [
            {"tool": "read_file", "result": {"path": "a.py", "marker": "A-OK"}},
            {"tool": "read_file", "result": {"path": "b.py", "marker": "B-OK"}},
        ]
        expected.update({
            "tool_mode": "parallel",
            "tool_calls": [
                {"name": "read_file", "arguments": {"path": "a.py"}},
                {"name": "read_file", "arguments": {"path": "b.py"}},
            ],
            "result_markers": ["A-OK", "B-OK"],
            "final": "A-OK | B-OK",
        })
    elif scenario_type == "dependent-result":
        scenario["messages"] = [{
            "role": "user", "content": "Look up user Ada, then update that exact user to active."
        }]
        scenario["tools"] = [_tool("lookup_user", ["name"]), _tool("update_user", ["id", "state"])]
        scenario["injected_results"] = [
            {"tool": "lookup_user", "result": {"id": "user-73"}},
            {"tool": "update_user", "result": {"status": "UPDATED-73"}},
        ]
        expected.update({
            "tool_mode": "sequential",
            "tool_calls": [
                {"name": "lookup_user", "arguments": {"name": "Ada"}},
                {"name": "update_user", "arguments": {"id": "user-73", "state": "active"}},
            ],
            "result_markers": ["UPDATED-73"],
            "final": "UPDATED-73",
        })
    elif scenario_type == "tool-recovery":
        scenario["scenario_id"] = f"tool-recovery-{recovery_result}"
        expected["scenario_id"] = scenario["scenario_id"]
        scenario["messages"] = [{
            "role": "user", "content": "Fetch build B-9. If the result is unusable, retry once safely."
        }]
        scenario["tools"] = [_tool("fetch_build", ["id", "attempt"])]
        fixtures = {
            "malformed": "{not-json",
            "partial": {"status": "partial"},
            "error": {"error": "temporary"},
            "timeout": {"error": "timeout"},
            "contradictory": {"status": "passed", "tests_failed": 2},
        }
        scenario["injected_results"] = [
            {"tool": "fetch_build", "result": fixtures[recovery_result]},
            {"tool": "fetch_build", "result": {"status": "passed", "marker": "BUILD-OK"}},
        ]
        expected.update({
            "tool_mode": "sequential",
            "tool_calls": [
                {"name": "fetch_build", "arguments": {"id": "B-9", "attempt": "1"}},
                {"name": "fetch_build", "arguments": {"id": "B-9", "attempt": "2"}},
            ],
            "result_markers": ["BUILD-OK"],
            "final": "BUILD-OK",
            "recovery_required": True,
            "recovery_result": recovery_result,
        })
    elif scenario_type == "debug-loop":
        scenario["messages"] = [{
            "role": "user", "content": "Run tests, inspect the failing file, apply the one-line fix, and rerun."
        }]
        scenario["tools"] = [
            _tool("run_tests", ["scope"]),
            _tool("read_file", ["path"]),
            _tool("apply_edit", ["path", "edit"]),
        ]
        expected.update({
            "tool_mode": "sequential",
            "tool_calls": [
                {"name": "run_tests", "arguments": {"scope": "unit"}},
                {"name": "read_file", "arguments": {"path": "calc.py"}},
                {"name": "apply_edit", "arguments": {"path": "calc.py", "edit": "return a + b"}},
                {"name": "run_tests", "arguments": {"scope": "unit"}},
            ],
            "result_markers": ["TESTS-PASS"],
            "final": "TESTS-PASS",
        })
    else:
        code = "SESSION-8841"
        history = []
        for index in range(session_turns - 1):
            history.extend([
                {"role": "user", "content": f"Turn {index}: remember neutral note {index}."},
                {"role": "assistant", "content": f"noted {index}"},
            ])
        history.insert(0, {"role": "user", "content": f"Remember private session code {code}."})
        history.append({"role": "user", "content": "Return only the private session code."})
        scenario["messages"] = history
        expected.update({
            "final": code,
            "history": copy.deepcopy(history),
            "require_token_growth": True,
        })
    return scenario, expected


def build_recovery_matrix() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [
        build_agentic_scenario("tool-recovery", recovery_result=result_type)
        for result_type in RECOVERY_RESULT_TYPES
    ]


def _normalize(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _tool_call_matches(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return observed.get("name") == expected["name"] and observed.get("arguments") == expected[
        "arguments"
    ]


def score_agentic_trace(
    scenario: Mapping[str, Any],
    expected: Mapping[str, Any],
    trace: Mapping[str, Any],
) -> dict[str, Any]:
    """Score protocol, reasoning, recovery, and answer stages without model calls."""
    if scenario.get("schema") != AGENTIC_SCENARIO_SCHEMA or scenario.get(
        "scenario_id"
    ) != expected.get("scenario_id"):
        raise BenchmarkJobError("scenario_identity_mismatch", "scenario identity does not match")
    tool_calls = trace.get("tool_calls", [])
    if not isinstance(tool_calls, list) or not all(isinstance(item, Mapping) for item in tool_calls):
        raise BenchmarkJobError("bad_agentic_trace", "tool_calls must be a list of objects")
    expected_calls = expected.get("tool_calls", [])
    if expected.get("tool_mode") == "parallel":
        protocol_passed = len(tool_calls) == len(expected_calls) and all(
            any(_tool_call_matches(item, wanted) for item in tool_calls) for wanted in expected_calls
        )
    else:
        protocol_passed = len(tool_calls) == len(expected_calls) and all(
            _tool_call_matches(item, wanted)
            for item, wanted in zip(tool_calls, expected_calls, strict=True)
        )
    final = trace.get("final_answer")
    final_passed = False
    if expected.get("final_kind") == "exact_json":
        try:
            parsed = json.loads(final) if isinstance(final, str) else final
        except json.JSONDecodeError:
            parsed = None
        final_passed = parsed == expected.get("final")
    elif isinstance(final, str) and isinstance(expected.get("final"), str):
        final_passed = _normalize(final) == _normalize(expected["final"])
    markers = expected.get("result_markers", [])
    result_passed = isinstance(final, str) and all(marker in final for marker in markers)
    if not markers:
        result_passed = True
    recovery_passed = not expected.get("recovery_required") or (
        protocol_passed and len(tool_calls) >= 2 and result_passed
    )
    reasoning_passed = final_passed
    history = trace.get("history", [])
    growth = trace.get("history_prompt_tokens", [])
    history_passed = True
    if expected.get("history") is not None:
        history_passed = history == expected["history"]
        history_passed = history_passed and isinstance(growth, list) and len(growth) > 1
        history_passed = history_passed and all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in growth
        )
        history_passed = history_passed and growth == sorted(growth) and growth[-1] > growth[0]
    failure = trace.get("failure")
    failure_code = failure.get("code") if isinstance(failure, Mapping) else None
    if failure_code == "reasoning_budget_exhausted":
        classification = "reasoning_budget_exhausted"
    elif failure_code in {"infrastructure_error", "endpoint_error", "timeout"}:
        classification = "infrastructure_failure"
    elif failure_code in {"parser_error", "malformed_tool_call"}:
        classification = "parser_failure"
    elif not protocol_passed:
        classification = "protocol_failure"
    elif not recovery_passed:
        classification = "recovery_failure"
    elif not reasoning_passed:
        classification = "reasoning_failure"
    elif not final_passed or not result_passed or not history_passed:
        classification = "final_answer_failure"
    else:
        classification = None
    stages = {
        "protocol": {"passed": protocol_passed},
        "reasoning": {"passed": reasoning_passed},
        "result_incorporation": {"passed": result_passed},
        "recovery": {"passed": recovery_passed},
        "history": {"passed": history_passed},
        "final_answer": {"passed": final_passed},
    }
    return {
        "schema": AGENTIC_OBSERVATION_SCHEMA,
        "scenario_id": scenario["scenario_id"],
        "passed": classification is None and all(item["passed"] for item in stages.values()),
        "failure_class": classification,
        "stages": stages,
        "tool_calls": [dict(item) for item in tool_calls],
        "full_history": copy.deepcopy(history),
        "history_prompt_tokens": list(growth) if isinstance(growth, list) else [],
        "failure": dict(failure) if isinstance(failure, Mapping) else None,
    }
