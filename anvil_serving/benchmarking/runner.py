"""Shared benchmark aggregation helpers."""

import json
import math
import random
import sys
import time
from dataclasses import dataclass

from .artifacts import atomic_write_json as _atomic_write_json
from .evaluation import (
    aggregate_attempts as _aggregate_attempts,
    attempt_passed as _attempt_passed,
    eval_budget,
    failure_class as _failure_class,
    request_control_kwargs as _request_control_kwargs,
    resolve_thinking_settings,
)
from .limits import (
    MAX_TOTAL_CONTEXT_TARGET_TOKENS as _MAX_TOTAL_CONTEXT_TARGET_TOKENS,
    MAX_TOTAL_EVAL_ATTEMPTS as _MAX_TOTAL_EVAL_ATTEMPTS,
    MAX_TOTAL_QUALITY_TOKENS as _MAX_TOTAL_QUALITY_TOKENS,
    MIN_COMPARABLE_REPETITIONS as _MIN_COMPARABLE_REPETITIONS,
)
from .requests import (
    CHARS_PER_TOKEN,
    _choice_messages,
    clamp_ctx,
    ctx_cap,
    detect_max_model_len,
    make_shared_prefix,
    make_prompt,
    post_chat,
    response_observation,
    stream_chat,
    validate_function_tool_call,
    validate_stream_result,
)
from .specs import evaluate_text_checks, parse_context_targets, parse_csv


def cached_fraction(usage):
    if not usage:
        return None
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    prompt_tokens = usage.get("prompt_tokens")
    if cached is None or not prompt_tokens:
        return None
    return cached / prompt_tokens


def percentiles(values, quantiles):
    """Return nearest-rank percentiles after sorting the samples once."""
    for quantile in quantiles:
        if not 0 <= quantile <= 100:
            raise ValueError("percentile must be from 0 through 100")
    ranked = sorted(value for value in values if value is not None)
    if not ranked:
        return [0 for _ in quantiles]
    resolved = []
    for quantile in quantiles:
        rank = max(0, math.ceil(quantile * len(ranked) / 100) - 1)
        resolved.append(ranked[rank])
    return resolved


def percentile(values, quantile):
    """Return one percentile while preserving the benchmark's historical rank rule."""
    return percentiles(values, [quantile])[0]


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _usage_token_count(usage, key):
    if not isinstance(usage, dict):
        return None
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def result_timing(result, fallback_index=None):
    """Return one publication-safe request timing row.

    Effective prefill throughput uses prompt tokens divided by client-observed
    TTFT, so it includes queueing, scheduling, prefill, and first-token work.
    Decode throughput excludes the first token and uses the interval after TTFT.
    """
    if not isinstance(result, dict):
        return None
    ttft = _finite_number(result.get("ttft"))
    e2e = _finite_number(result.get("e2e"))
    if ttft is None or e2e is None or ttft < 0 or e2e < ttft:
        return None
    usage = result.get("usage")
    prompt_tokens = _usage_token_count(usage, "prompt_tokens")
    output_tokens = result.get("out_toks")
    if (
        isinstance(output_tokens, bool)
        or not isinstance(output_tokens, int)
        or output_tokens < 0
    ):
        output_tokens = None
    source = result.get("output_token_source")
    exact_output_tokens = source in (None, "usage")
    generation = e2e - ttft
    decode_tokens = (
        max(output_tokens - 1, 0)
        if exact_output_tokens and output_tokens is not None else None
    )
    row = {
        "request_index": result.get("request_index", fallback_index),
        "planned_context_tokens": result.get("planned_context_tokens"),
        "prompt_tokens": prompt_tokens,
        "output_tokens": output_tokens if exact_output_tokens else None,
        "output_token_source": source or "unknown",
        "ttft_ms": ttft * 1000.0,
        "generation_ms": generation * 1000.0,
        "e2e_ms": e2e * 1000.0,
        "effective_prefill_tok_s": (
            prompt_tokens / ttft if prompt_tokens is not None and ttft > 0 else None
        ),
        "decode_tok_s": (
            decode_tokens / generation
            if decode_tokens is not None and decode_tokens > 0 and generation > 0
            else None
        ),
        "mean_inter_token_latency_ms": (
            generation * 1000.0 / decode_tokens
            if decode_tokens is not None and decode_tokens > 0 and generation > 0
            else None
        ),
    }
    return row


