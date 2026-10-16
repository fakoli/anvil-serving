"""Pure suite admission shared by benchmark submission and worker execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .jobs import BenchmarkJobError
from .profiles import load_profile
from .request_controls import resolve_job_request_controls
from .suite_runner import (
    _agentic_selection,
    _context_request_controls,
    _context_selection,
    _validate_case_limit,
)
from .swe import _validate_request_controls, validate_swe_selection
from .swe_images import validate_paired_image_ids


_RESERVED_PARAMETERS = {
    "thinking_mode", "reasoning_effort", "temperature", "top_p",
    "clear_thinking", "force_nonempty_content", "case_ids", "case_limit",
    "token_buckets", "positions", "repetitions", "output_headroom_tokens",
    "advertised_context", "recovery_result", "session_turns", "instance_ids",
    "paired_image_ids", "paired_image_repo_digests",
}
_SUITE_PARAMETERS = {
    "context": frozenset({
        "thinking_mode", "reasoning_effort", "temperature", "top_p", "clear_thinking",
        "case_ids", "case_limit", "token_buckets", "positions", "repetitions",
        "output_headroom_tokens", "advertised_context",
    }),
    "agentic": frozenset({
        "thinking_mode", "reasoning_effort", "temperature", "top_p", "force_nonempty_content",
        "case_ids", "case_limit", "recovery_result", "session_turns",
    }),
    "swe": frozenset({
        "thinking_mode", "reasoning_effort", "temperature", "top_p", "force_nonempty_content",
        "instance_ids", "paired_image_ids", "paired_image_repo_digests",
    }),
}


def _controls(parameters: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: parameters[key]
        for key in (
            "thinking_mode", "reasoning_effort", "temperature", "top_p",
            "force_nonempty_content",
        )
        if key in parameters
    }


def _validate_suite_parameters(suite: str, parameters: Mapping[str, Any]) -> None:
    unsupported = sorted((_RESERVED_PARAMETERS - _SUITE_PARAMETERS[suite]).intersection(parameters))
    if unsupported:
        raise BenchmarkJobError(
            "suite_incompatible_parameter",
            "benchmark parameters include a control or selection unsupported by this suite",
            {"suite": suite, "parameters": unsupported},
        )


def validate_submission_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate all suite selections before a job can be stored or executed.

    The returned summary is derived only from the immutable job specification and
    pinned profile. It deliberately performs no asset, endpoint, or filesystem
    work, so submit previews and confirmed submissions share the same admission.
    """
    suite_name = spec["suite"]
    profile = load_profile(spec["profile"])
    parameters = spec.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise BenchmarkJobError("bad_parameters", "benchmark parameters must be an object")
    _validate_suite_parameters(suite_name, parameters)
    suite = profile["suites"][suite_name]

    if suite_name == "context":
        controls = _context_request_controls(parameters)
        selection = _context_selection(suite, parameters)
        _validate_case_limit(parameters, suite="context")
        return {"profile": profile["name"], "request_controls": controls, "selection": selection}
    if suite_name == "agentic":
        controls = resolve_job_request_controls(_controls(parameters))
        selection = _agentic_selection(suite, parameters)
        _validate_case_limit(parameters, suite="agentic")
        return {"profile": profile["name"], "request_controls": controls, "selection": selection}
    if suite_name == "swe":
        selected = validate_swe_selection(profile, parameters.get("instance_ids"))
        controls = _validate_request_controls(_controls(parameters))
        paired = validate_paired_image_ids(parameters.get("paired_image_ids"), selected)
        return {
            "profile": profile["name"],
            "request_controls": controls,
            "selection": {"instance_ids": selected, "paired_image_ids": paired},
        }
    raise BenchmarkJobError("unknown_suite", "benchmark suite is unknown")
