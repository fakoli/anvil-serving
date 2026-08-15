from __future__ import annotations

import copy
import math
import re

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking.suite_runner import run_agentic_suite, run_context_suite


def spec(suite, **parameters):
    return {
        "schema": JOB_SPEC_SCHEMA,
        "run_id": f"{suite}-run",
        "ownership_id": "campaign",
        "suite": suite,
        "profile": "smoke",
        "endpoint": {"base_url": "http://127.0.0.1:8000/v1", "model": "deepseek"},
        "worker": {"id": "worker"},
        "submitted_at": "2026-08-03T12:00:00Z",
        "timeout_s": 600,
        "parameters": parameters,
    }


def context_caller(base, model, key, messages, max_tokens, timeout, **_kwargs):
    prompt = messages[-1]["content"]
    if prompt.startswith("token calibration"):
        answer = "ok"
    elif "ALPHA, BETA, and GAMMA" in prompt:
        values = re.findall(r"Checkpoint (?:ALPHA|BETA|GAMMA) stores (K\d+)\.", prompt)
        answer = " | ".join(values)
    else:
        match = re.search(r"access marker for ORCHID is (K\d+)\.", prompt)
        answer = match.group(1)
    return {
        "latency_s": 0.25,
        "request_id": f"request-{len(prompt)}",
        "response": {
            "choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": max(2, math.ceil(len(prompt) / 4)),
                "completion_tokens": 2,
            },
        },
    }


def test_context_runner_calibrates_with_endpoint_usage_and_scores_exactly():
    result = run_context_suite(
        load_profile("smoke"),
        spec("context", case_limit=1, advertised_context=650000),
        caller=context_caller,
    )
    assert result["calibration"]["method"] == "endpoint-usage-ratio/v1"
    assert result["observations"][0]["passed"] is True
    assert result["curve"]["effective_context"] == 8192
    assert result["request_ids"]


def test_context_runner_rejects_unimplemented_profile_cases():
    profile = copy.deepcopy(load_profile("smoke"))
    profile["suites"]["context"]["cases"] = ["ruler-niah"]

    with pytest.raises(BenchmarkJobError) as exc:
        run_context_suite(profile, spec("context"), caller=context_caller)

    assert exc.value.code == "unsupported_context_case"


class AgentCaller:
    def __init__(self):
        self.kwargs = []

    def __call__(self, base, model, key, messages, max_tokens, timeout, tools=None, **_kwargs):
        self.kwargs.append(_kwargs)
        tool_messages = [item for item in messages if item["role"] == "tool"]
        if not tool_messages:
            message = {
                "content": None,
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "fetch_build", "arguments": '{"id":"B-9","attempt":"1"}'},
                }],
            }
        elif len(tool_messages) == 1:
            message = {
                "content": None,
                "tool_calls": [{
                    "id": "call-2",
                    "type": "function",
                    "function": {"name": "fetch_build", "arguments": '{"id":"B-9","attempt":"2"}'},
                }],
            }
        else:
            message = {"content": "BUILD-OK"}
        return {
            "latency_s": 0.1,
            "request_id": f"agent-{len(tool_messages)}",
            "response": {
                "choices": [{"message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": 100 + len(messages), "completion_tokens": 10},
            },
        }


def test_agentic_runner_injects_failed_result_then_scores_retry():
    result = run_agentic_suite(
        load_profile("smoke"),
        spec("agentic", recovery_result="error", case_ids=["tool-recovery"]),
        caller=AgentCaller(),
    )
    assert result["passed"] is True
    observation = result["observations"][0]
    assert observation["stages"]["recovery"]["passed"] is True
    assert [call["arguments"]["attempt"] for call in observation["tool_calls"]] == ["1", "2"]
    assert len(result["request_ids"]) == 3
    assert observation["visible_answer"] == "BUILD-OK"
    assert len(observation["turns"]) == 3


def test_agentic_runner_forwards_one_explicit_reasoning_control():
    caller = AgentCaller()
    result = run_agentic_suite(
        load_profile("smoke"),
        spec(
            "agentic",
            recovery_result="error",
            case_ids=["tool-recovery"],
            reasoning_effort="xhigh",
        ),
        caller=caller,
    )

    assert result["request_controls"]["reasoning_effort"] == "xhigh"
    assert all(item["reasoning_effort"] == "xhigh" for item in caller.kwargs)
    assert all(item["chat_template_kwargs"] is None for item in caller.kwargs)


def test_agentic_runner_rejects_conflicting_reasoning_controls():
    with pytest.raises(BenchmarkJobError) as exc:
        run_agentic_suite(
            load_profile("smoke"),
            spec(
                "agentic",
                case_ids=["tool-recovery"],
                reasoning_effort="xhigh",
                thinking_mode="enabled",
            ),
            caller=AgentCaller(),
        )

    assert exc.value.code == "conflicting_reasoning_controls"


def test_agentic_runner_executes_every_profile_repetition():
    profile = copy.deepcopy(load_profile("smoke"))
    profile["suites"]["agentic"]["repetitions"] = 2

    result = run_agentic_suite(
        profile,
        spec("agentic", recovery_result="error", case_ids=["tool-recovery"]),
        caller=AgentCaller(),
    )

    assert [item["repetition"] for item in result["observations"]] == [0, 1]
    assert result["summary"]["attempted"] == 2


@pytest.mark.parametrize("case_ids", [[], ["tool-recovery", "tool-recovery"], ["unknown"]])
def test_agentic_runner_rejects_invalid_case_selection(case_ids):
    with pytest.raises(BenchmarkJobError):
        run_agentic_suite(
            load_profile("smoke"),
            spec("agentic", case_ids=case_ids),
            caller=AgentCaller(),
        )