def result_timings(results):
    rows = [
        row
        for index, result in enumerate(results)
        if (row := result_timing(result, fallback_index=index)) is not None
    ]
    return sorted(
        rows,
        key=lambda row: (
            row["request_index"] is None,
            row["request_index"] if row["request_index"] is not None else 0,
        ),
    )


def _timing_percentiles(rows, key, *, scale=1.0):
    values = [row.get(key) for row in rows if row.get(key) is not None]
    if not values:
        return None, None
    p50, p95 = percentiles(values, [50, 95])
    return p50 * scale, p95 * scale


def result_metrics(results):
    timing_rows = result_timings(results)
    ttft_p50, ttft_p95 = _timing_percentiles(timing_rows, "ttft_ms")
    e2e_p50, e2e_p95 = _timing_percentiles(timing_rows, "e2e_ms")
    generation_p50, generation_p95 = _timing_percentiles(
        timing_rows, "generation_ms"
    )
    prefill_p50, prefill_p95 = _timing_percentiles(
        timing_rows, "effective_prefill_tok_s"
    )
    decode_p50, decode_p95 = _timing_percentiles(timing_rows, "decode_tok_s")
    mitl_p50, mitl_p95 = _timing_percentiles(
        timing_rows, "mean_inter_token_latency_ms"
    )
    prompt_p50, prompt_p95 = _timing_percentiles(timing_rows, "prompt_tokens")
    raw_output_units = sum(
        result.get("out_toks") or 0
        for result in results
        if isinstance(result, dict)
    )
    sources = [
        result.get("output_token_source")
        for result in results
        if isinstance(result, dict)
    ]
    exact_output_tokens = all(source in (None, "usage") for source in sources)
    content_chunks = sum(
        result.get("content_chunks") or 0
        for result in results
        if isinstance(result, dict)
    )
    prompt_tokens = [
        row["prompt_tokens"]
        for row in timing_rows
        if row["prompt_tokens"] is not None
    ]
    return {
        "ttft_p50_ms": ttft_p50,
        "ttft_p95_ms": ttft_p95,
        "ttft_samples": len(timing_rows),
        "generation_p50_ms": generation_p50,
        "generation_p95_ms": generation_p95,
        "e2e_p50_ms": e2e_p50,
        "e2e_p95_ms": e2e_p95,
        "effective_prefill_tok_s_p50": prefill_p50,
        "effective_prefill_tok_s_p95": prefill_p95,
        "decode_tok_s_p50": decode_p50,
        "decode_tok_s_p95": decode_p95,
        "mean_inter_token_latency_ms_p50": mitl_p50,
        "mean_inter_token_latency_ms_p95": mitl_p95,
        "prompt_tokens": sum(prompt_tokens) if prompt_tokens else None,
        "prompt_token_samples": len(prompt_tokens),
        "prompt_tokens_p50": prompt_p50,
        "prompt_tokens_p95": prompt_p95,
        "output_tokens": raw_output_units if exact_output_tokens else None,
        "content_chunks": content_chunks,
    }

BAKEOFF_TOOL = {
    "type": "function",
    "function": {
        "name": "record_weather_zip",
        "description": "Record the ZIP code the user supplied for weather lookup.",
        "parameters": {
            "type": "object",
            "properties": {"zip": {"type": "string"}},
            "required": ["zip"],
        },
    },
}

