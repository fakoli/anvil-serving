"""Evaluation budgets, attempt classification, and aggregation."""

import math
from collections.abc import Mapping

from .limits import MAX_QUALITY_COMPLETION_TOKENS


def resolve_thinking_settings(args):
    """Resolve CLI thinking flags into request kwargs plus evidence metadata."""
    mode = getattr(args, "thinking_mode", None) or "default"
    if getattr(args, "no_thinking", False):
        mode = "disabled"

    if mode == "enabled":
        kwargs = {"enable_thinking": True}
    elif mode == "disabled":
        kwargs = {"enable_thinking": False}
    else:
        kwargs = None

    reasoning_effort = getattr(args, "reasoning_effort", None)
    if reasoning_effort is not None:
        kwargs = None
        mechanism = "reasoning_effort"
        requested = reasoning_effort
    elif kwargs is not None:
        mechanism = "chat_template_kwargs"
        requested = kwargs
    elif mode == "unsupported":
        mechanism = "unsupported"
        requested = None
    else:
        mechanism = "none"
        requested = None

    control_status = getattr(args, "control_status", None)
    if requested is None:
        control_status = mechanism
    elif control_status is None:
        control_status = "requested_unverified"
    return kwargs, reasoning_effort, {
        "mode": mode,
        "chat_template_kwargs": kwargs,
        "reasoning_effort": reasoning_effort,
        "control_mechanism": mechanism,
        "control_requested": requested,
        "control_status": control_status,
        "control_evidence": getattr(args, "control_evidence", None),
        "control_evidence_sha256": getattr(args, "control_evidence_sha256", None),
        "unsupported": mode == "unsupported",
    }


def request_control_kwargs(chat_template_kwargs, reasoning_effort):
    """Build call kwargs without passing a new optional key to legacy fakes."""
    kwargs = {"chat_template_kwargs": chat_template_kwargs}
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort
    return kwargs


def resolve_sampling_settings(args):
    """Resolve explicitly requested sampler values without guessing engine defaults."""
    requested_temperature = getattr(args, "temperature", None)
    requested_top_p = getattr(args, "top_p", None)
    return normalize_sampling({
        "temperature": {
            "requested": requested_temperature,
            "effective_request": (
                requested_temperature if requested_temperature is not None else 0.0
            ),
            "sent": True,
        },
        "top_p": {
            "requested": requested_top_p,
            "effective_request": requested_top_p,
            "sent": requested_top_p is not None,
        },
    })


def normalize_sampling(sampling: object) -> dict:
    """Validate and normalize producer sampler provenance without inferred values."""
    if not isinstance(sampling, Mapping) or set(sampling) != {"temperature", "top_p"}:
        raise ValueError("sampling must contain exactly temperature and top_p")
    normalized: dict[str, dict[str, int | float | bool | None]] = {}
    for field, fallback, minimum, maximum, sent_when_none in (
        ("temperature", 0.0, 0.0, 2.0, True),
        ("top_p", None, 0.0, 1.0, False),
    ):
        entry = sampling[field]
        if not isinstance(entry, Mapping) or set(entry) != {
            "requested", "effective_request", "sent",
        }:
            raise ValueError(
                f"sampling.{field} must contain exactly requested, effective_request, and sent"
            )
        requested = entry["requested"]
        if requested is not None:
            if (
                isinstance(requested, bool)
                or not isinstance(requested, (int, float))
                or not math.isfinite(requested)
                or not minimum <= requested <= maximum
                or field == "top_p" and requested == 0
            ):
                raise ValueError(f"sampling.{field}.requested is outside the supported range")
        expected_effective = requested if requested is not None else fallback
        effective = entry["effective_request"]
        if effective != expected_effective:
            raise ValueError(
                f"sampling.{field}.effective_request is inconsistent with requested"
            )
        if effective is not None and (
            isinstance(effective, bool)
            or not isinstance(effective, (int, float))
            or not math.isfinite(effective)
            or not minimum <= effective <= maximum
            or field == "top_p" and effective == 0
        ):
            raise ValueError(f"sampling.{field}.effective_request is outside the supported range")
        expected_sent = requested is not None or sent_when_none
        if entry["sent"] is not expected_sent:
            raise ValueError(f"sampling.{field}.sent is inconsistent with requested")
        normalized[field] = {
            "requested": requested,
            "effective_request": effective,
            "sent": entry["sent"],
        }
    return normalized


