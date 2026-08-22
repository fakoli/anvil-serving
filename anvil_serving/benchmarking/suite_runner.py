"""Concrete context and agentic benchmark execution through a routed endpoint."""

from __future__ import annotations

import copy
import json
import math
from typing import Any, Callable, Mapping

from .agentic import build_agentic_scenario, score_agentic_trace
from .context import (
    NATIVE_CONTEXT_CASES,
    build_native_context_case,
    score_context_response,
    summarize_context_degradation,
)
from .jobs import BenchmarkJobError
from .requests import post_chat, resolve_api_key, response_observation
from ..model_controls import REASONING_EFFORT_CHOICES


ChatCaller = Callable[..., dict[str, Any]]


def _message(response: Mapping[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise BenchmarkJobError("malformed_model_response", "response has no first choice")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise BenchmarkJobError("malformed_model_response", "response has no assistant message")
    return copy.deepcopy(dict(message))


def _usage_tokens(response: Mapping[str, Any], name: str) -> int | None:
    usage = response.get("usage")
    value = usage.get(name) if isinstance(usage, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _calibrated_counter(
    *, endpoint: Mapping[str, str], key: str | None, caller: ChatCaller, timeout: float
) -> tuple[Callable[[str], int], dict[str, Any]]:
    sample = "token calibration phrase 0123456789.\n" + "\n".join(
        f"Reference paragraph {index:07d}: neutral cobalt ledger material for calibration."
        for index in range(512)
    )
    result = caller(
        endpoint["base_url"],
        endpoint["model"],
        key,
        [{"role": "user", "content": sample}],
        max_tokens=1,
        timeout=timeout,
    )
    prompt_tokens = _usage_tokens(result["response"], "prompt_tokens")
    if prompt_tokens is None or prompt_tokens < 2:
        raise BenchmarkJobError(
            "tokenizer_calibration_unavailable",
            "endpoint usage is required to calibrate context cases",
        )
    chars_per_token = len(sample) / prompt_tokens
    if not 0.25 <= chars_per_token <= 20:
        raise BenchmarkJobError("bad_tokenizer_calibration", "endpoint token ratio is implausible")

    def counter(text: str) -> int:
        return max(1, math.ceil(len(text) / chars_per_token))

    return counter, {
        "method": "endpoint-usage-filler-ratio/v2",
        "sample_chars": len(sample),
        "sample_prompt_tokens": prompt_tokens,
        "chars_per_token": chars_per_token,
        "request_id": result.get("request_id"),
    }


def _context_selection(suite: Mapping[str, Any], parameters: Mapping[str, Any]) -> dict[str, Any]:
    cases = parameters.get("case_ids", suite["cases"])
    if (
        not isinstance(cases, list)
        or not cases
        or any(not isinstance(item, str) or not item for item in cases)
        or len(set(cases)) != len(cases)
    ):
        raise BenchmarkJobError(
            "bad_context_case_ids",
            "context case_ids must be a non-empty list of unique strings",
        )
    unsupported = sorted(set(cases) - NATIVE_CONTEXT_CASES)
    if unsupported:
        raise BenchmarkJobError(
            "unsupported_context_case",
            "context case_ids include cases without an executable native adapter",
            {"cases": unsupported},
        )

    buckets = parameters.get("token_buckets", suite["token_buckets"])
    if (
        not isinstance(buckets, list)
        or not buckets
        or any(
            not isinstance(item, int)
            or isinstance(item, bool)
            or not 128 <= item <= 1048576
            for item in buckets
        )
        or buckets != sorted(set(buckets))
    ):
        raise BenchmarkJobError(
            "bad_context_buckets",
            "context token_buckets must be sorted unique integers from 128 through 1048576",
        )

    positions = parameters.get("positions", suite["positions"])
    if (
        not isinstance(positions, list)
        or not positions
        or any(
            not isinstance(item, (int, float))
            or isinstance(item, bool)
            or not 0 <= item <= 1
            for item in positions
        )
        or len(set(positions)) != len(positions)
    ):
        raise BenchmarkJobError(
            "bad_context_positions",
            "context positions must be a non-empty list of unique values from 0 through 1",
        )

    repetitions = parameters.get("repetitions", suite["repetitions"])
    if (
        not isinstance(repetitions, int)
        or isinstance(repetitions, bool)
        or not 1 <= repetitions <= 20
    ):
        raise BenchmarkJobError(
            "bad_context_repetitions", "context repetitions must be from 1 through 20"
        )
    headroom = parameters.get("output_headroom_tokens", suite["output_headroom_tokens"])
    if (
        not isinstance(headroom, int)
        or isinstance(headroom, bool)
        or not 1 <= headroom <= 65536
    ):
        raise BenchmarkJobError(
            "bad_context_headroom", "context output_headroom_tokens must be from 1 through 65536"
        )
    advertised = parameters.get("advertised_context")
    if advertised is not None:
        if (
            not isinstance(advertised, int)
            or isinstance(advertised, bool)
            or not 128 <= advertised <= 1048576
        ):
            raise BenchmarkJobError(
                "bad_advertised_context", "advertised_context must be from 128 through 1048576"
            )
        if buckets[-1] + headroom > advertised:
            raise BenchmarkJobError(
                "context_capacity_exceeded",
                "selected context bucket exceeds advertised capacity after output headroom",
                {
                    "advertised_context": advertised,
                    "requested_tokens": buckets[-1],
                    "output_headroom_tokens": headroom,
                },
            )
    return {
        "case_ids": list(cases),
        "token_buckets": list(buckets),
        "positions": [float(item) for item in positions],
        "repetitions": repetitions,
        "output_headroom_tokens": headroom,
    }


def run_context_suite(
    profile: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    caller: ChatCaller = post_chat,
) -> dict[str, Any]:
    """Execute deterministic context cases and return raw observations plus a curve."""
    suite = profile["suites"]["context"]
    endpoint = spec["endpoint"]
    key = resolve_api_key(endpoint.get("auth_env"))
    timeout = min(float(spec["timeout_s"]), 900.0)
    counter, calibration = _calibrated_counter(
        endpoint=endpoint, key=key, caller=caller, timeout=timeout
    )
    parameters = spec.get("parameters", {})
    selection = _context_selection(suite, parameters)
    case_limit = parameters.get("case_limit")
    if case_limit is not None and (
        not isinstance(case_limit, int) or isinstance(case_limit, bool) or case_limit < 1
    ):
        raise BenchmarkJobError("bad_case_limit", "context case_limit must be positive")
    observations = []
    request_ids = []
    attempted = 0
    for bucket in selection["token_buckets"]:
        for case_type in selection["case_ids"]:
            for position in selection["positions"]:
                for repetition in range(selection["repetitions"]):
                    if case_limit is not None and attempted >= case_limit:
                        break
                    case, expected = build_native_context_case(
                        case_type,
                        requested_tokens=bucket,
                        position=position,
                        repetition=repetition,
                        token_counter=counter,
                        seed=17,
                    )
                    attempted += 1
                    try:
                        result = caller(
                            endpoint["base_url"],
                            endpoint["model"],
                            key,
                            [{"role": "user", "content": case["prompt"]}],
                            max_tokens=selection["output_headroom_tokens"],
                            timeout=timeout,
                        )
                        response = result["response"]
                        observation = response_observation(response)
                        prompt_tokens = _usage_tokens(response, "prompt_tokens")
                        completion_tokens = _usage_tokens(response, "completion_tokens")
                        throughput = (
                            completion_tokens / result["latency_s"]
                            if completion_tokens is not None and result["latency_s"] > 0
                            else None
                        )
                        scored = score_context_response(
                            case,
                            expected,
                            observation["content"],
                            observed_prompt_tokens=prompt_tokens,
                            latency_ms=result["latency_s"] * 1000,
                            throughput_tps=throughput,
                            finish_reason=observation["finish_reason"],
                        )
                        if result.get("request_id"):
                            request_ids.append(result["request_id"])
                    except Exception as exc:  # retained per sample; lower evidence survives
                        scored = score_context_response(
                            case,
                            expected,
                            "",
                            failure={"code": type(exc).__name__, "message": str(exc)[:1024]},
                        )
                    scored["answer_sha256"] = expected["answer_sha256"]
                    observations.append(scored)
                if case_limit is not None and attempted >= case_limit:
                    break
            if case_limit is not None and attempted >= case_limit:
                break
        if case_limit is not None and attempted >= case_limit:
            break
    if not observations:
        raise BenchmarkJobError("no_context_cases", "profile selected no executable context cases")
    attempted_buckets = {item["requested_tokens"] for item in observations}
    scoring = dict(suite["scoring"])
    if scoring["baseline_bucket"] not in attempted_buckets:
        scoring["baseline_bucket"] = min(attempted_buckets)
    curve = summarize_context_degradation(
        observations,
        scoring=scoring,
        advertised_context=parameters.get("advertised_context"),
    )
    return {
        "schema": "anvil-serving.context-suite-run/v1",
        "calibration": calibration,
        "selection": selection,
        "request_ids": sorted(set(request_ids)),
        "observations": observations,
        "curve": curve,
        "passed": all(item["passed"] for item in observations),
    }


def _normalized_tool_calls(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = message.get("tool_calls")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BenchmarkJobError("malformed_tool_call", "tool_calls must be a list")
    result = []
    for index, item in enumerate(raw):
        function = item.get("function") if isinstance(item, Mapping) else None
        if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
            raise BenchmarkJobError("malformed_tool_call", "tool call function is invalid")
        arguments = function.get("arguments")
        try:
            arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError as exc:
            raise BenchmarkJobError("malformed_tool_call", "tool call arguments are invalid JSON") from exc
        if not isinstance(arguments, dict):
            raise BenchmarkJobError("malformed_tool_call", "tool call arguments must be an object")
        result.append({
            "id": item.get("id") or f"call-{index}",
            "name": function["name"],
            "arguments": arguments,
        })
    return result


def _fixture_result(scenario: Mapping[str, Any], call_index: int, call: Mapping[str, Any]) -> Any:
    fixtures = scenario.get("injected_results")
    if isinstance(fixtures, list) and call_index < len(fixtures):
        fixture = fixtures[call_index]
        if isinstance(fixture, Mapping) and fixture.get("tool") == call["name"]:
            return fixture.get("result")
    # Deterministic debug-loop fixtures are intentionally executable without a real repository.
    debug = {
        0: {"status": "failed", "file": "calc.py"},
        1: {"path": "calc.py", "content": "return a - b"},
        2: {"status": "edited"},
        3: {"status": "passed", "marker": "TESTS-PASS"},
    }
    if scenario.get("scenario_type") == "debug-loop" and call_index in debug:
        return debug[call_index]
    return {"error": "unexpected deterministic tool call"}


def _run_long_session_case(
    scenario: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    endpoint: Mapping[str, str],
    key: str | None,
    max_tokens: int,
    timeout: float,
    caller: ChatCaller,
    chat_template_kwargs: Mapping[str, Any] | None,
    reasoning_effort: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Execute every scripted user turn so endurance evidence has real token growth."""
    scripted_users = [
        copy.deepcopy(item) for item in scenario["messages"] if item.get("role") == "user"
    ]
    messages: list[dict[str, Any]] = []
    growth: list[int] = []
    request_ids: list[str] = []
    turns: list[dict[str, Any]] = []
    final_answer = ""
    failure = None
    reasoning_present = False
    for index, user_message in enumerate(scripted_users):
        messages.append(user_message)
        try:
            response = caller(
                endpoint["base_url"],
                endpoint["model"],
                key,
                messages,
                max_tokens=max_tokens if index == len(scripted_users) - 1 else min(max_tokens, 64),
                timeout=timeout,
                tools=None,
                chat_template_kwargs=chat_template_kwargs,
                reasoning_effort=reasoning_effort,
            )
            payload = response["response"]
            prompt_tokens = _usage_tokens(payload, "prompt_tokens")
            if prompt_tokens is not None:
                growth.append(prompt_tokens)
            if response.get("request_id"):
                request_ids.append(response["request_id"])
            message = _message(payload)
            calls = _normalized_tool_calls(message)
            if calls:
                raise BenchmarkJobError(
                    "parser_error", "long-session response unexpectedly contained tool calls"
                )
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise BenchmarkJobError(
                    "parser_error", "long-session response has no visible assistant content"
                )
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            reasoning_chars = len(reasoning) if isinstance(reasoning, str) else 0
            reasoning_present = reasoning_present or reasoning_chars > 0
            choices = payload.get("choices")
            first_choice = choices[0] if isinstance(choices, list) and choices else {}
            turns.append({
                "latency_ms": response["latency_s"] * 1000,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": _usage_tokens(payload, "completion_tokens"),
                "finish_reason": (
                    first_choice.get("finish_reason")
                    if isinstance(first_choice, Mapping)
                    else None
                ),
                "reasoning_chars": reasoning_chars,
                "tool_call_count": 0,
            })
            if index == len(scripted_users) - 1:
                final_answer = content
            else:
                messages.append({"role": "assistant", "content": content})
        except BenchmarkJobError as exc:
            failure = {"code": exc.code, "message": exc.message}
            break
        except Exception as exc:
            failure = {"code": "endpoint_error", "message": str(exc)[:1024]}
            break
    trace = {
        "tool_calls": [],
        "final_answer": final_answer,
        "history": copy.deepcopy(messages),
        "history_prompt_tokens": growth,
        "failure": failure,
        "reasoning_present": reasoning_present,
        "turns": turns,
    }
    return score_agentic_trace(scenario, expected, trace), request_ids


def _run_agentic_case(
    scenario: Mapping[str, Any],
    expected: Mapping[str, Any],
    *,
    endpoint: Mapping[str, str],
    key: str | None,
    max_steps: int,
    max_tokens: int,
    timeout: float,
    caller: ChatCaller,
    chat_template_kwargs: Mapping[str, Any] | None = None,
    reasoning_effort: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    if expected.get("require_token_growth"):
        return _run_long_session_case(
            scenario,
            expected,
            endpoint=endpoint,
            key=key,
            max_tokens=max_tokens,
            timeout=timeout,
            caller=caller,
            chat_template_kwargs=chat_template_kwargs,
            reasoning_effort=reasoning_effort,
        )
    messages = copy.deepcopy(scenario["messages"])
    observed_calls = []
    growth = []
    request_ids = []
    final_answer = ""
    failure = None
    reasoning_present = False
    turns = []
    for _step in range(max_steps):
        try:
            response = caller(
                endpoint["base_url"],
                endpoint["model"],
                key,
                messages,
                max_tokens=max_tokens,
                timeout=timeout,
                tools=scenario.get("tools") or None,
                chat_template_kwargs=chat_template_kwargs,
                reasoning_effort=reasoning_effort,
            )
            payload = response["response"]
            prompt_tokens = _usage_tokens(payload, "prompt_tokens")
            if prompt_tokens is not None:
                growth.append(prompt_tokens)
            if response.get("request_id"):
                request_ids.append(response["request_id"])
            message = _message(payload)
            calls = _normalized_tool_calls(message)
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            reasoning_chars = len(reasoning) if isinstance(reasoning, str) else 0
            reasoning_present = reasoning_present or reasoning_chars > 0
            choices = payload.get("choices")
            first_choice = choices[0] if isinstance(choices, list) and choices else {}
            turns.append({
                "latency_ms": response["latency_s"] * 1000,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": _usage_tokens(payload, "completion_tokens"),
                "finish_reason": (
                    first_choice.get("finish_reason")
                    if isinstance(first_choice, Mapping)
                    else None
                ),
                "reasoning_chars": reasoning_chars,
                "tool_call_count": len(calls),
            })
            if not calls:
                content = message.get("content")
                final_answer = content if isinstance(content, str) else ""
                break
            message.setdefault("role", "assistant")
            messages.append(message)
            for call in calls:
                observed_calls.append({"name": call["name"], "arguments": call["arguments"]})
                fixture = _fixture_result(scenario, len(observed_calls) - 1, call)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": json.dumps(fixture, sort_keys=True),
                })
        except BenchmarkJobError as exc:
            failure = {"code": exc.code, "message": exc.message}
            break
        except Exception as exc:
            failure = {"code": "endpoint_error", "message": str(exc)[:1024]}
            break
    else:
        failure = {"code": "reasoning_budget_exhausted", "message": "agent step limit reached"}
    trace = {
        "tool_calls": observed_calls,
        "final_answer": final_answer,
        "history": copy.deepcopy(scenario["messages"]) if expected.get("history") is not None else [],
        "history_prompt_tokens": growth,
        "failure": failure,
        "reasoning_present": reasoning_present,
        "turns": turns,
    }
    return score_agentic_trace(scenario, expected, trace), request_ids


def run_agentic_suite(
    profile: Mapping[str, Any],
    spec: Mapping[str, Any],
    *,
    caller: ChatCaller = post_chat,
) -> dict[str, Any]:
    """Execute deterministic agentic scenarios with harness-owned tool results."""
    suite = profile["suites"]["agentic"]
    endpoint = spec["endpoint"]
    key = resolve_api_key(endpoint.get("auth_env"))
    parameters = spec.get("parameters", {})
    thinking_mode = parameters.get("thinking_mode", "default")
    if thinking_mode not in {"default", "enabled", "disabled"}:
        raise BenchmarkJobError(
            "bad_thinking_mode", "thinking_mode must be default, enabled, or disabled"
        )
    reasoning_effort = parameters.get("reasoning_effort")
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORT_CHOICES:
        raise BenchmarkJobError(
            "bad_reasoning_effort", "reasoning_effort is not supported by the harness"
        )
    if reasoning_effort is not None and thinking_mode != "default":
        raise BenchmarkJobError(
            "conflicting_reasoning_controls",
            "reasoning_effort and thinking_mode cannot both be explicit",
        )
    chat_template_kwargs = (
        {"enable_thinking": True}
        if thinking_mode == "enabled"
        else {"enable_thinking": False} if thinking_mode == "disabled" else None
    )
    case_limit = parameters.get("case_limit")
    if case_limit is not None and (
        not isinstance(case_limit, int) or isinstance(case_limit, bool) or case_limit < 1
    ):
        raise BenchmarkJobError("bad_case_limit", "agentic case_limit must be positive")
    case_ids = parameters.get("case_ids")
    if case_ids is not None:
        if (
            not isinstance(case_ids, list)
            or not case_ids
            or any(not isinstance(item, str) or not item for item in case_ids)
            or len(set(case_ids)) != len(case_ids)
        ):
            raise BenchmarkJobError(
                "bad_agentic_case_ids",
                "agentic case_ids must be a non-empty list of unique strings",
            )
        unknown = set(case_ids).difference(suite["cases"])
        if unknown:
            raise BenchmarkJobError(
                "unknown_agentic_case_id",
                "agentic case_ids must be present in the pinned profile",
            )
        selected_cases = case_ids
    else:
        selected_cases = suite["cases"]
    recovery_result = parameters.get("recovery_result", "error")
    observations = []
    request_ids = []
    for case_type in selected_cases:
        for repetition in range(suite["repetitions"]):
            scenario, expected = build_agentic_scenario(
                case_type,
                recovery_result=recovery_result,
                session_turns=parameters.get("session_turns", 12),
            )
            observation, ids = _run_agentic_case(
                scenario,
                expected,
                endpoint=endpoint,
                key=key,
                max_steps=suite["max_steps"],
                max_tokens=suite["max_completion_tokens"],
                timeout=min(float(spec["timeout_s"]), 900.0),
                caller=caller,
                chat_template_kwargs=chat_template_kwargs,
                reasoning_effort=reasoning_effort,
            )
            observation["repetition"] = repetition
            observations.append(observation)
            request_ids.extend(ids)
            if case_limit is not None and len(observations) >= case_limit:
                break
        if case_limit is not None and len(observations) >= case_limit:
            break
    if not observations:
        raise BenchmarkJobError("no_agentic_cases", "profile selected no agentic scenarios")
    passed = sum(item["passed"] for item in observations)
    return {
        "schema": "anvil-serving.agentic-suite-run/v1",
        "request_ids": sorted(set(request_ids)),
        "observations": observations,
        "summary": {
            "attempted": len(observations),
            "passed": passed,
            "pass_rate": passed / len(observations),
            "required_pass_rate": suite["scoring"]["pass_rate_floor"],
        },
        "request_controls": {
            "thinking_mode": thinking_mode,
            "chat_template_kwargs": chat_template_kwargs,
            "reasoning_effort": reasoning_effort,
        },
        "passed": passed / len(observations) >= suite["scoring"]["pass_rate_floor"],
    }