INTELLIGENCE_PROMPTS = [
    {
        "id": "unified_diff_timeout_edit",
        "prompt": (
            "You are editing app.py. Original file:\n"
            "timeout = 30\n"
            "retries = 2\n\n"
            "Return only a unified diff that changes timeout to 45 and leaves "
            "retries unchanged."
        ),
        "checks": [
            {"name": "diff_shape", "contains_all": ["---", "+++", "@@"]},
            {"name": "removes_old_timeout", "contains": "-timeout = 30"},
            {"name": "adds_new_timeout", "contains": "+timeout = 45"},
        ],
    },
    {
        "id": "parallel_timeout_triage",
        "prompt": (
            "A voice agent calls STT, an LLM, and TTS. The total turn timeout is "
            "2500 ms. Logs show STT=550 ms, LLM=1800 ms, TTS=650 ms. In one "
            "concise sentence, identify the problem and one practical fix."
        ),
        "checks": [
            {
                "name": "identifies_budget_overrun",
                "contains_any": ["timeout", "budget", "overrun", "too slow", "exceeds"],
            },
            {
                "name": "offers_latency_fix",
                "contains_any": [
                    "faster", "reduce", "parallel", "cache", "shorter", "limit",
                    "stream",
                ],
            },
        ],
    },
]

SESSION_RECALL_PROMPT = [
    {"role": "user", "content": "Remember this session code: RIVER-918. Reply with ok."},
    {"role": "assistant", "content": "ok"},
    {"role": "user", "content": "What session code should be used? Reply with only the code."},
]

@dataclass(frozen=True)
class QualityPlan:
    """Validated quality inputs consumed unchanged by the runner."""

    suites: tuple[str, ...]
    context_targets: tuple[int, ...]


def validate_eval_work_plan(args, suite_spec):
    """Reject a quality plan whose aggregate requests or retained output can explode."""
    selected = parse_csv(args.suite, default=[] if suite_spec else ["chat"])
    context_targets = parse_context_targets(args.context_targets)
    for flag, value, maximum in (
            ("--shared-prefix-tokens", args.shared_prefix_tokens, 262144),
            ("--max-tokens", args.max_tokens, 65536),
            ("--max-model-len", args.max_model_len, 1048576),
            ("--margin", args.margin, 1048576)):
        minimum = 1 if flag == "--max-tokens" else 0
        if not minimum <= value <= maximum:
            raise ValueError("%s must be from %d through %d" % (flag, minimum, maximum))
    if ("chat" in selected or "context" in selected) and (
            sum(context_targets) > _MAX_TOTAL_CONTEXT_TARGET_TOKENS):
        raise ValueError(
            "quality context plan exceeds %d aggregate prompt tokens"
            % _MAX_TOTAL_CONTEXT_TARGET_TOKENS
        )
    budgets = []
    if "intelligence" in selected:
        budgets.extend(eval_budget({}, args) for _ in INTELLIGENCE_PROMPTS)
    if "tool" in selected:
        budgets.append(eval_budget({}, args))
    if "session" in selected:
        budgets.append(eval_budget({}, args))
    if suite_spec:
        budgets.extend(eval_budget(item, args) for item in suite_spec["evals"])
    attempt_count = len(budgets) * args.eval_repetitions
    if attempt_count > _MAX_TOTAL_EVAL_ATTEMPTS:
        raise ValueError(
            "quality plan exceeds %d total attempts" % _MAX_TOTAL_EVAL_ATTEMPTS
        )
    requested_tokens = sum(item["max_completion_tokens"] for item in budgets)
    requested_tokens *= args.eval_repetitions
    if requested_tokens > _MAX_TOTAL_QUALITY_TOKENS:
        raise ValueError(
            "quality plan exceeds %d requested completion tokens"
            % _MAX_TOTAL_QUALITY_TOKENS
        )
    return QualityPlan(tuple(selected), tuple(context_targets))

# measured subagent ctx percentiles (from role_split): rough inverse-CDF sampler
SUBAGENT_CTX = [
    (0.218, 16000),
    (0.602, 32768),
    (0.906, 65536),
    (0.995, 131072),
    (1.0, 262144),
]


