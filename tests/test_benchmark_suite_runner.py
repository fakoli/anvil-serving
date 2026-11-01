from __future__ import annotations

import copy
import json
import math
import re

import pytest

from anvil_serving.benchmarking.jobs import BenchmarkJobError, JOB_SPEC_SCHEMA
from anvil_serving.benchmarking.profiles import load_profile
from anvil_serving.benchmarking.submission import validate_submission_spec
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


def test_debug_loop_v2_submission_requires_legacy_case_authorization():
    with pytest.raises(BenchmarkJobError) as error:
        validate_submission_spec(spec("agentic", case_ids=["debug-loop-v2"]))

    assert error.value.code == "unknown_agentic_case_id"


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


def budget_profile(suite, *, calls, tokens=16, deadline=600, repetitions=1):
    profile = copy.deepcopy(load_profile("deep"))
    profile["schema"] = "anvil-serving.benchmark-profile/v2"
    settings = profile["suites"][suite]
    if suite == "context":
        settings.update(token_buckets=[512, 1024], positions=[0.5, 0.97])
    settings["repetitions"] = repetitions
    settings["timeout_s"] = deadline
    settings["output_headroom_tokens" if suite == "context" else "max_completion_tokens"] = tokens
    settings["budgets"] = {
        "max_model_calls": calls, "max_completion_tokens": tokens,
        "deadline_s": deadline, "max_retries": 0,
        "min_free_disk_bytes": settings["requirements"]["min_free_disk_bytes"],
    }
    return profile


def test_context_budget_deadline_during_calibration_retains_evidence(monkeypatch):
    from anvil_serving.benchmarking import budgets

    now = [0]
    monkeypatch.setattr(budgets.time, "monotonic", lambda: now[0])

    def caller(*args, **kwargs):
        now[0] = 11
        return context_caller(*args, **kwargs)

    result = run_context_suite(
        budget_profile("context", calls=5, deadline=10),
        spec("context", case_ids=["native-needle"], token_buckets=[512], positions=[0.5]),
        caller=caller,
    )
    assert result["calibration"] is not None
    assert result["observations"] == []
    assert result["curve"] is None
    assert result["failure"]["budget"] == "deadline_s"
    assert result["budget"]["model_calls"] == 1


def test_agentic_deadline_before_first_send_has_no_attempted_case(monkeypatch):
    from anvil_serving.benchmarking import budgets, suite_runner

    now = [0]
    monkeypatch.setattr(budgets.time, "monotonic", lambda: now[0])
    original = suite_runner.build_agentic_scenario

    def scenario(*args, **kwargs):
        now[0] += 10
        return original(*args, **kwargs)

    monkeypatch.setattr(suite_runner, "build_agentic_scenario", scenario)
    result = run_agentic_suite(
        budget_profile("agentic", calls=3, deadline=10),
        spec("agentic", case_ids=["tool-recovery"]),
        caller=lambda *_, **__: pytest.fail("sent after deadline"),
    )
    assert result["state"] == "incomplete"
    assert result["budget"]["model_calls"] == 0
    assert result["observations"] == []
    assert result["summary"]["attempted"] == 0
    assert result["summary"]["pass_rate"] is None


def test_long_session_budget_counts_every_real_turn():
    calls = []

    def caller(_base, _model, _key, messages, **kwargs):
        calls.append((copy.deepcopy(messages), kwargs))
        return {"latency_s": 0.1, "response": {
            "choices": [{"message": {"content": "ACK"}}],
            "usage": {"prompt_tokens": len(calls) * 100, "completion_tokens": 2},
        }}

    result = run_agentic_suite(
        budget_profile("agentic", calls=2),
        spec("agentic", case_ids=["long-session"], session_turns=4), caller=caller,
    )
    assert len(calls) == 2
    assert result["budget"]["model_calls"] == 2
    assert result["observations"][0]["history_prompt_tokens"] == [100, 200]
    assert len(result["observations"][0]["turns"]) == 2
    assert result["state"] == "incomplete"


def test_agentic_late_last_reply_is_retained_but_cannot_complete(monkeypatch):
    from anvil_serving.benchmarking import budgets

    now = [0]
    monkeypatch.setattr(budgets.time, "monotonic", lambda: now[0])
    agent = AgentCaller()
    timeouts = []

    def caller(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        now[0] += 4
        return agent(*args, **kwargs)

    result = run_agentic_suite(
        budget_profile("agentic", calls=5, deadline=10),
        spec("agentic", case_ids=["tool-recovery"]), caller=caller,
    )
    assert timeouts == [10, 6, 2]
    assert result["observations"][0]["visible_answer"] == "BUILD-OK"
    assert result["observations"][0]["passed"] is True  # retained content correctness
    assert result["passed"] is False  # suite deadline was violated
    assert result["failure"]["budget"] == "deadline_s"
    assert result["state"] == "incomplete"
