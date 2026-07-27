"""Evaluation budgets, attempt classification, and aggregation."""

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