def sample_ctx(random_value=None):
    """Sample one context bucket from measured cumulative upper bounds."""
    value = random.random() if random_value is None else random_value
    if not 0 <= value <= 1:
        raise ValueError("random sample must be from 0 through 1")
    for upper_bound, context_tokens in SUBAGENT_CTX:
        if value <= upper_bound:
            return context_tokens
    return SUBAGENT_CTX[-1][1]


def run_bakeoff(
    a,
    api_key,
    *,
    plan=None,
    post_request=post_chat,
    stream_request=stream_chat,
    detect_context_limit=detect_max_model_len,
):
    """Run selected bakeoff suites against one already-loaded endpoint.

    This mode intentionally never starts, stops, unloads, or reloads models. It
    only sends OpenAI-compatible requests to the supplied base URL and records
    both successful sub-checks and failures in one JSON artifact.
    """
    started_at = time.time()
    started_monotonic = time.perf_counter()
    suite_spec = getattr(a, "suite_spec", None)
    # --suite-file alone runs ONLY the external suite: the default chat/context
    # probe must be opted into (--suite chat) so an unrelated probe failure can
    # never pollute external-suite evidence (or trip the notebook no_failures gate).
    suites = list(
        plan.suites
        if plan is not None
        else parse_csv(a.suite, default=[] if suite_spec else ["chat"])
    )
    known_suites = {"chat", "context", "tool", "session", "intelligence", "voice"}
    unknown_suites = sorted(set(suites) - known_suites)
    if unknown_suites:
        raise ValueError("unknown quality suite(s): %s" % ", ".join(unknown_suites))
    context_targets = list(
        plan.context_targets
        if plan is not None
        else parse_context_targets(a.context_targets)
    )
    max_model_len = a.max_model_len or detect_context_limit(
        a.base_url, a.model, api_key
    )
    cap = ctx_cap(max_model_len, a.max_tokens, a.margin)
    ctk, reasoning_effort, thinking_section = resolve_thinking_settings(a)
    control_kwargs = _request_control_kwargs(ctk, reasoning_effort)
    failures = []
    chat_results = []
    context_results = []

    should_run_context = "chat" in suites or "context" in suites
    if should_run_context:
        shared = make_shared_prefix(a.shared_prefix_tokens)
        chars_per_token = CHARS_PER_TOKEN
        for target in context_targets:
            clamped = clamp_ctx(target, cap)
            prompt = make_prompt(
                shared, clamped, target, max_prompt_tokens=cap,
                chars_per_token=chars_per_token,
            )
            row = {
                "target_tokens": target,
                "clamped_tokens": clamped,
                "attempted_context_tokens": clamped,
                # estimate with the SAME rate the prompt was sized with — this field is
                # the operator's diagnostic when a row fails (no usage comes back), so
                # it must not drift from the fixed constant once calibration advances.
                "estimated_prompt_tokens": int(len(prompt) / chars_per_token),
                "chars_per_token": round(chars_per_token, 3),
                "status": "pending",
            }
            try:
                result = validate_stream_result(stream_request(
                    a.base_url, a.model, prompt, api_key, a.max_tokens,
                    timeout=a.timeout, **control_kwargs,
                ))
                row.update({
                    "status": "passed",
                    "ttft_ms": result["ttft"] * 1000.0,
                    "e2e_ms": result["e2e"] * 1000.0,
                    "output_tokens": result["out_toks"],
                    "usage": result.get("usage"),
                })
                chat_results.append(result)
                # Calibrate sizing from the serve's REAL tokenizer count so later
                # targets land ON target instead of ~15% under the conservative default.
                usage = result.get("usage") or {}
                if usage.get("prompt_tokens"):
                    measured = len(prompt) / usage["prompt_tokens"]
                    if 1.0 <= measured <= 10.0:  # ignore bogus usage
                        chars_per_token = measured
            except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                row.update({"status": "failed", "error": str(exc)})
                failures.append({
                    "suite": "context" if "context" in suites else "chat",
                    "target_tokens": target,
                    "error": str(exc),
                })
            context_results.append(row)

    tool_section = {"status": "not_run", "checks": []}
    if "tool" in suites:
        check = {
            "name": "openai_tool_call_smoke",
            "status": "pending",
            "expected_function": "record_weather_zip",
            "expected_arguments": {"zip": "98101"},
        }
        budget = eval_budget({}, a)
        attempts = []
        for attempt_index in range(a.eval_repetitions):
            try:
                result = post_request(
                    a.base_url,
                    a.model,
                    api_key,
                    [{"role": "user", "content": "Call record_weather_zip with zip 98101."}],
                    max_tokens=budget["max_completion_tokens"],
                    timeout=a.timeout,
                    tools=[BAKEOFF_TOOL],
                    **control_kwargs,
                )
                response = result.get("response", {})
                messages = _choice_messages(response)
                observation = response_observation(response)
                validations = [
                    validate_function_tool_call(
                        message, "record_weather_zip", {"zip": "98101"}
                    )
                    for message in messages
                ]
                valid = [item for item in validations if item["valid"]]
                passed = _attempt_passed(
                    observation, bool(valid), allowed_finish_reasons=("tool_calls", "stop")
                )
                attempt = {
                    "attempt": attempt_index + 1,
                    "status": "passed" if passed else "failed",
                    "latency_ms": result["latency_s"] * 1000.0,
                    "tool_call_count": sum(
                        len(message.get("tool_calls") or []) for message in messages
                    ),
                    "valid_tool_call_count": len(valid),
                    "arguments": valid[0]["arguments"] if valid else None,
                    "validation_errors": [
                        item["error"] for item in validations if item["error"]
                    ],
                    "budget": budget,
                    **observation,
                }
                attempt["failure_class"] = _failure_class(
                    observation, checks_passed=bool(valid)
                )
                if not valid:
                    attempt["error"] = (
                        attempt["validation_errors"][0]
                        if attempt["validation_errors"]
                        else "response did not include valid tool_calls"
                    )
                elif not passed:
                    attempt["error"] = "tool response did not finish cleanly"
                attempts.append(attempt)
            except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                attempts.append({
                    "attempt": attempt_index + 1,
                    "status": "failed",
                    "error": str(exc),
                    "failure_class": "request_error",
                    "budget": budget,
                })
        _aggregate_attempts(check, attempts, a.eval_min_pass_rate)
        if check["status"] != "passed":
            check["error"] = check.get("error") or "pass rate below threshold"
            failures.append({
                "suite": "tool",
                "error": check["error"],
                "failure_classes": sorted({
                    item.get("failure_class") for item in attempts
                    if item.get("failure_class")
                }),
                "pass_rate": check["pass_rate"],
            })
        tool_section = {"status": check["status"], "checks": [check]}

    session_section = {"status": "not_run", "checks": []}
    if "session" in suites:
        check = {"name": "single_request_multiturn_recall", "status": "pending"}
        budget = eval_budget({}, a)
        attempts = []
        for attempt_index in range(a.eval_repetitions):
            try:
                result = post_request(
                    a.base_url,
                    a.model,
                    api_key,
                    SESSION_RECALL_PROMPT,
                    max_tokens=budget["max_completion_tokens"],
                    timeout=a.timeout,
                    **control_kwargs,
                )
                response = result.get("response", {})
                observation = response_observation(response)
                marker_passed = "RIVER-918" in observation["content"].replace(" ", "")
                passed = _attempt_passed(observation, marker_passed)
                attempt = {
                    "attempt": attempt_index + 1,
                    "status": "passed" if passed else "failed",
                    "latency_ms": result["latency_s"] * 1000.0,
                    "expected": "RIVER-918",
                    "budget": budget,
                    **observation,
                }
                attempt["failure_class"] = _failure_class(
                    observation, checks_passed=marker_passed
                )
                if not marker_passed:
                    attempt["error"] = "response did not recall session code"
                elif not passed:
                    attempt["error"] = "session response did not finish cleanly"
                attempts.append(attempt)
            except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                attempts.append({
                    "attempt": attempt_index + 1,
                    "status": "failed",
                    "error": str(exc),
                    "failure_class": "request_error",
                    "budget": budget,
                })
        _aggregate_attempts(check, attempts, a.eval_min_pass_rate)
        if check["status"] != "passed":
            check["error"] = check.get("error") or "pass rate below threshold"
            failures.append({
                "suite": "session",
                "error": check["error"],
                "failure_classes": sorted({
                    item.get("failure_class") for item in attempts
                    if item.get("failure_class")
                }),
                "pass_rate": check["pass_rate"],
            })
        session_section = {"status": check["status"], "checks": [check]}

    intelligence_section = {"status": "not_run", "checks": []}
    if "intelligence" in suites:
        checks = []
        for spec in INTELLIGENCE_PROMPTS:
            check = {
                "id": spec["id"],
                "status": "pending",
                "validator": "deterministic_text_checks",
            }
            budget = eval_budget({}, a)
            attempts = []
            for attempt_index in range(a.eval_repetitions):
                try:
                    result = post_request(
                        a.base_url,
                        a.model,
                        api_key,
                        [{"role": "user", "content": spec["prompt"]}],
                        max_tokens=budget["max_completion_tokens"],
                        timeout=a.timeout,
                        **control_kwargs,
                    )
                    observation = response_observation(result.get("response", {}))
                    text_checks = evaluate_text_checks(observation["content"], spec["checks"])
                    checks_passed = all(item["passed"] for item in text_checks)
                    passed = _attempt_passed(observation, checks_passed)
                    attempts.append({
                        "attempt": attempt_index + 1,
                        "status": "passed" if passed else "failed",
                        "latency_ms": result["latency_s"] * 1000.0,
                        "text_checks": text_checks,
                        "budget": budget,
                        "failure_class": _failure_class(
                            observation, checks_passed=checks_passed
                        ),
                        **observation,
                    })
                except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                    attempts.append({
                        "attempt": attempt_index + 1,
                        "status": "failed",
                        "error": str(exc),
                        "failure_class": "request_error",
                        "budget": budget,
                    })
            _aggregate_attempts(check, attempts, a.eval_min_pass_rate)
            if check["status"] != "passed":
                failure_classes = sorted({
                    attempt.get("failure_class") for attempt in attempts
                    if attempt.get("failure_class")
                })
                if not check.get("error"):
                    check["error"] = "pass rate below threshold"
                failures.append({
                    "suite": "intelligence",
                    "prompt_id": spec["id"],
                    "error": check["error"],
                    "failure_classes": failure_classes,
                    "pass_rate": check["pass_rate"],
                })
            checks.append(check)
        intelligence_section = {
            "status": "passed" if checks and all(c["status"] == "passed" for c in checks) else "failed",
            "checks": checks,
        }

    # --suite-file: externally-authored evals through the SAME check engine as the
    # built-in intelligence/tool suites (spec validated up front in main()).
    external_suites = {}
    spec = suite_spec
    if spec:
        checks = []
        for item in spec["evals"]:
            validators = []
            if item.get("checks"):
                validators.append("deterministic_text_checks")
            if item.get("expect_tool"):
                validators.append("tool_call")
            check = {
                "id": item["id"],
                "status": "pending",
                "validator": "+".join(validators),
            }
            request_messages = item.get("messages") or [
                {"role": "user", "content": item["prompt"]}
            ]
            budget = eval_budget(item, a)
            attempts = []
            for attempt_index in range(a.eval_repetitions):
                try:
                    result = post_request(
                        a.base_url,
                        a.model,
                        api_key,
                        request_messages,
                        max_tokens=budget["max_completion_tokens"],
                        timeout=a.timeout,
                        tools=item.get("tools"),
                        **control_kwargs,
                    )
                    response = result.get("response", {})
                    messages = _choice_messages(response)
                    observation = response_observation(response)
                    text_checks = evaluate_text_checks(
                        observation["content"], item.get("checks") or []
                    )
                    errors = [c["name"] for c in text_checks if not c["passed"]]
                    attempt = {
                        "attempt": attempt_index + 1,
                        "latency_ms": result["latency_s"] * 1000.0,
                        "text_checks": text_checks,
                        "budget": budget,
                        **observation,
                    }
                    tool_failed = False
                    expect = item.get("expect_tool")
                    if expect:
                        validations = [
                            validate_function_tool_call(
                                message, expect["name"], expect.get("required_args") or {}
                            )
                            for message in messages
                        ]
                        valid = [value for value in validations if value["valid"]]
                        attempt["tool_call"] = {
                            "valid": bool(valid),
                            "arguments": valid[0]["arguments"] if valid else None,
                            "validation_errors": [
                                value["error"] for value in validations if value["error"]
                            ],
                        }
                        if not valid:
                            tool_failed = True
                            errors.extend(
                                attempt["tool_call"]["validation_errors"]
                                or ["response did not include tool_calls"]
                            )
                    checks_passed = not errors
                    allowed_finishes = ("tool_calls", "stop") if expect else ("stop",)
                    passed = _attempt_passed(
                        observation, checks_passed,
                        allowed_finish_reasons=allowed_finishes,
                    )
                    attempt["status"] = "passed" if passed else "failed"
                    failure_class = _failure_class(
                        observation, checks_passed=checks_passed
                    )
                    if tool_failed and failure_class in {
                            "deterministic_check_failed", "visible_answer_missing"}:
                        failure_class = "tool_call_failed"
                    attempt["failure_class"] = failure_class
                    if errors:
                        attempt["error"] = "; ".join(errors)
                    attempts.append(attempt)
                except Exception as exc:  # noqa: BLE001 - failure is benchmark evidence
                    attempts.append({
                        "attempt": attempt_index + 1,
                        "status": "failed",
                        "error": str(exc),
                        "failure_class": "request_error",
                        "budget": budget,
                    })
            _aggregate_attempts(check, attempts, a.eval_min_pass_rate)
            if check["status"] != "passed":
                failure_classes = sorted({
                    attempt.get("failure_class") for attempt in attempts
                    if attempt.get("failure_class")
                })
                if not check.get("error"):
                    check["error"] = "pass rate below threshold"
                failures.append({
                    "suite": spec["suite"],
                    "eval_id": item["id"],
                    "error": check["error"],
                    "failure_classes": failure_classes,
                    "pass_rate": check["pass_rate"],
                })
            checks.append(check)
        external_suites[spec["suite"]] = {
            "status": "passed" if all(c["status"] == "passed" for c in checks) else "failed",
            "source": a.suite_file,
            "source_sha256": spec["_source_sha256"],
            "date": spec.get("date"),
            "work_class": spec.get("work_class"),
            "evidence_use": spec.get("evidence_use", "diagnostic"),
            "validator_strength": spec.get(
                "validator_strength", "deterministic_marker"
            ),
            "checks": checks,
        }

    voice_section = {
        "status": "not_run",
        "stt_latency_ms": a.stt_latency_ms,
        "llm_latency_ms": None,
        "tts_latency_ms": a.tts_latency_ms,
        "total_turn_latency_ms": a.voice_latency_ms,
    }
    if "voice" in suites:
        if a.voice_latency_ms is None:
            voice_section["status"] = "skipped"
            voice_section["reason"] = "voice latency metrics were not supplied"
        else:
            voice_section["status"] = "recorded"

    metrics = result_metrics(chat_results)
    wall_ms = (time.perf_counter() - started_monotonic) * 1000.0
    passed_contexts = [
        r["target_tokens"] for r in context_results if r.get("status") == "passed"
    ]
    intelligence_checks = intelligence_section.get("checks") or []
    intelligence_pass_rate = None
    if intelligence_checks:
        intelligence_pass_rate = (
            sum(1 for check in intelligence_checks if check.get("status") == "passed")
            / len(intelligence_checks)
        )
    evidence = {
        "schema": "anvil-serving.fast-tier-bakeoff/v1",
        "run_id": time.strftime("fast-bakeoff-%Y%m%dT%H%M%SZ", time.gmtime(started_at)),
        "identity": {
            "candidate_id": a.candidate_id,
            "config_id": a.config_id,
            "model": a.model,
            "base_url": a.base_url,
            "engine": a.engine,
            "gpu": a.gpu,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        },
        "source_recipe": {
            "ref": a.source_recipe,
            "serve_command": a.serve_command,
        },
        "selection": {
            "suites": suites,
            "context_targets": context_targets,
            "requests_per_context": 1,
            "endpoint_already_loaded": True,
        },
        "evaluation_protocol": {
            "version": 3,
            "repetitions": a.eval_repetitions,
            "minimum_pass_rate": a.eval_min_pass_rate,
            "minimum_comparable_repetitions": _MIN_COMPARABLE_REPETITIONS,
            "visible_answer_tokens": a.visible_answer_tokens or 256,
            "reasoning_headroom_tokens": (
                a.reasoning_headroom_tokens
                if a.reasoning_headroom_tokens is not None else 0
            ),
            "budget_semantics": (
                "visible_answer_tokens plus reasoning_headroom_tokens are sent as one "
                "max_tokens cap; the endpoint does not hard-partition the channels"
            ),
            "records_full_visible_answer": True,
            "records_finish_reason": True,
            "records_reasoning_channel_metadata": True,
        },
        "timing": {
            "wall_ms": wall_ms,
            "chat": metrics,
        },
        "context": {
            "max_model_len": max_model_len,
            "cap_tokens": cap,
            "targets": context_results,
        },
        "tool": tool_section,
        "session": session_section,
        "intelligence": intelligence_section,
        "suites": external_suites,
        "thinking": thinking_section,
        "voice": voice_section,
        "score_inputs": {
            "voice_latency_ms": a.voice_latency_ms,
            "tool_call_passed": tool_section.get("status") == "passed",
            "session_recall_passed": session_section.get("status") == "passed",
            "intelligence_pass_rate": intelligence_pass_rate,
            "usable_context_tokens": max(passed_contexts) if passed_contexts else None,
            "ttft_p50_ms": metrics["ttft_p50_ms"],
            "e2e_p50_ms": metrics["e2e_p50_ms"],
            "thinking_mode": thinking_section["mode"],
            "operational_fit_notes": [
                "endpoint was already loaded; benchmark did not start or stop serves"
            ],
        },
        "failures": failures,
    }
    evidence["identity"] = {
        key: value for key, value in evidence["identity"].items() if value is not None
    }
    if a.evidence_out:
        _atomic_write_json(a.evidence_out, evidence)
        print("wrote bakeoff evidence: " + a.evidence_out)
    else:
        print(json.dumps(evidence, indent=2, sort_keys=True))

    # Persist into the bakeoff notebook (in ADDITION to --evidence-out, so the
    # existing behavior never regresses). Requires --notebook-task/-hardware to
    # key the comparison; missing them is a loud error, not a silent skip.
    if getattr(a, "notebook", None):
        if not a.notebook_task or not a.notebook_hardware:
            print("error: --notebook requires --notebook-task and --notebook-hardware",
                  file=sys.stderr)
            return 2
        from ..external_benchmarks import store as _nb_store

        row_id = _nb_store.record_bakeoff_run(
            a.notebook, evidence,
            task=a.notebook_task, hardware=a.notebook_hardware,
            evidence_path=a.evidence_out,
        )
        print("recorded bakeoff run %s into notebook %s (row %d)"
              % (evidence["run_id"], a.notebook, row_id))
    return 1 if failures else 0