def request_sampling_kwargs(sampling):
    """Forward sampler kwargs only when the operator explicitly requested them."""
    sampling = normalize_sampling(sampling)
    kwargs = {}
    temperature = sampling["temperature"]
    top_p = sampling["top_p"]
    if temperature["requested"] is not None:
        kwargs["temperature"] = temperature["effective_request"]
    if top_p["requested"] is not None:
        kwargs["top_p"] = top_p["effective_request"]
    return kwargs


def eval_budget(item, args, *, default_visible=256):
    """Resolve a quality-eval completion allocation."""
    cli_visible = getattr(args, "visible_answer_tokens", None)
    cli_headroom = getattr(args, "reasoning_headroom_tokens", None)
    visible = cli_visible
    if visible is None:
        visible = item.get("visible_answer_tokens")
    legacy_budget = visible is None and item.get("max_tokens") is not None
    if legacy_budget:
        visible = item["max_tokens"]
    if visible is None:
        visible = default_visible
    headroom = cli_headroom
    if headroom is None:
        headroom = item.get("reasoning_headroom_tokens", 0)
    resolved = {
        "visible_answer_tokens": int(visible),
        "reasoning_headroom_tokens": int(headroom),
        "max_completion_tokens": int(visible) + int(headroom),
        "legacy_max_tokens_as_visible": bool(legacy_budget),
    }
    if not 0 < resolved["visible_answer_tokens"] <= MAX_QUALITY_COMPLETION_TOKENS:
        raise ValueError("resolved visible-answer allocation is outside the safe range")
    if not 0 <= resolved["reasoning_headroom_tokens"] <= MAX_QUALITY_COMPLETION_TOKENS:
        raise ValueError("resolved reasoning-headroom allocation is outside the safe range")
    if resolved["max_completion_tokens"] > MAX_QUALITY_COMPLETION_TOKENS:
        raise ValueError(
            "resolved visible-answer plus reasoning-headroom allocation exceeds %d"
            % MAX_QUALITY_COMPLETION_TOKENS
        )
    return resolved


def failure_class(observation, *, checks_passed):
    has_visible_content = bool(observation["content"].strip())
    if (not has_visible_content and observation["finish_reason"] == "length"
            and (observation["reasoning_chars"] or observation["reasoning_tokens"])):
        return "reasoning_budget_exhausted"
    if (has_visible_content and observation["finish_reason"] == "length"
            and (observation["reasoning_chars"] or observation["reasoning_tokens"])):
        return "completion_budget_exhausted_after_visible_output"
    if has_visible_content and observation["finish_reason"] == "length":
        return "visible_answer_budget_exhausted"
    if observation["finish_reason"] not in {"stop", "tool_calls"}:
        return "unexpected_finish_reason"
    if checks_passed:
        return None
    if not has_visible_content:
        return "visible_answer_missing"
    return "deterministic_check_failed"


def attempt_passed(observation, checks_passed, *, allowed_finish_reasons=("stop",)):
    """A deterministic match is not a pass when generation did not finish cleanly."""
    return bool(
        checks_passed and observation.get("finish_reason") in set(allowed_finish_reasons)
    )


def aggregate_attempts(check, attempts, min_pass_rate):
    passed = sum(1 for attempt in attempts if attempt.get("status") == "passed")
    pass_rate = passed / len(attempts) if attempts else 0.0
    check.update({
        "attempts": attempts,
        "pass_count": passed,
        "attempt_count": len(attempts),
        "pass_rate": pass_rate,
        "required_pass_rate": min_pass_rate,
        "status": "passed" if pass_rate >= min_pass_rate else "failed",
    })
    if check["status"] != "passed" and not check.get("error"):
        errors = [
            str(attempt["error"])
            for attempt in attempts
            if attempt.get("error")
        ]
        check["error"] = errors[0] if errors else "pass rate below threshold"
    if len(attempts) == 1:
        for key in (
            "latency_ms", "text_checks", "content", "content_excerpt",
            "finish_reason", "reasoning_field", "reasoning_chars",
            "reasoning_excerpt", "reasoning_tokens", "usage", "budget",
            "failure_class", "tool_call", "error",
            "tool_call_count", "valid_tool_call_count", "arguments",
            "validation_errors", "expected",
        ):
            if key in attempts[0]:
                check[key] = attempts[0][key]
    return check
