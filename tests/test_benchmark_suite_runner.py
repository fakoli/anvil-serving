from __future__ import annotations

import copy
import json
import math
import re

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking.suite_runner import run_agentic_suite, run_context_suite


@pytest.mark.parametrize("extra", [None, "read", "wrong-edit"])
def test_debug_fixture_rejects_extra_calls_without_advancing_state(extra):
    calls = [
        ("run_tests", {"scope": "unit"}),
        ("read_file", {"path": "calc.py"}),
        ("apply_edit", {"path": "calc.py", "edit": "return a + b"}),
        ("run_tests", {"scope": "unit"}),
    ]
    if extra:
        calls.insert(2, ("read_file", {"path": "calc.py"}) if extra == "read" else
                     ("apply_edit", {"path": "calc.py", "edit": "return a - b"}))
    replies = []

    def caller(base, model, key, messages, **_kwargs):
        index = len(replies)
        replies.append(copy.deepcopy(messages))
        message = {"content": "tests pass"}
        if index < len(calls):
            name, arguments = calls[index]
            message = {"content": "", "tool_calls": [{
                "id": f"call-{index}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }]}
        return {"latency_s": 0.01, "response": {
            "choices": [{"message": message, "finish_reason": "stop"}],
        }}

    profile = copy.deepcopy(load_profile("deep"))
    profile["suites"]["agentic"]["repetitions"] = 1
    result = run_agentic_suite(
        profile, spec("agentic", case_ids=["debug-loop"]),
        caller=caller,
    )
    tool_results = [json.loads(item["content"]) for item in replies[-1] if item["role"] == "tool"]
    expected_results = [
        {"status": "failed", "file": "calc.py"},
        {"path": "calc.py", "content": "return a - b"},
        {"status": "edited"},
        {"status": "passed", "marker": "TESTS-PASS"},
    ]
    if extra:
        expected_results.insert(2, {"error": "unexpected deterministic tool call"})
    assert tool_results == expected_results
    assert result["observations"][0]["passed"] is (extra is None)


def test_parallel_fixture_matches_arguments_and_rejects_duplicate_consumption():
    from anvil_serving.benchmarking.agentic import build_agentic_scenario
    from anvil_serving.benchmarking.suite_runner import _fixture_result

    scenario, expected = build_agentic_scenario("parallel-tools")
    consumed = set()
    b = {"name": "read_file", "arguments": {"path": "b.py"}}
    a = {"name": "read_file", "arguments": {"path": "a.py"}}
    assert _fixture_result(scenario, expected, consumed, b) == {"path": "b.py", "marker": "B-OK"}
    assert _fixture_result(scenario, expected, consumed, b) == {"error": "unexpected deterministic tool call"}
    assert _fixture_result(scenario, expected, consumed, a) == {"path": "a.py", "marker": "A-OK"}


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
    assert result["calibration"]["method"] == "endpoint-usage-filler-ratio/v2"
    assert result["observations"][0]["passed"] is True
    assert result["curve"]["effective_context"] == 8192
    assert result["request_ids"]


def test_context_runner_honors_recorded_case_bucket_position_and_headroom_selection():
    result = run_context_suite(
        load_profile("smoke"),
        spec(
            "context",
            case_ids=["native-needle"],
            token_buckets=[512],
            positions=[0.97],
            repetitions=1,
            output_headroom_tokens=8192,
            advertised_context=262144,
        ),
        caller=context_caller,
    )

    assert result["selection"] == {
        "case_ids": ["native-needle"],
        "token_buckets": [512],
        "positions": [0.97],
        "repetitions": 1,
        "output_headroom_tokens": 8192,
    }
    assert result["observations"][0]["requested_tokens"] == 512
    assert result["observations"][0]["position"] == 0.97


def test_context_runner_forwards_controls_and_retains_bounded_reasoning_capture():
    calls = []

    def caller(base, model, key, messages, max_tokens, timeout, **kwargs):
        calls.append(kwargs)
        prompt = messages[-1]["content"]
        calibration = prompt.startswith("token calibration")
        answer = "ok" if calibration else re.search(
            r"access marker for ORCHID is (K\d+)\.", prompt
        ).group(1)
        if not calibration:
            answer += " " * 9000
        return {
            "latency_s": 0.1,
            "request_id": "context-control",
            "response": {
                "choices": [{"message": {
                    "content": answer,
                    "reasoning": "",
                    "reasoning_content": "r" * 9000,
                }, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": max(2, math.ceil(len(prompt) / 4)), "completion_tokens": 2},
            },
        }

    result = run_context_suite(
        load_profile("smoke"),
        spec(
            "context",
            case_ids=["native-needle"],
            token_buckets=[512],
            positions=[0.5],
            thinking_mode="enabled",
            reasoning_effort="max",
            clear_thinking=False,
            temperature=1.0,
            top_p=0.95,
        ),
        caller=caller,
    )

    assert all(call == {
        "chat_template_kwargs": {"enable_thinking": True, "clear_thinking": False},
        "reasoning_effort": "max", "temperature": 1.0, "top_p": 0.95,
    } for call in calls)
    assert result["request_controls"] == {
        "thinking_mode": "enabled", "reasoning_effort": "max",
        "chat_template_kwargs": {"enable_thinking": True, "clear_thinking": False},
        "sampling": {
            "temperature": {"requested": 1.0, "effective_request": 1.0, "sent": True},
            "top_p": {"requested": 0.95, "effective_request": 0.95, "sent": True},
        },
    }
    observation = result["observations"][0]
    assert observation["passed"] is True
    assert len(observation["visible_answer"]) == 8192
    assert observation["visible_answer_truncated"] is True
    assert "raw_visible_answer" not in observation
    assert observation["raw_reasoning_field"] == "reasoning_content"
    assert observation["raw_reasoning"] == "r" * 8192
    assert observation["raw_reasoning_truncated"] is True
    assert observation["finish_reason"] == "stop"


def test_context_runner_preserves_legacy_optional_sampler_kwargs():
    calls = []

    def caller(base, model, key, messages, max_tokens, timeout, **kwargs):
        calls.append(kwargs)
        prompt = messages[-1]["content"]
        answer = "ok" if prompt.startswith("token calibration") else re.search(
            r"access marker for ORCHID is (K\d+)\.", prompt
        ).group(1)
        return {
            "latency_s": 0.1,
            "response": {
                "choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": max(2, math.ceil(len(prompt) / 4)), "completion_tokens": 2},
            },
        }

    result = run_context_suite(
        load_profile("smoke"),
        spec("context", case_ids=["native-needle"], token_buckets=[512], positions=[0.5]),
        caller=caller,
    )

    assert calls == [{}, {}]
    assert result["request_controls"]["sampling"] == {
        "temperature": {"requested": None, "effective_request": 0.0, "sent": True},
        "top_p": {"requested": None, "effective_request": None, "sent": False},
    }


@pytest.mark.parametrize(
    ("suite", "parameters", "caller"),
    (
        ("context", {"temperature": -0.1}, context_caller),
        ("agentic", {"top_p": 0.0}, context_caller),
    ),
)
def test_suite_runners_reject_invalid_optional_sampler_controls(suite, parameters, caller):
    with pytest.raises(BenchmarkJobError) as exc:
        run = run_context_suite if suite == "context" else run_agentic_suite
        run(load_profile("smoke"), spec(suite, **parameters), caller=caller)

    assert exc.value.code == "bad_sampling"


def test_context_runner_marks_reasoning_only_reply_as_empty_visible_failure():
    def caller(base, model, key, messages, max_tokens, timeout, **_kwargs):
        prompt = messages[-1]["content"]
        return {
            "latency_s": 0.1,
            "response": {
                "choices": [{"message": {"content": "ok" if prompt.startswith("token calibration") else "", "reasoning": "hidden"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": max(2, math.ceil(len(prompt) / 4)), "completion_tokens": 2},
            },
        }

    result = run_context_suite(
        load_profile("smoke"),
        spec("context", case_ids=["native-needle"], token_buckets=[512], positions=[0.5]),
        caller=caller,
    )

    assert result["observations"][0]["failure"]["code"] == "empty_visible_answer"


def test_context_runner_rejects_selection_beyond_advertised_capacity():
    with pytest.raises(BenchmarkJobError) as exc:
        run_context_suite(
            load_profile("smoke"),
            spec(
                "context",
                token_buckets=[250000],
                output_headroom_tokens=16384,
                advertised_context=262144,
            ),
            caller=context_caller,
        )

    assert exc.value.code == "context_capacity_exceeded"


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
            temperature=1.0,
            top_p=0.95,
        ),
        caller=caller,
    )

    assert result["request_controls"]["reasoning_effort"] == "xhigh"
    assert result["request_controls"]["sampling"] == {
        "temperature": {"requested": 1.0, "effective_request": 1.0, "sent": True},
        "top_p": {"requested": 0.95, "effective_request": 0.95, "sent": True},
    }
    assert all(item["reasoning_effort"] == "xhigh" for item in caller.kwargs)
    assert all(item["chat_template_kwargs"] is None for item in caller.kwargs)
    assert all(item["temperature"] == 1.0 and item["top_p"] == 0.95 for item in caller.kwargs)


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


def test_agentic_runner_executes_real_long_session_turns_with_token_growth():
    class LongSessionCaller:
        def __init__(self):
            self.prompts = []

        def __call__(self, base, model, key, messages, max_tokens, timeout, **_kwargs):
            self.prompts.append(copy.deepcopy(messages))
            final = messages[-1]["content"] == "Return only the session marker."
            content = "SESSION-8841" if final else f"noted {len(self.prompts)}"
            return {
                "latency_s": 0.1,
                "request_id": f"session-{len(self.prompts)}",
                "response": {
                    "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 100 * len(self.prompts),
                        "completion_tokens": 2,
                    },
                },
            }

    profile = copy.deepcopy(load_profile("deep"))
    profile["suites"]["agentic"]["repetitions"] = 1
    caller = LongSessionCaller()
    result = run_agentic_suite(
        profile,
        spec("agentic", case_ids=["long-session"], session_turns=4),
        caller=caller,
    )

    assert result["passed"] is True
    assert len(caller.prompts) == 5
    assert result["observations"][0]["history_prompt_tokens"] == [100, 200, 300, 400, 500]
    assert len(result["request_ids"]) == 5


def test_long_session_preserves_declared_budget_telemetry_and_reasoning_replay():
    class LongSessionFailureCaller:
        def __init__(self):
            self.calls = []

        def __call__(self, base, model, key, messages, max_tokens, timeout, **_kwargs):
            self.calls.append((copy.deepcopy(messages), max_tokens))
            first = len(self.calls) == 1
            message = (
                {"content": "ACK", "reasoning_content": "first-turn reasoning"}
                if first
                else {"content": "", "reasoning_content": "second-turn reasoning"}
            )
            return {
                "latency_s": 0.1,
                "response": {
                    "choices": [{"message": message, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 28 * len(self.calls), "completion_tokens": 64},
                },
            }

    profile = copy.deepcopy(load_profile("deep"))
    profile["suites"]["agentic"]["repetitions"] = 1
    caller = LongSessionFailureCaller()
    result = run_agentic_suite(
        profile,
        spec("agentic", case_ids=["long-session"], session_turns=2),
        caller=caller,
    )

    observation = result["observations"][0]
    assert [max_tokens for _messages, max_tokens in caller.calls] == [16384, 16384]
    replayed_assistant = caller.calls[1][0][-2]
    assert replayed_assistant == {
        "role": "assistant", "content": "ACK", "reasoning_content": "first-turn reasoning"
    }
    assert observation["failure"]["code"] == "parser_error"
    assert observation["turns"][-1] == {
        "latency_ms": 100.0,
        "prompt_tokens": 56,
        "completion_tokens": 64,
        "finish_reason": "length",
        "reasoning_chars": len("second-turn reasoning"),
        "tool_call_count": 0,
    }


@pytest.mark.parametrize("case_ids", [[], ["tool-recovery", "tool-recovery"], ["unknown"]])
def test_agentic_runner_rejects_invalid_case_selection(case_ids):
    with pytest.raises(BenchmarkJobError):
        run_agentic_suite(
            load_profile("smoke"),
            spec("agentic", case_ids=case_ids),
            caller=AgentCaller(),
        )
