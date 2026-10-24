"""Pure suite admission shared by benchmark submission and worker execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .artifacts import REQUIRED_MEASURED_IDENTITIES
from .jobs import BenchmarkJobError, validate_job_id
from .profiles import load_profile
from .request_controls import resolve_job_request_controls
from .suite_runner import (
    _agentic_selection,
    _context_request_controls,
    _context_selection,
    _validate_case_limit,
)
from .swe import SWE_PROMPT_CONTRACTS, _validate_request_controls, validate_swe_selection
from .swe_images import validate_paired_image_ids, validate_paired_image_repo_digests
from .swe_logs import validate_server_log_selector


_RESERVED_PARAMETERS = {
    "thinking_mode", "reasoning_effort", "temperature", "top_p",
    "clear_thinking", "force_nonempty_content", "case_ids", "case_limit",
    "token_buckets", "positions", "repetitions", "output_headroom_tokens",
    "advertised_context", "recovery_result", "session_turns", "instance_ids",
    "paired_image_ids", "paired_image_repo_digests",
    "server_log",
    "offline", "client_topology", "model_host_id", "identities",
    "operation", "source_run_id", "prompt_contract",
}
_COMMON_PARAMETERS = frozenset({"offline", "client_topology", "model_host_id", "identities"})
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
        "server_log",
        "operation", "source_run_id", "prompt_contract",
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
    unsupported = sorted(
        (_RESERVED_PARAMETERS - _COMMON_PARAMETERS - _SUITE_PARAMETERS[suite]).intersection(parameters)
    )
    if unsupported:
        raise BenchmarkJobError(
            "suite_incompatible_parameter",
            "benchmark parameters include a control or selection unsupported by this suite",
            {"suite": suite, "parameters": unsupported},
        )


def _validate_common_parameters(parameters: Mapping[str, Any]) -> None:
    """Validate parameter fields consumed outside an individual suite runner."""
    if "offline" in parameters and not isinstance(parameters["offline"], bool):
        raise BenchmarkJobError("bad_offline", "offline must be boolean")
    topology = parameters.get("client_topology", "isolated")
    if topology not in {"co-resident", "isolated"}:
        raise BenchmarkJobError(
            "bad_client_topology", "client_topology must be 'co-resident' or 'isolated'"
        )
    model_host_id = parameters.get("model_host_id")
    if model_host_id is not None and (
        not isinstance(model_host_id, str)
        or not model_host_id
        or model_host_id != model_host_id.strip()
    ):
        raise BenchmarkJobError(
            "bad_model_host_id", "model_host_id must be a non-empty trimmed string"
        )
    if "identities" in parameters:
        identities = parameters["identities"]
        if (
            not isinstance(identities, Mapping)
            or not identities
            or not set(identities).issubset(REQUIRED_MEASURED_IDENTITIES)
            or any(not isinstance(value, Mapping) or not value for value in identities.values())
        ):
            raise BenchmarkJobError(
                "bad_identities",
                "identities must contain supported non-empty identity objects",
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
    _validate_common_parameters(parameters)
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
        operation = parameters.get("operation", "run")
        if not isinstance(operation, str) or operation not in {"run", "regrade"}:
            raise BenchmarkJobError("bad_swe_operation", "SWE operation must be run or regrade")
        if operation == "regrade":
            source = validate_job_id(parameters.get("source_run_id"), field="source_run_id")
            if source == spec["run_id"]:
                raise BenchmarkJobError("bad_regrade_source", "regrading requires a new run ID")
            if set(parameters) - {"operation", "source_run_id"}:
                raise BenchmarkJobError(
                    "bad_regrade_parameters", "regrading uses the retained source plan without overrides"
                )
            return {"profile": profile["name"], "operation": operation, "source_run_id": source}
        if "source_run_id" in parameters:
            raise BenchmarkJobError("bad_regrade_source", "source_run_id requires operation regrade")
        prompt_contract = parameters.get("prompt_contract", "legacy/v1")
        if not isinstance(prompt_contract, str) or prompt_contract not in SWE_PROMPT_CONTRACTS:
            raise BenchmarkJobError(
                "bad_prompt_contract", "prompt_contract must be legacy/v1 or disclosed/v2"
            )
        selected = validate_swe_selection(profile, parameters.get("instance_ids"))
        controls = _validate_request_controls(_controls(parameters))
        paired = validate_paired_image_ids(parameters.get("paired_image_ids"), selected)
        repo_digests = validate_paired_image_repo_digests(
            parameters.get("paired_image_repo_digests"), selected
        ) if "paired_image_repo_digests" in parameters else None
        server_log = validate_server_log_selector(parameters["server_log"]) if "server_log" in parameters else None
        return {
            "profile": profile["name"],
            "request_controls": controls,
            "parameters": {"server_log": server_log, "prompt_contract": prompt_contract},
            "selection": {
                "instance_ids": selected,
                "paired_image_ids": paired,
                "paired_image_repo_digests": repo_digests,
                "server_log": server_log,
            },
        }
    raise BenchmarkJobError("unknown_suite", "benchmark suite is unknown")
