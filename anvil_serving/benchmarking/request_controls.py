"""Typed benchmark request controls shared by agentic and SWE execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .evaluation import normalize_sampling
from .jobs import BenchmarkJobError
from ..model_controls import REASONING_EFFORT_CHOICES


JOB_REQUEST_CONTROL_KEYS = frozenset({
    "thinking_mode", "reasoning_effort", "temperature", "top_p",
    "force_nonempty_content",
})


def resolve_job_request_controls(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the small benchmark control allowlist and form request kwargs."""
    controls = dict(value or {})
    if set(controls) - JOB_REQUEST_CONTROL_KEYS:
        raise BenchmarkJobError("bad_request_controls", "benchmark request controls are invalid")
    thinking_mode = controls.get("thinking_mode", "default")
    reasoning_effort = controls.get("reasoning_effort")
    if thinking_mode not in {"default", "enabled", "disabled"}:
        raise BenchmarkJobError(
            "bad_thinking_mode", "thinking_mode must be default, enabled, or disabled"
        )
    if reasoning_effort is not None and reasoning_effort not in REASONING_EFFORT_CHOICES:
        raise BenchmarkJobError(
            "bad_reasoning_effort", "reasoning_effort is not supported by the harness"
        )
    if reasoning_effort is not None and thinking_mode != "default":
        raise BenchmarkJobError(
            "conflicting_reasoning_controls",
            "reasoning_effort and thinking_mode cannot both be explicit",
        )
    force_nonempty_content = controls.get("force_nonempty_content")
    if force_nonempty_content is not None and not isinstance(force_nonempty_content, bool):
        raise BenchmarkJobError(
            "bad_force_nonempty_content", "force_nonempty_content must be boolean"
        )
    if force_nonempty_content is not None and thinking_mode != "enabled":
        raise BenchmarkJobError(
            "force_nonempty_content_requires_enabled_thinking",
            "force_nonempty_content requires thinking_mode=enabled",
        )
    chat_template_kwargs = (
        {"enable_thinking": True}
        if thinking_mode == "enabled"
        else {"enable_thinking": False} if thinking_mode == "disabled" else None
    )
    if force_nonempty_content is not None:
        chat_template_kwargs = {
            "enable_thinking": True,
            "force_nonempty_content": force_nonempty_content,
        }
    result = {
        "thinking_mode": thinking_mode,
        "reasoning_effort": reasoning_effort,
        "chat_template_kwargs": chat_template_kwargs,
    }
    if force_nonempty_content is not None:
        result["force_nonempty_content"] = force_nonempty_content
    if "temperature" in controls or "top_p" in controls:
        requested_temperature = controls.get("temperature")
        requested_top_p = controls.get("top_p")
        try:
            result["sampling"] = normalize_sampling({
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
        except ValueError as exc:
            raise BenchmarkJobError("bad_request_controls", "benchmark sampler controls are invalid") from exc
    return result
