"""anvil-serving MCP control plane.

This module exposes a small stdio JSON-RPC server for agent-facing operations
around ADR-0013: inspect the router/serves/host state, preview/apply OpenClaw
harness sync, restart the OpenClaw gateway, and run bounded probes. It is a
control plane only; model traffic still flows through ``anvil-serving router run``.

Runtime dependencies stay stdlib-only. Commands are argv lists, never shell
strings. Mutating tools require explicit ``confirm: true`` and keep dry-run
paths available.
"""
# Re-exported compatibility names and subprocess are intentionally module globals.
# ruff: noqa: F401
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Dict, Iterable, Optional

from . import __version__
from .commands import COMMAND_TREE
from .control_plane.mcp.arguments import (
    MAX_CONTEXT_STRING as _MAX_CONTEXT_STRING,
    arg_bool as _arg_bool,
    bounded_float_arg as _bounded_float_arg,
    bounded_int_arg as _bounded_int_arg,
    bounded_integer_schema as _bounded_integer_schema,
    bounded_tool_schema as _bounded_tool_schema,
    int_arg as _int_arg,
    schema as _schema,
    str_arg as _str_arg,
    str_list_arg as _str_list_arg,
    target_context as _build_target_context,
    validate_tool_arguments as _validate_arguments,
)
from .control_plane.mcp.catalog import (
    build_catalog as _build_catalog,
    call_tool as _call_catalog_tool,
    list_tools as _list_catalog_tools,
    operation_declarations as _catalog_operation_declarations,
)
from .control_plane.mcp.controller_client import (
    NoRedirectHandler as _NoRedirectHandler,
    controller_auth_headers,
    http_error_details as _http_error_details,
    remote_controller_request,
    resolve_controller_token,
    urlopen_no_proxy_no_redirect as _urlopen_no_proxy_no_redirect,
)
from .control_plane.mcp.errors import ToolError
from .control_plane.mcp.errors import fail as _failure_envelope
from .control_plane.mcp.errors import ok as _ok
from .control_plane.mcp.protocol import (
    handle_proxy_request as _handle_proxy_protocol_request,
    handle_request as _handle_protocol_request,
    jsonrpc_error as _jsonrpc_error,
    tool_result as _tool_result,
)
from .control_plane.mcp.runtime import (
    capture as _capture,
    command_preview as _command_preview,
    read_spooled_text as _read_spooled_text,
    run_argv as _run_argv,
    run_argv_spooled as _run_argv_spooled,
)
from .control_plane.mcp.security import (
    ENV_NAME_RE as _ENV_NAME_RE,
    PROBE_API_KEY_ENVS as _PROBE_API_KEY_ENVS,
    redact_error_details as _redact_error_details,
    redact_log_text as _redact_log_text,
    redact_secret as _redact_secret,
    redact_text as _redact_text,
    resolve_benchmark_artifact_path as _resolve_benchmark_artifact_path,
    safe_controller_url as _safe_controller_url,
    safe_probe_url as _safe_probe_url,
)
from .control_plane.mcp.stdio import (
    build_main_parser as _build_main_parser,
    main as _stdio_main,
    parse_main_args as _parse_main_args,
    serve_stdio as _serve_stdio_loop,
)
from .operator_output import CONTEXT_FIELDS, context_from_plan, redact


SERVER_INFO = {"name": "anvil-serving", "version": __version__}
PROTOCOL_VERSION = "2024-11-05"
_WORKFLOW_SCHEMA_VERSION = "operator-workflow/v1"
_WORKFLOW_GATE_STATES = {"not_required", "confirm_required", "human_required", "blocked"}
_WORKFLOW_SOURCE_CLASSES = {"mcp", "controller", "cli", "manual", "fixture"}
_WORKFLOW_RECOMMENDATIONS = {"promote", "do_not_promote", "needs_more_data", "blocked"}
_WORKFLOW_VOICE_ARTIFACT_KINDS = {"voice-benchmark", "voice-sidecar-render"}
_WORKFLOW_VOICE_CONTEXT_RE = re.compile(r"(^|[^a-z0-9])(voice|stt|tts|realtime)([^a-z0-9]|$)", re.I)
_WORKFLOW_SEMANTIC_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$", re.I)
_WORKFLOW_DRIVE_RELATIVE_RE = re.compile(r"^[a-z]:", re.I)
_WORKFLOW_PATH_FIELD_RE = re.compile(r"(^|_)(path|file|filename|dir|directory|root|roots)(_|$)", re.I)
_WORKFLOW_DOTTED_DOMAIN_SEGMENTS = frozenset({
    "benchmark", "pipeline", "realtime", "sidecar", "stt", "tts", "voice",
})
_WORKFLOW_VERSION_SEGMENT_RE = re.compile(r"^v[0-9]+$", re.I)


def _fail(code: str, message: str, details: Optional[dict] = None) -> dict:
    return _failure_envelope(
        code,
        message,
        details,
        redact_text=_redact_text,
        redact_details=_redact_error_details,
    )


def _probe_api_key_env(args: dict) -> str:
    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; set api_key_env to the credential env var name",
        )
    api_key_env = _str_arg(args, "api_key_env", "")
    if not api_key_env:
        return ""
    if not _ENV_NAME_RE.fullmatch(api_key_env):
        raise ToolError("bad_api_key_env", "api_key_env must name an ENV VAR matching ^[A-Z][A-Z0-9_]*$")
    if api_key_env not in _PROBE_API_KEY_ENVS:
        raise ToolError(
            "unsafe_api_key_env",
            "api_key_env must be ANVIL_ROUTER_TOKEN for MCP probe tools",
            {"allowed_api_key_envs": sorted(_PROBE_API_KEY_ENVS)},
        )
    return api_key_env


def _benchmark_key_metrics(summary: dict[str, Any]) -> dict[str, Any]:
    metrics = summary.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    keys = (
        "ttft_p50_ms",
        "ttft_p95_ms",
        "e2e_p50_ms",
        "e2e_p95_ms",
        "throughput_tok_s",
        "output_tokens",
        "prefix_cache_hit_avg",
    )
    return {
        "requests": summary.get("requests"),
        "completed": summary.get("completed"),
        "concurrency": summary.get("concurrency"),
        "context_tokens": summary.get("context_tokens"),
        "max_context_tokens": summary.get("max_context_tokens"),
        "max_tokens": summary.get("max_tokens"),
        **{key: metrics.get(key) for key in keys},
    }


def _metric_delta(local_value: Any, external_value: Any) -> dict[str, Any]:
    def as_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    local_num = as_float(local_value)
    external_num = as_float(external_value)
    delta_abs = None
    delta_pct = None
    if local_num is not None and external_num not in (None, 0.0):
        delta_abs = local_num - external_num
        delta_pct = (delta_abs / external_num) * 100.0
    return {
        "local": local_num,
        "external": external_num,
        "delta_abs": delta_abs,
        "delta_pct": delta_pct,
    }


def _read_benchmark_artifact(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        raise ToolError("artifact_not_written", "benchmark completed but JSON artifact was not written", {"artifact_path": path})
    try:
        with open(path, "r", encoding="utf-8") as f:
            summary = json.load(f)
    except OSError as exc:
        raise ToolError("artifact_read_failed", str(exc), {"artifact_path": path})
    except ValueError as exc:
        raise ToolError("bad_benchmark_artifact", "benchmark artifact is not valid JSON", {"artifact_path": path, "error": str(exc)})
    if not isinstance(summary, dict):
        raise ToolError("bad_benchmark_artifact", "benchmark artifact must be a JSON object", {"artifact_path": path})
    return summary


def _workflow_error(errors: list[dict[str, Any]], field: str, message: str, details: Optional[dict] = None) -> None:
    errors.append({"field": field, "message": message, "details": details or {}})


def _workflow_is_dotted_domain_symbol(value: str) -> bool:
    parts = value.split(".")
    if len(parts) < 2 or not all(_WORKFLOW_SEMANTIC_IDENTIFIER_RE.fullmatch(part) for part in parts):
        return False
    if not _WORKFLOW_VOICE_CONTEXT_RE.search(parts[0]):
        return False
    return all(
        part.casefold() in _WORKFLOW_DOTTED_DOMAIN_SEGMENTS
        or _WORKFLOW_VERSION_SEGMENT_RE.fullmatch(part)
        for part in parts[1:]
    )


def _workflow_symbol_mentions_voice(value: Any) -> bool:
    if isinstance(value, str):
        symbol = value.strip("'\"()[]{}<>,;!?").rstrip(".")
        if not symbol or _WORKFLOW_DRIVE_RELATIVE_RE.match(symbol) or "\\" in symbol:
            return False
        if "." in symbol and not _workflow_is_dotted_domain_symbol(symbol):
            return False
        if "/" in symbol:
            parts = symbol.split("/")
            # Mixed slash strings are paths. Only an all-domain chain is a
            # semantic label, for example ``voice/stt/realtime``.
            if len(parts) < 2 or not all(_WORKFLOW_VOICE_CONTEXT_RE.fullmatch(part) for part in parts):
                return False
        elif not _WORKFLOW_SEMANTIC_IDENTIFIER_RE.fullmatch(symbol):
            return False
        return bool(_WORKFLOW_VOICE_CONTEXT_RE.search(symbol))
    if isinstance(value, list):
        return any(_workflow_symbol_mentions_voice(item) for item in value)
    if isinstance(value, dict):
        for key, item in value.items():
            if _workflow_symbol_mentions_voice(key) or _workflow_symbol_mentions_voice(item):
                return True
    return False


def _workflow_request_mentions_voice(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    for raw_token in value.split():
        if _workflow_symbol_mentions_voice(raw_token):
            return True
    return False


def _workflow_is_path_field(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value).replace("-", "_")
    return bool(_WORKFLOW_PATH_FIELD_RE.search(normalized))


def _workflow_targets_mention_voice(value: Any) -> bool:
    if isinstance(value, list):
        return any(_workflow_targets_mention_voice(item) for item in value)
    if isinstance(value, dict):
        for key, item in value.items():
            if _workflow_is_path_field(key):
                continue
            if _workflow_symbol_mentions_voice(key):
                return True
            if _workflow_targets_mention_voice(item):
                return True
        return False
    return _workflow_symbol_mentions_voice(value)


def _workflow_artifacts_declare_voice(artifacts: list[Any]) -> bool:
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        for field in ("kind", "evidence_scope"):
            value = artifact.get(field)
            if isinstance(value, str) and _WORKFLOW_VOICE_CONTEXT_RE.search(value):
                return True
    return False


def _workflow_voice_context(packet: dict[str, Any], artifacts: list[Any]) -> bool:
    """Infer voice intent from semantic workflow declarations only.

    Tool results and filesystem-bearing values are deliberately excluded: fields
    such as ``data.allowed_roots`` and artifact paths describe execution context,
    not the workflow's evidence domain.
    """
    tools_used = packet.get("tools_used")
    tool_names = [
        tool.get("name")
        for tool in tools_used
        if isinstance(tool, dict)
    ] if isinstance(tools_used, list) else []
    return (
        _workflow_request_mentions_voice(packet.get("request"))
        or _workflow_targets_mention_voice(packet.get("targets"))
        or _workflow_symbol_mentions_voice(tool_names)
        or _workflow_artifacts_declare_voice(artifacts)
    )


def _normalize_workflow_artifacts(packet: dict[str, Any], errors: list[dict[str, Any]]) -> list[Any]:
    artifacts = packet.get("artifacts")
    if not isinstance(artifacts, list):
        _workflow_error(errors, "artifacts", "artifacts must be an array")
        return []
    voice_context = _workflow_voice_context(packet, artifacts)
    normalized = []
    for index, artifact in enumerate(artifacts):
        field = "artifacts[%d]" % index
        if isinstance(artifact, str):
            raw_path = artifact
            if voice_context:
                _workflow_error(
                    errors,
                    field,
                    "voice workflow artifacts must be objects with kind, evidence_scope, and promotion_quality_evidence",
                )
                continue
            item: Any = artifact
        elif isinstance(artifact, dict):
            raw_path = artifact.get("path")
            item = dict(artifact)
        else:
            _workflow_error(errors, field, "artifact must be a string path or an object with path")
            continue
        if not isinstance(raw_path, str) or not raw_path:
            _workflow_error(errors, field + ".path", "artifact path must be a non-empty string")
            continue
        try:
            normalized_path, _ = _resolve_benchmark_artifact_path(raw_path)
        except ToolError as exc:
            _workflow_error(errors, field + ".path", exc.message, {"code": exc.code, **exc.details})
            continue
        if isinstance(item, dict):
            kind = item.get("kind")
            if kind is not None and not isinstance(kind, str):
                _workflow_error(errors, field + ".kind", "artifact kind must be a string")
            evidence_scope = item.get("evidence_scope")
            if evidence_scope is not None and not isinstance(evidence_scope, str):
                _workflow_error(errors, field + ".evidence_scope", "artifact evidence_scope must be a string")
            is_voice_artifact = isinstance(kind, str) and kind in _WORKFLOW_VOICE_ARTIFACT_KINDS
            if voice_context and not is_voice_artifact:
                _workflow_error(
                    errors,
                    field + ".kind",
                    "voice workflow artifacts must declare a voice artifact kind",
                    {"allowed": sorted(_WORKFLOW_VOICE_ARTIFACT_KINDS)},
                )
            if voice_context or is_voice_artifact:
                if item.get("evidence_scope") != "voice-pipeline":
                    _workflow_error(
                        errors,
                        field + ".evidence_scope",
                        "voice artifacts must declare evidence_scope='voice-pipeline'",
                    )
                if item.get("promotion_quality_evidence") is not False:
                    _workflow_error(
                        errors,
                        field + ".promotion_quality_evidence",
                        "voice artifacts are not LLM serve qualification evidence",
                    )
            item["path"] = normalized_path
            normalized.append(item)
        else:
            normalized.append(normalized_path)
    return normalized


def _is_approved_promote_tool(tool: dict[str, Any]) -> bool:
    if tool.get("name") != "serves_promote":
        return False
    if tool.get("ok") is not True or tool.get("dry_run") is not False or tool.get("confirmed") is not True:
        return False
    if tool.get("error") is not None:
        return False

    candidates = [tool]
    for key in ("result", "data", "output"):
        nested = tool.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for candidate in candidates:
        if (
            candidate.get("human_approved") is True
            and candidate.get("applied") is True
            and candidate.get("returncode") == 0
        ):
            return True
    return False


def validate_workflow_packet(packet: Any) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if not isinstance(packet, dict):
        return {"valid": False, "errors": [{"field": "packet", "message": "packet must be an object", "details": {}}]}

    normalized = dict(packet)
    required = (
        "schema_version",
        "request",
        "gate_state",
        "targets",
        "tools_used",
        "artifacts",
        "advisory_priors",
        "recommendation",
        "human_gate_required",
        "promoted",
    )
    for field in required:
        if field not in packet:
            _workflow_error(errors, field, "missing required field")

    if packet.get("schema_version") != _WORKFLOW_SCHEMA_VERSION:
        _workflow_error(errors, "schema_version", "schema_version must be %r" % _WORKFLOW_SCHEMA_VERSION)
    if not isinstance(packet.get("request"), str) or not packet.get("request", "").strip():
        _workflow_error(errors, "request", "request must be a non-empty string")
    if packet.get("gate_state") not in _WORKFLOW_GATE_STATES:
        _workflow_error(errors, "gate_state", "invalid gate_state", {"allowed": sorted(_WORKFLOW_GATE_STATES)})
    if packet.get("recommendation") not in _WORKFLOW_RECOMMENDATIONS:
        _workflow_error(errors, "recommendation", "invalid recommendation", {"allowed": sorted(_WORKFLOW_RECOMMENDATIONS)})
    if not isinstance(packet.get("human_gate_required"), bool):
        _workflow_error(errors, "human_gate_required", "human_gate_required must be a boolean")
    if not isinstance(packet.get("promoted"), bool):
        _workflow_error(errors, "promoted", "promoted must be a boolean")

    targets = packet.get("targets")
    if not isinstance(targets, dict):
        _workflow_error(errors, "targets", "targets must be an object")
    else:
        for key in targets:
            if not isinstance(key, str):
                _workflow_error(errors, "targets", "target keys must be strings")
                break

    tools_used = packet.get("tools_used")
    normalized_tools = []
    if not isinstance(tools_used, list):
        _workflow_error(errors, "tools_used", "tools_used must be an array")
        tools_used = []
    for index, tool in enumerate(tools_used):
        field = "tools_used[%d]" % index
        if not isinstance(tool, dict):
            _workflow_error(errors, field, "tool entry must be an object")
            continue
        normalized_tools.append(dict(tool))
        if not isinstance(tool.get("name"), str) or not tool.get("name"):
            _workflow_error(errors, field + ".name", "tool name must be a non-empty string")
        if tool.get("source_class") not in _WORKFLOW_SOURCE_CLASSES:
            _workflow_error(errors, field + ".source_class", "invalid source_class", {"allowed": sorted(_WORKFLOW_SOURCE_CLASSES)})
        for bool_field in ("ok", "dry_run", "confirmed"):
            if not isinstance(tool.get(bool_field), bool):
                _workflow_error(errors, field + "." + bool_field, "%s must be a boolean" % bool_field)
        if "target" not in tool:
            _workflow_error(errors, field + ".target", "target field is required")
        if "error" not in tool:
            _workflow_error(errors, field + ".error", "error field is required")
    normalized["tools_used"] = normalized_tools

    normalized["artifacts"] = _normalize_workflow_artifacts(packet, errors)

    advisory_priors = packet.get("advisory_priors")
    if not isinstance(advisory_priors, list):
        _workflow_error(errors, "advisory_priors", "advisory_priors must be an array")
    else:
        for index, prior in enumerate(advisory_priors):
            field = "advisory_priors[%d]" % index
            if not isinstance(prior, dict):
                _workflow_error(errors, field, "advisory prior must be an object")
                continue
            if prior.get("advisory_only") is not True:
                _workflow_error(errors, field + ".advisory_only", "external priors must declare advisory_only=true")
            if prior.get("promotion_quality_evidence") is not False:
                _workflow_error(
                    errors,
                    field + ".promotion_quality_evidence",
                    "external priors must declare promotion_quality_evidence=false",
                )

    has_approved_promote = any(_is_approved_promote_tool(tool) for tool in tools_used if isinstance(tool, dict))
    if packet.get("promoted") is True and not has_approved_promote:
        _workflow_error(
            errors,
            "promoted",
            "promoted=true requires a human-approved serves_promote tool result",
        )
    if packet.get("recommendation") == "promote" and not has_approved_promote:
        if packet.get("human_gate_required") is not True:
            _workflow_error(
                errors,
                "human_gate_required",
                "recommendation=promote requires human_gate_required=true until a successful human-approved promotion result is present",
            )
        if packet.get("gate_state") != "human_required":
            _workflow_error(
                errors,
                "gate_state",
                "recommendation=promote requires gate_state='human_required' until a successful human-approved promotion result is present",
            )

    return {"valid": not errors, "errors": errors, "normalized_packet": normalized}


def _router_manage_cli_argv(action: str, *, container: str = "", compose: str = "",
                            service: str = "", env_file: str = "", dry_run: bool = False,
                            no_verify: bool = False, recreate: bool = False,
                            confirm: bool = False) -> list[str]:
    argv = [sys.executable, "-m", "anvil_serving.cli", "router", action]
    if container:
        argv += ["--container", container]
    if compose:
        argv += ["--compose", compose]
    if service:
        argv += ["--service", service]
    if env_file:
        argv += ["--env-file", env_file]
    if dry_run:
        argv.append("--dry-run")
    if no_verify:
        argv.append("--no-verify")
    if recreate:
        argv.append("--recreate")
    if confirm:
        argv.append("--confirm")
    return argv


def tool_router_status(args: dict) -> dict:
    from . import router_manage

    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    return _ok(router_manage.status_summary(container))


def tool_router_logs(args: dict) -> dict:
    from . import router_manage

    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    follow = _arg_bool(args.get("follow"), False, name="follow")
    if follow:
        raise ToolError("follow_not_allowed", "router_logs rejects unbounded follow mode; use a bounded tail")
    tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
    max_output_bytes = _bounded_int_arg(args, "max_output_bytes", 65536, min_value=1024, max_value=1048576)
    since = _str_arg(args, "since", "")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 60, min_value=1, max_value=600)
    argv = [sys.executable, "-m", "anvil_serving.cli", "router", "logs",
            "--container", container, "--tail", str(tail)]
    if since:
        argv += ["--since", since]
    result = _run_argv_spooled(
        argv,
        timeout=timeout_seconds,
        max_output_bytes=max_output_bytes,
        redactor=_redact_log_text,
    )
    return _ok({
        "bounded": True,
        "tail": tail,
        "since": since or None,
        "max_output_bytes": max_output_bytes,
        **result,
    })


def tool_router_manage(args: dict) -> dict:
    from . import router_manage

    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "restart", "reload"}:
        raise ToolError("bad_action", "action must be one of: up, down, restart, reload", {"action": action})
    container = _str_arg(args, "container", router_manage.DEFAULT_CONTAINER)
    compose_arg = _str_arg(args, "compose", "")
    service = _str_arg(args, "service", router_manage.DEFAULT_SERVICE)
    env_file_arg = _str_arg(args, "env_file", "")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    no_verify = _arg_bool(args.get("no_verify"), False, name="no_verify")
    recreate = _arg_bool(args.get("recreate"), False, name="recreate")
    if recreate and action != "up":
        raise ToolError("bad_argument", "'recreate' is only valid with action='up'")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 300, min_value=1, max_value=7200)
    preview = dry_run or not confirm
    lifecycle = router_manage.lifecycle_plan(
        action,
        compose=compose_arg or None,
        service=service,
        env_file=env_file_arg or None,
        container=container,
        recreate=recreate,
    )
    argv = _router_manage_cli_argv(
        action,
        container=container if action in {"restart", "reload"} else "",
        compose=lifecycle["compose"] or "",
        service=lifecycle["service"] or "",
        env_file=lifecycle["env_file"] or "",
        dry_run=preview,
        no_verify=no_verify if action in {"restart", "reload"} else False,
        recreate=recreate,
        confirm=confirm,
    )
    target = {
        "action": action,
        "container": container,
        "compose": lifecycle["compose"],
        "service": lifecycle["service"],
        "env_file": lifecycle["env_file"],
        "recreate": recreate,
        "timeout_seconds": timeout_seconds,
        "no_verify": no_verify if action in {"restart", "reload"} else False,
    }
    if preview:
        return _ok({
            "applied": False,
            "dry_run": True,
            "target": target,
            "command": argv,
            "lifecycle_command": lifecycle["command"],
        })
    result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
    return _ok({
        "applied": True,
        "dry_run": False,
        "target": target,
        "lifecycle_command": lifecycle["command"],
        **result,
    })


def tool_router_transition(args: dict) -> dict:
    from . import router_manage

    action = _str_arg(args, "action", required=True)
    tier_id = _str_arg(args, "tier", "")
    router_url = _str_arg(args, "router_url", "")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout = args.get("timeout")
    if timeout is not None:
        timeout = _bounded_int_arg(args, "timeout", 60, min_value=1, max_value=3600)
    try:
        result = router_manage.transition_request(
            action,
            tier_id=tier_id or None,
            timeout=timeout,
            router_url=router_url or None,
            confirm=confirm,
            dry_run=dry_run,
        )
    except ValueError as exc:
        raise ToolError("transition_failed", str(exc))
    return _ok(result)


def _decision_records_from_path(path: str, *, max_input_bytes: int) -> list[dict]:
    if not os.path.isfile(path):
        raise ToolError("decision_log_not_found", "decision summary source not found", {"path": path})
    if os.path.getsize(path) > max_input_bytes:
        raise ToolError(
            "decision_log_too_large",
            "decision summary source exceeds max_input_bytes",
            {"path": path, "max_input_bytes": max_input_bytes},
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    if not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        records = []
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as exc:
                raise ToolError("bad_decision_log", "bad JSONL decision record", {"path": path, "line": lineno, "error": str(exc)})
            if isinstance(item, dict):
                records.append(item)
        return records
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        if isinstance(parsed.get("records"), list):
            return [item for item in parsed["records"] if isinstance(item, dict)]
        return [parsed]
    raise ToolError("bad_decision_log", "decision summary source must be JSON array, JSONL, or object with records[]")


def tool_decision_summary(args: dict) -> dict:
    from .router.decision_log import summarize_decisions

    limit = _bounded_int_arg(args, "limit", 20, min_value=1, max_value=500)
    max_input_bytes = _bounded_int_arg(args, "max_input_bytes", 1048576, min_value=1024, max_value=10485760)
    timeout = _bounded_int_arg(args, "timeout_seconds", 5, min_value=1, max_value=60)
    base_url = _str_arg(args, "base_url", "http://127.0.0.1:8000/v1")
    api_key_env = _probe_api_key_env(args)
    path = _str_arg(args, "path", "")
    records_arg = args.get("records", [])
    if records_arg is None:
        records_arg = []
    if not isinstance(records_arg, list) or not all(isinstance(item, dict) for item in records_arg):
        raise ToolError("bad_argument", "'records' must be an array of objects")
    records = list(records_arg)
    source = "inline"
    if path:
        records = _decision_records_from_path(path, max_input_bytes=max_input_bytes)
        source = "path"
    if not path and not records:
        base_url = _safe_probe_url(base_url)
        token = ""
        headers = {"Accept": "application/json"}
        if api_key_env:
            token = os.environ.get(api_key_env)
            if token:
                headers.update(controller_auth_headers(token))
        req = urllib.request.Request(_decisions_url(base_url, limit), headers=headers, method="GET")
        try:
            with _urlopen_no_proxy_no_redirect(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                parsed = json.loads(raw or "{}")
        except urllib.error.HTTPError as exc:
            details, _ = _http_error_details(exc, token)
            raise ToolError("decision_summary_http_error", "decision summary returned HTTP %s" % exc.code, details)
        except Exception as exc:
            raise ToolError("decision_summary_failed", _redact_secret(str(exc), token), {"base_url": base_url})
        if not isinstance(parsed, dict):
            raise ToolError("bad_decision_summary", "decision summary response must be a JSON object")
        parsed = _redact_secret(parsed, token)
        parsed["source"] = "router"
        parsed["base_url"] = base_url
        return _ok(parsed)
    summary = summarize_decisions(records, limit=limit)
    summary["source"] = source
    summary["path"] = path or None
    return _ok(summary)


def tool_serves_status(args: dict) -> dict:
    from . import serves as serves_mod

    manifest_arg = _str_arg(args, "manifest", "")
    manifest = serves_mod.resolve_manifest_path(manifest_arg or None)
    names = args.get("names", [])
    if names is None:
        names = []
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise ToolError("bad_argument", "'names' must be an array of strings")
    try:
        serves = serves_mod.load_manifest(manifest)
    except FileNotFoundError:
        raise ToolError("manifest_not_found", "serves manifest not found", {"manifest": manifest})
    except Exception as exc:
        raise ToolError("bad_manifest", "could not load serves manifest", {"manifest": manifest, "error": str(exc)})
    return _ok(serves_mod.status_summary(serves, names))


def tool_reservation_status(args: dict) -> dict:
    from . import serves as serves_mod

    manifest_arg = _str_arg(args, "manifest", "")
    manifest = serves_mod.resolve_manifest_path(manifest_arg or None)
    serves = _load_serves_for_tool(manifest)
    return _ok(serves_mod.reservation_summary(serves))


def _load_serves_for_tool(manifest: str):
    from . import serves as serves_mod

    try:
        return serves_mod.load_manifest(manifest)
    except FileNotFoundError:
        raise ToolError("manifest_not_found", "serves manifest not found", {"manifest": manifest})
    except Exception as exc:
        raise ToolError("bad_manifest", "could not load serves manifest", {"manifest": manifest, "error": str(exc)})


def _serves_cli_argv(action: str, manifest: str, names: list[str], *, dry_run: bool = False,
                     recreate: bool = False, compose: str = "", tail: Optional[int] = None,
                     since: str = "") -> list[str]:
    argv = [sys.executable, "-m", "anvil_serving.cli", "serves", action]
    if compose:
        argv += ["--compose", compose]
    if dry_run:
        argv.append("--dry-run")
    elif action in {"up", "down", "rm", "adopt"}:
        # MCP's confirm=true + dry_run=false is the operator approval for this
        # exact declared action. Forward it to the resource-owner CLI so its
        # shared confirmation gate authorizes the lifecycle operation and all
        # declared postconditions (including lifecycle cache reclaim).
        argv.append("--confirm")
    if recreate:
        argv.append("--recreate")
    if tail is not None:
        argv += ["--tail", str(tail)]
    if since:
        argv += ["--since", since]
    if action in ("rm", "adopt") and not dry_run:
        # The serves module additionally gates these irreversible actions behind an interactive
        # [y/N] prompt. This subprocess has no TTY (stdin is the JSON-RPC
        # pipe), and the MCP triple gate (confirm=true, dry_run=false) IS the
        # operator's consent — pass it through, or the child EOFs to "No" and
        # every confirmed rm/adopt silently aborts.
        argv.append("--yes")
    if not compose:
        argv += ["--manifest", manifest]
    argv += names
    return argv


def _dedupe_serves(serves_list: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for serve in serves_list:
        key = serve.get("name"), serve.get("container")
        if key in seen:
            continue
        seen.add(key)
        out.append(serve)
    return out


def _resolve_manifest_serves(serves_mod, manifest_serves: list[dict], names: list[str], *,
                             caller: str = "serves_manage") -> list[dict]:
    if not names:
        raise ToolError("missing_argument", "%s requires explicit manifest serve names" % caller)
    selected = []
    for name in names:
        matched = serves_mod._select(manifest_serves, [name])
        if not matched:
            raise ToolError("no_matching_serve", "no serve in manifest matches %r" % name, {"name": name})
        if len(matched) > 1:
            raise ToolError(
                "ambiguous_serve",
                "%r matches multiple serves; use an exact container name" % name,
                {"name": name, "matches": [item.get("name") for item in matched]},
            )
        selected.append(matched[0])
    return _dedupe_serves(selected)


def _serves_manage_plan(action: str, manifest_serves: list[dict], names: list[str], *,
                        compose: str = "", recreate: bool = False,
                        allow_literal: bool = False) -> tuple[list[dict], dict]:
    from . import serves as serves_mod

    if compose:
        if not names:
            raise ToolError("missing_argument", "compose up through MCP requires explicit service names")
        command = ["docker", "compose", "-f", compose, "up", "-d", *names]
        return [], {
            "mode": "compose",
            "commands": [{"kind": "compose_up", "argv": command}],
            "services": names,
        }

    if action == "rm":
        commands = []
        targets = []
        literal_names = []
        for name in names:
            matched = serves_mod._select(manifest_serves, [name])
            if len(matched) > 1:
                raise ToolError(
                    "ambiguous_serve",
                    "%r matches multiple serves; use an exact container name" % name,
                    {"name": name, "matches": [item.get("name") for item in matched]},
                )
            if matched:
                target = matched[0]
                targets.append(target)
                container = target["container"]
                target_name = target.get("name")
            else:
                if not allow_literal:
                    literal_names.append(name)
                    continue
                container = name
                target_name = name
            commands.append({"kind": "docker_rm", "target": target_name, "argv": ["docker", "rm", "-f", container]})
        if literal_names:
            raise ToolError(
                "literal_container_requires_allow",
                "rm of a container not recognized in the manifest requires allow_literal=true",
                {"literal_names": literal_names},
            )
        targets = _dedupe_serves(targets)
        return targets, {
            "mode": "manifest",
            "targets": [
                {
                    "name": item.get("name"),
                    "container": item.get("container"),
                    "manifest_up": item.get("up"),
                }
                for item in targets
            ],
            "commands": commands,
        }

    targets = _resolve_manifest_serves(serves_mod, manifest_serves, names)
    plan = {
        "mode": "manifest",
        "targets": [
            {
                "name": item.get("name"),
                "container": item.get("container"),
                "manifest_up": item.get("up"),
            }
            for item in targets
        ],
        "commands": [],
    }
    if action == "down":
        plan["commands"] = [
            {"kind": "docker_stop", "target": item.get("name"), "argv": ["docker", "stop", item["container"]]}
            for item in targets
        ]
    elif action == "adopt":
        for item in targets:
            plan["commands"].append({
                "kind": "docker_rm_before_adopt",
                "target": item.get("name"),
                "argv": ["docker", "rm", "-f", item["container"]],
            })
            if item.get("up"):
                plan["commands"].append({
                    "kind": "manifest_up_after_adopt",
                    "target": item.get("name"),
                    "argv": item["up"],
                })
    elif action == "up":
        for item in targets:
            if recreate:
                plan["commands"].append({
                    "kind": "docker_rm_before_recreate",
                    "target": item.get("name"),
                    "argv": ["docker", "rm", "-f", item["container"]],
                })
            if item.get("up"):
                plan["commands"].append({
                    "kind": "manifest_up_when_absent_or_compose_reconcile",
                    "target": item.get("name"),
                    "argv": item["up"],
                })
            plan["commands"].extend([
                {
                    "kind": "docker_start_when_existing_script_serve_stopped",
                    "target": item.get("name"),
                    "argv": ["docker", "start", item["container"]],
                },
                {
                    "kind": "docker_unpause_when_paused",
                    "target": item.get("name"),
                    "argv": ["docker", "unpause", item["container"]],
                },
            ])
    return targets, plan


def tool_serves_manage(args: dict) -> dict:
    from . import serves as serves_mod

    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "rm", "adopt"}:
        raise ToolError("bad_action", "action must be one of: up, down, rm, adopt", {"action": action})
    manifest_arg = _str_arg(args, "manifest", "")
    manifest = serves_mod.resolve_manifest_path(manifest_arg or None)
    names = _str_list_arg(args, "names")
    compose = _str_arg(args, "compose", "")
    recreate = _arg_bool(args.get("recreate"), False, name="recreate")
    allow_literal = _arg_bool(args.get("allow_literal"), False, name="allow_literal")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 300, min_value=1, max_value=7200)

    if compose and action != "up":
        raise ToolError("bad_argument", "'compose' is only valid with action='up'")
    if compose and recreate:
        raise ToolError("bad_argument", "'recreate' has no meaning with compose up")
    if action == "rm" and not names:
        raise ToolError("missing_argument", "rm requires at least one name")

    manifest_serves = []
    if not compose:
        manifest_serves = _load_serves_for_tool(manifest)
    _, plan = _serves_manage_plan(
        action,
        manifest_serves,
        names,
        compose=compose,
        recreate=recreate,
        allow_literal=allow_literal,
    )

    preview = dry_run or not confirm
    argv = _serves_cli_argv(action, manifest, names, dry_run=preview, recreate=recreate, compose=compose)
    target = {
        "action": action,
        "manifest": None if compose else manifest,
        "names": names,
        "compose": compose or None,
        "recreate": recreate,
        "allow_literal": allow_literal,
        "timeout_seconds": timeout_seconds,
    }
    if preview:
        return _ok({"applied": False, "dry_run": True, "target": target, "command": argv, "plan": plan})
    result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
    return _ok({"applied": True, "dry_run": False, "target": target, "plan": plan, **result})


def tool_serves_promote(args: dict) -> dict:
    from . import serves as serves_mod

    manifest_arg = _str_arg(args, "manifest", "")
    manifest = serves_mod.resolve_manifest_path(manifest_arg or None)
    plan_name = _str_arg(args, "plan", required=True)
    rollback = _arg_bool(args.get("rollback"), False, name="rollback")
    resume = _arg_bool(args.get("resume"), False, name="resume")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    human_approved = _arg_bool(args.get("human_approved"), False, name="human_approved")
    timeout_seconds = _bounded_int_arg(
        args, "timeout_seconds", 7200, min_value=1, max_value=14400
    )
    apply_requested = confirm and not dry_run
    if apply_requested and not human_approved:
        raise ToolError(
            "human_approval_required",
            "serve promotion apply requires confirm=true, dry_run=false, and human_approved=true",
        )
    argv = [
        sys.executable, "-m", "anvil_serving.cli", "serves", "promote",
        plan_name, "--manifest", manifest,
    ]
    if rollback:
        argv.append("--rollback")
    if resume:
        argv.append("--resume")
    if not apply_requested:
        argv.append("--dry-run")
        # Execute the canonical dry-run on the serving host.  This validates
        # manifest resolution and topology and returns the same ordered plan a
        # local operator reviews, while remaining non-mutating.
        result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
        return _ok({
            "applied": False,
            "dry_run": True,
            "human_gate_required": True,
            "manifest": manifest,
            "plan": plan_name,
            **result,
        })
    argv.append("--confirm")
    result = _run_argv(argv, confirm=True, timeout=timeout_seconds)
    return _ok({
        "applied": True,
        "dry_run": False,
        "human_approved": True,
        "manifest": manifest,
        "plan": plan_name,
        **result,
    })


def tool_serves_logs(args: dict) -> dict:
    from . import serves as serves_mod

    manifest_arg = _str_arg(args, "manifest", "")
    manifest = serves_mod.resolve_manifest_path(manifest_arg or None)
    names = _str_list_arg(args, "names")
    if len(names) != 1:
        raise ToolError("bad_argument", "serves_logs requires exactly one serve name", {"names": names})
    follow = _arg_bool(args.get("follow"), False, name="follow")
    if follow:
        raise ToolError("follow_not_allowed", "serves_logs rejects unbounded follow mode; use a bounded tail")
    tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
    max_output_bytes = _bounded_int_arg(args, "max_output_bytes", 65536, min_value=1024, max_value=1048576)
    since = _str_arg(args, "since", "")
    manifest_serves = _load_serves_for_tool(manifest)
    _resolve_manifest_serves(serves_mod, manifest_serves, names, caller="serves_logs")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 60, min_value=1, max_value=600)
    argv = _serves_cli_argv("logs", manifest, names, tail=tail, since=since)
    result = _run_argv_spooled(argv, timeout=timeout_seconds, max_output_bytes=max_output_bytes)
    return _ok({
        "bounded": True,
        "tail": tail,
        "since": since or None,
        "max_output_bytes": max_output_bytes,
        **result,
    })


def _voice_cli_argv(
    action: str,
    config: str,
    *,
    topology: str,
    dry_run: bool = False,
    profile: str = "",
    ready_timeout: float = 3.0,
    tail: int = 200,
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "anvil_serving.cli",
        "voice",
        "audio",
        action,
        "--config",
        config,
    ]
    if profile:
        argv += ["--profile", profile]
    argv += ["--topology", topology]
    if dry_run:
        argv.append("--dry-run")
    if action == "status":
        argv += ["--ready-timeout", str(ready_timeout)]
    elif action == "logs":
        argv += ["--tail", str(tail)]
    return argv


def _voice_manage_plan(config: str, *, profile: str = "") -> dict:
    from .voice import config as voice_config
    from .voice.serves import native as native_serve

    try:
        raw = voice_config.load_raw_manifest(config)
        available_profiles = voice_config.profile_names(raw)
        data = voice_config.load_manifest(config, profile=profile) if profile else voice_config.load_manifest(config)
    except FileNotFoundError:
        raise ToolError("config_not_found", "voice manifest not found", {"config": config})
    except voice_config.ConfigError as exc:
        raise ToolError("bad_config", "could not load voice manifest", {"config": config, "error": str(exc)})
    voice = data.get("voice", {})
    audio = []
    for kind in ("stt", "tts"):
        table = voice.get(kind, {})
        lifecycle = table.get("lifecycle", "managed")
        item = {
            "kind": kind,
            "lifecycle": lifecycle,
            "base_url": table.get("base_url"),
            "model": table.get("model"),
        }
        if lifecycle == "native":
            cfg = native_serve.NativeServeConfig.from_table(kind, table)
            item.update({
                "start_command": native_serve.parse_command(cfg.start_command),
                "stop_command": (
                    native_serve.parse_command(cfg.stop_command)
                    if cfg.stop_command else None
                ),
                "workdir": cfg.workdir or None,
                "pid_file": cfg.pid_file,
                "log_file": cfg.log_file,
                "ready_timeout": cfg.ready_timeout,
                "stop_timeout": cfg.stop_timeout,
            })
        elif lifecycle == "external":
            item["note"] = "external/manual lifecycle; voice_manage will skip it"
        else:
            item["note"] = "managed through the voice serve adapter and serves.toml"
        audio.append(item)
    return {
        "voice": voice.get("name", "anvil-voice"),
        "config": config,
        "profile": profile or None,
        "available_profiles": available_profiles,
        "audio_serves": audio,
    }


def tool_voice_manage(args: dict) -> dict:
    """Manage Dark-owned STT/TTS only after local topology authorization."""
    from .topology import load_topology
    from .voice import config as voice_config
    from .voice import cli as voice_cli

    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "status", "logs"}:
        raise ToolError(
            "bad_action",
            "action must be one of: up, down, status, logs",
            {"action": action},
        )
    config_arg = _str_arg(args, "config", "")
    config = voice_config.resolve_config_path(config_arg or None)
    profile = _str_arg(args, "profile", "")
    topology_path = _str_arg(args, "topology", "") or os.environ.get(
        "ANVIL_VOICE_TOPOLOGY", ""
    ).strip()
    if not topology_path:
        raise ToolError(
            "missing_topology",
            "set ANVIL_VOICE_TOPOLOGY on the Dark controller or pass topology",
        )
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 300, min_value=1, max_value=7200)
    ready_timeout = _bounded_float_arg(
        args, "ready_timeout", 3.0, min_value=0.1, max_value=60.0
    )
    tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
    plan = _voice_manage_plan(config, profile=profile)
    try:
        topology = load_topology(topology_path)
        owners = tuple(topology.resource_owner("%s-serve" % kind) for kind in ("stt", "tts"))
        if owners[0].host != owners[1].host or owners[0].runtime != owners[1].runtime:
            raise voice_config.ConfigError(
                "STT and TTS must be co-owned by one host/runtime for audio lifecycle"
            )
        cli_args = argparse.Namespace(
            config=config,
            profile=profile or None,
            topology=topology_path,
            topology_overlay=None,
            command_host=None,
            command_runtime=None,
            target=None,
            transport="local",
            experimental_model_workload=False,
            ready_timeout=ready_timeout,
            tail=tail,
            operation_timeout=float(timeout_seconds),
        )
        data, targets, error, error_code = voice_cli._resolve_audio_operation(cli_args)
        if error:
            raise ToolError(
                "audio_target_refused",
                error,
                {"topology": topology_path, "exit_code": error_code},
            )
    except ToolError:
        raise
    except (OSError, ValueError) as exc:
        raise ToolError(
            "bad_audio_config",
            "could not resolve Dark audio ownership",
            {"config": config, "topology": topology_path, "error": str(exc)},
        )
    assert data is not None and targets is not None
    cli_args._resolved_audio = (data, targets)
    preview = action in {"up", "down"} and (dry_run or not confirm)
    argv = _voice_cli_argv(
        action,
        config,
        topology=topology_path,
        dry_run=preview,
        profile=profile,
        ready_timeout=ready_timeout,
        tail=tail,
    )
    target = {
        "action": action,
        "config": config,
        "profile": profile or None,
        "topology": topology_path,
        "owners": targets.as_dict(),
        "timeout_seconds": timeout_seconds,
    }
    if action in {"status", "logs"}:
        handler = voice_cli.cmd_audio_status if action == "status" else voice_cli.cmd_audio_logs
        returncode, stdout, stderr = _capture(lambda: handler(cli_args))
        if returncode != 0:
            raise ToolError(
                "command_failed",
                "voice audio %s failed" % action,
                {"command": argv, "returncode": returncode, "stderr": stderr},
            )
        return _ok({
            "applied": False,
            "target": target,
            "command": argv,
            "plan": plan,
            "output": stdout,
            "stderr": stderr,
        })
    if preview:
        return _ok({"applied": False, "dry_run": True, "target": target, "command": argv, "plan": plan})
    result = voice_cli.execute_audio_lifecycle(
        data, action, targets=targets, timeout_seconds=float(timeout_seconds)
    )
    if result["returncode"] != 0:
        raise ToolError(
            "command_failed",
            "voice audio lifecycle failed",
            {"command": argv, "lifecycle": result},
        )
    applied = any(item.get("lifecycle") != "external" for item in plan.get("audio_serves", []))
    return _ok({
        "applied": applied,
        "dry_run": False,
        "target": target,
        "command": argv,
        "plan": plan,
        "lifecycle": result,
    })


def tool_voice_proxy_manage(args: dict) -> dict:
    """Manage the persistent Mini proxy process without touching model serves."""
    from .topology import load_topology
    from .voice import config as voice_config
    from .voice.realtime_service import ProxyProcessConfig, RealtimeProxyProcessService

    action = _str_arg(args, "action", required=True)
    if action not in {"up", "down", "restart", "status", "logs"}:
        raise ToolError(
            "bad_action",
            "action must be one of: up, down, restart, status, logs",
            {"action": action},
        )
    config = voice_config.resolve_config_path(_str_arg(args, "config", "") or None)
    profile = _str_arg(args, "profile", "")
    topology_path = _str_arg(args, "topology", "") or os.environ.get(
        "ANVIL_VOICE_TOPOLOGY", ""
    ).strip()
    if not topology_path:
        raise ToolError(
            "missing_topology",
            "set ANVIL_VOICE_TOPOLOGY on the Mini controller or pass topology",
        )
    try:
        data = voice_config.load_manifest(config, profile=profile or None)
        topology = load_topology(topology_path)
        targets = voice_config.resolve_proxy_targets(
            topology,
            operation="voice-proxy-%s" % action,
            transport="local",
        )
    except (OSError, ValueError) as exc:
        raise ToolError(
            "bad_proxy_config",
            "could not resolve Mini proxy configuration",
            {"config": config, "topology": topology_path, "error": str(exc)},
        )
    voice = data["voice"]
    process = RealtimeProxyProcessService(ProxyProcessConfig(
        config_path=config,
        topology_path=topology_path,
        profile=profile or None,
        host=voice.get("realtime_host", "127.0.0.1"),
        port=int(voice.get("realtime_port", 8765)),
        owner=targets.proxy.resource_host.id,
        pid_file=_str_arg(args, "pid_file", "") or os.path.join(
            "~/.anvil-serving/run", "voice-proxy.pid"
        ),
        log_file=_str_arg(args, "log_file", "") or os.path.join(
            "~/.anvil-serving/run", "voice-proxy.log"
        ),
        ready_timeout=float(_bounded_int_arg(
            args, "timeout_seconds", 15, min_value=1, max_value=300
        )),
    ))
    if action == "status":
        return _ok(process.status())
    if action == "logs":
        tail = _bounded_int_arg(args, "tail", 200, min_value=1, max_value=5000)
        return _ok(process.logs(tail=tail))
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    preview = dry_run or not confirm
    result = getattr(process, action)(dry_run=preview)
    result["dry_run"] = preview
    if preview:
        result["applied"] = False
    elif action == "restart":
        result["applied"] = any(
            isinstance(step, dict) and bool(step.get("applied"))
            for step in (result.get("down"), result.get("up"))
        )
    else:
        result["applied"] = bool(
            result.get("applied", result.get("returncode") == 0)
        )
    return _ok(result)


def tool_doctor_summary(args: dict) -> dict:
    from . import doctor

    no_config = _arg_bool(args.get("no_config"), False, name="no_config")
    config = None if no_config else args.get("config", doctor.DEFAULT_CONFIG)
    if config is not None and not isinstance(config, str):
        raise ToolError("bad_argument", "'config' must be a string")
    return _ok(doctor.checks_summary(config_path=config, config_explicit=bool(args.get("config"))))


def tool_host_summary(args: dict) -> dict:
    from . import host

    if args:
        raise ToolError("bad_argument", "host_summary does not accept arguments")
    return _ok(host.host_summary())


def tool_gpu_inventory(args: dict) -> dict:
    from . import gpus

    if args:
        raise ToolError("bad_argument", "gpu_inventory does not accept arguments")
    return _ok({"gpus": gpus.list_gpus()})


def tool_observability_collect(args: dict) -> dict:
    from .observability.api import controller_collect

    capabilities = args.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        raise ToolError("bad_argument", "capabilities must be a non-empty array")
    try:
        return _ok(controller_collect(capabilities))
    except (TypeError, ValueError) as exc:
        raise ToolError("bad_argument", str(exc)) from exc


def tool_host_manage(args: dict) -> dict:
    from . import host

    action = _str_arg(args, "action", required=True)
    if action not in {"wsl-config", "restart-docker", "reset-wsl"}:
        raise ToolError("bad_action", "action must be wsl-config, restart-docker, or reset-wsl")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    force = _arg_bool(args.get("force"), False, name="force")
    revert = _arg_bool(args.get("revert"), False, name="revert")
    memory = _bounded_int_arg(args, "memory", 0, min_value=0, max_value=4096)
    swap = _bounded_int_arg(args, "swap", 0, min_value=0, max_value=4096)
    target = {
        "action": action,
        "memory": memory or None,
        "swap": swap if "swap" in args else None,
        "revert": revert,
        "force": force,
    }
    if action != "wsl-config" and any(key in args for key in ("memory", "swap", "revert", "force")):
        raise ToolError("bad_argument", "memory, swap, revert, and force apply only to wsl-config")
    if dry_run or not confirm:
        return _ok({"applied": False, "dry_run": True, "target": target})
    if action == "wsl-config":
        rc, stdout, stderr = _capture(lambda: host.cmd_wsl_config(
            memory_gb=memory or None,
            swap_gb=swap if "swap" in args else None,
            revert=revert,
            force=force,
        ))
    elif action == "restart-docker":
        rc, stdout, stderr = _capture(lambda: host.cmd_restart_docker(force=True))
    else:
        rc, stdout, stderr = _capture(lambda: host.cmd_reset_wsl(force=True))
    result = {
        "applied": rc == 0,
        "dry_run": False,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "target": target,
    }
    if rc != 0:
        raise ToolError("command_failed", f"host {action} exited with status {rc}", result)
    return _ok(result)


def tool_models_inventory(args: dict) -> dict:
    from . import models

    catalog_dir = _str_arg(args, "catalog_dir", "model-library")
    hf_roots = _str_arg(args, "hf_roots", "")
    model_dirs = _str_arg(args, "model_dirs", "")
    sync = _arg_bool(args.get("sync"), False, name="sync")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 1800, min_value=1, max_value=7200)
    argv = models.build_sync_argv(catalog_dir, hf_roots=hf_roots, model_dirs=model_dirs)
    if sync:
        if not confirm:
            return _ok({
                "synced": False,
                "dry_run": True,
                "catalog_dir": os.path.abspath(catalog_dir),
                "command": [*argv, "--dry-run"],
            })
        apply_argv = [*argv, "--confirm"]
        run_result = _run_argv(apply_argv, confirm=True, timeout=timeout_seconds)
        try:
            inventory = models.load_model_catalog(catalog_dir)
        except models.CatalogNotFound as exc:
            raise ToolError(
                "catalog_not_found",
                "models sync completed but no catalog was found; check sync output and --out",
                {"catalog_dir": exc.catalog_dir, "command": apply_argv, "stdout": run_result.get("stdout", ""), "stderr": run_result.get("stderr", "")},
            )
        except models.CatalogError as exc:
            raise ToolError("bad_catalog", str(exc), exc.details)
        return _ok({
            "synced": True,
            "dry_run": False,
            "command": apply_argv,
            "returncode": run_result["returncode"],
            "stdout": run_result["stdout"],
            "stderr": run_result["stderr"],
            "catalog": inventory,
        })

    try:
        inventory = models.load_model_catalog(catalog_dir)
    except models.CatalogNotFound as exc:
        raise ToolError(
            "catalog_not_found",
            "model catalog not found; run the command from error.details.command first",
            {"catalog_dir": exc.catalog_dir, "command": [*argv, "--confirm"]},
        )
    except models.CatalogError as exc:
        raise ToolError("bad_catalog", str(exc), exc.details)
    return _ok({"synced": False, "dry_run": False, "catalog": inventory})


def _cache_prune_plan_argv(mixture: list[str], *, include_servable: bool) -> list[str]:
    argv = [sys.executable, "-m", "anvil_serving.cli", "models", "cache", "prune", "--json"]
    if mixture:
        argv += ["--mixture", ",".join(mixture)]
    if include_servable:
        argv.append("--include-servable")
    return argv


def tool_cache_prune_plan(args: dict) -> dict:
    from . import cache_prune

    allowed = {"mixture", "include_servable", "execute", "confirm", "yes", "dry_run"}
    extras = sorted(str(key) for key in args if key not in allowed)
    if extras:
        raise ToolError("bad_argument", "unsupported cache_prune_plan argument(s)", {"arguments": extras})
    for name in ("execute", "confirm", "yes"):
        if _arg_bool(args.get(name), False, name=name):
            raise ToolError(
                "cache_prune_delete_not_available",
                "cache_prune_plan is read-only; destructive pruning requires the human-gated CLI",
                {"requested": name},
            )
    if args.get("dry_run") is not None and not _arg_bool(args.get("dry_run"), True, name="dry_run"):
        raise ToolError(
            "cache_prune_delete_not_available",
            "cache_prune_plan cannot disable dry_run through MCP",
            {"requested": "dry_run=false"},
        )

    mixture = sorted(set(_str_list_arg(args, "mixture")))
    include_servable = _arg_bool(args.get("include_servable"), False, name="include_servable")
    argv = _cache_prune_plan_argv(mixture, include_servable=include_servable)
    try:
        plan = cache_prune.build_plan(set(mixture))
        report = cache_prune.execute_plan(plan, dry_run=True, include_servable=include_servable)
    except Exception as exc:
        raise ToolError("cache_prune_plan_failed", str(exc), {"command": argv})
    return _ok({
        "dry_run": True,
        "deletion_available": False,
        "human_gate_required": True,
        "command": argv,
        "mixture": mixture,
        "include_servable": include_servable,
        "plan": plan,
        "report": report,
    })


def _decisions_url(base_url: str, limit: int) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/decisions"):
        url = base
    elif base.endswith("/v1"):
        url = base + "/decisions"
    else:
        url = base + "/v1/decisions"
    return url + "?" + urllib.parse.urlencode({"limit": str(limit)})


def tool_openclaw_sync(args: dict) -> dict:
    from . import harness

    config = _str_arg(args, "config", required=True)
    base_url = _safe_probe_url(_str_arg(args, "base_url", "http://127.0.0.1:8000/v1"))
    if "api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw api_key is not accepted; set api_key_env to the credential env var name",
        )
    api_key_env = _probe_api_key_env({"api_key_env": _str_arg(args, "api_key_env", "ANVIL_ROUTER_TOKEN")})
    gateway_host = _str_arg(args, "gateway_host", "")
    gateway_user = _str_arg(args, "gateway_user", "")
    gateway_path = _str_arg(args, "gateway_path", "~/.openclaw/openclaw.json")
    out = _str_arg(args, "out", "")
    voice = _arg_bool(args.get("voice"), False, name="voice")
    voice_realtime_url = _str_arg(
        args,
        "voice_realtime_url",
        harness.DEFAULT_ANVIL_VOICE_REALTIME_URL,
    )
    voice_model = _str_arg(args, "voice_model", "")
    voice_consult_model = _str_arg(args, "voice_consult_model", "")
    voice_consult_thinking_level = _str_arg(args, "voice_consult_thinking_level", "off")
    voice_consult_bootstrap_context_mode = _str_arg(
        args,
        "voice_consult_bootstrap_context_mode",
        "lightweight",
    )
    try:
        voice_consult_thinking_level = (
            harness._normalize_voice_consult_thinking_level(voice_consult_thinking_level)
            or ""
        )
        voice_consult_bootstrap_context_mode = (
            harness._normalize_voice_consult_bootstrap_context_mode(
                voice_consult_bootstrap_context_mode
            )
            or ""
        )
    except ValueError as exc:
        raise ToolError(
            "bad_argument",
            str(exc),
            {
                "voice_consult_thinking_level": voice_consult_thinking_level,
                "voice_consult_bootstrap_context_mode": voice_consult_bootstrap_context_mode,
            },
        )
    if "voice_api_key" in args:
        raise ToolError(
            "raw_secret_not_allowed",
            "raw voice_api_key is not accepted; set voice_api_key_env to the credential env var name",
        )
    voice_api_key_env = _str_arg(args, "voice_api_key_env", "")
    if voice_api_key_env:
        try:
            harness._validate_env_var_name(voice_api_key_env, arg_name="voice_api_key_env")
        except ValueError as exc:
            raise ToolError("bad_voice_api_key_env", str(exc), {"voice_api_key_env": voice_api_key_env})
    overwrite = _arg_bool(args.get("overwrite"), False, name="overwrite")
    restart = _arg_bool(args.get("restart"), False, name="restart")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 120, min_value=1, max_value=7200)
    if gateway_host:
        try:
            harness._validate_gateway_target(gateway_host, gateway_user)
        except ValueError as exc:
            raise ToolError("bad_gateway_target", str(exc), {"gateway_host": gateway_host, "gateway_user": gateway_user})

    try:
        preview = harness.openclaw_sync_preview(
            config,
            base_url=base_url,
            api_key_env=api_key_env,
            voice=voice,
            voice_realtime_url=voice_realtime_url,
            voice_model=voice_model or None,
            voice_consult_model=voice_consult_model,
            voice_consult_thinking_level=voice_consult_thinking_level,
            voice_consult_bootstrap_context_mode=voice_consult_bootstrap_context_mode,
            voice_api_key_env=voice_api_key_env or None,
        )
    except FileNotFoundError:
        raise ToolError("config_not_found", "router config not found", {"config": config})
    except Exception as exc:
        raise ToolError("bad_config", "could not render OpenClaw config", {"config": config, "error": str(exc)})

    stdout_only = out == "-"
    target = {
        "gateway_host": gateway_host or None,
        "gateway_user": gateway_user or None,
        "gateway_path": gateway_path,
        "out": out or None,
        "voice": voice,
        "voice_realtime_url": voice_realtime_url if voice else None,
        "voice_model": preview.get("voice_model") if voice else None,
        "voice_consult_model": voice_consult_model if voice and voice_consult_model else None,
        "voice_consult_thinking_level": voice_consult_thinking_level if voice else None,
        "voice_consult_bootstrap_context_mode": (
            voice_consult_bootstrap_context_mode if voice else None
        ),
        "voice_api_key_env": voice_api_key_env or None,
        "overwrite": overwrite,
        "restart": restart,
        "timeout_seconds": timeout_seconds,
    }
    if dry_run or not confirm:
        return _ok({"applied": False, "target": target, "preview": preview})
    if not gateway_host and (not out or stdout_only):
        raise ToolError(
            "missing_target",
            "openclaw sync apply requires gateway_host or a real out path; '-' is render-only",
            {"target": target},
        )
    applied_payload = {}
    rc, stdout, stderr = _capture(lambda: harness.cmd_sync_openclaw(
        config,
        out=out or None,
        base_url=base_url,
        api_key_env=api_key_env,
        voice=voice,
        voice_realtime_url=voice_realtime_url,
        voice_model=voice_model or None,
        voice_consult_model=voice_consult_model,
        voice_consult_thinking_level=voice_consult_thinking_level,
        voice_consult_bootstrap_context_mode=voice_consult_bootstrap_context_mode,
        voice_api_key_env=voice_api_key_env or None,
        gateway_host=gateway_host or None,
        gateway_user=gateway_user or None,
        gateway_path=gateway_path,
        overwrite=overwrite,
        restart=restart,
        timeout_seconds=timeout_seconds,
        _replace_provider_keys=tuple(
            key for arg_name, key in (
                ("base_url", "baseUrl"),
                ("api_key_env", "apiKey"),
            )
            if arg_name in args
        ),
        _applied_payload=applied_payload,
    ))
    if rc == 0 and applied_payload:
        preview = harness._openclaw_payload_summary(
            applied_payload,
            voice=voice,
        )
    result = {
        "applied": rc == 0,
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "target": target,
        "preview": {
            "model_count": preview["model_count"],
            "model_ids": preview["model_ids"],
            "base_url": preview["base_url"],
            "api_key": preview["api_key"],
            "direct_aliases": preview["direct_aliases"],
            "voice": preview["voice"],
            "voice_provider": preview["voice_provider"],
            "voice_realtime_url": preview["voice_realtime_url"],
            "voice_model": preview["voice_model"],
            "voice_consult_model": preview["voice_consult_model"],
            "voice_consult_thinking_level": preview["voice_consult_thinking_level"],
            "voice_consult_bootstrap_context_mode": preview[
                "voice_consult_bootstrap_context_mode"
            ],
        },
    }
    if rc != 0:
        raise ToolError("command_failed", "openclaw sync exited with status %s" % rc, result)
    return _ok(result)


def tool_openclaw_gateway_restart(args: dict) -> dict:
    from . import harness

    gateway_host = _str_arg(args, "gateway_host", "")
    gateway_user = _str_arg(args, "gateway_user", "")
    dry_run = _arg_bool(args.get("dry_run"), True, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 120, min_value=1, max_value=7200)
    argv = ["openclaw", "gateway", "restart"]
    if gateway_host:
        try:
            harness._validate_gateway_target(gateway_host, gateway_user)
        except ValueError as exc:
            raise ToolError("bad_gateway_target", str(exc), {"gateway_host": gateway_host, "gateway_user": gateway_user})
        target = ("%s@%s" % (gateway_user, gateway_host)) if gateway_user else gateway_host
        argv = ["ssh", *harness._ssh_options(timeout_seconds), "--", target, harness._REMOTE_RESTART_COMMAND]
    if dry_run or not confirm:
        return _ok({"restarted": False, "dry_run": True, "command": argv})
    rc, stdout, stderr = _capture(lambda: harness.cmd_restart_openclaw(
        gateway_host=gateway_host or None,
        gateway_user=gateway_user or None,
        timeout_seconds=timeout_seconds,
    ))
    result = {"restarted": rc == 0, "returncode": rc, "stdout": stdout, "stderr": stderr}
    if rc != 0:
        raise ToolError("command_failed", "openclaw restart exited with status %s" % rc, result)
    return _ok(result)


def tool_openclaw_gateway_status(args: dict) -> dict:
    from . import harness

    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 120, min_value=1, max_value=7200)
    max_output_bytes = _bounded_int_arg(
        args, "max_output_bytes", 65536, min_value=1024, max_value=1048576
    )
    try:
        result = harness.openclaw_gateway_status(
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
    except ValueError as exc:
        raise ToolError("bad_argument", str(exc))
    if not result.get("ok"):
        raise ToolError("command_failed", "OpenClaw gateway status failed", result)
    return _ok(result)


def tool_preflight_probe(args: dict) -> dict:
    from .model_controls import validate_reasoning_control

    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    needle_ctx = _bounded_int_arg(args, "needle_ctx", 128000, min_value=1, max_value=1000000)
    tool_batch = _bounded_int_arg(args, "tool_batch", 20, min_value=1, max_value=128)
    no_thinking = _arg_bool(args.get("no_thinking"), False, name="no_thinking")
    checks = _str_arg(args, "checks") or "smoke,json,needle,tools"
    selected_checks = [item.strip() for item in checks.split(",") if item.strip()]
    unknown_checks = sorted(set(selected_checks) - {"smoke", "json", "needle", "tools"})
    if not selected_checks or unknown_checks:
        raise ToolError(
            "bad_argument",
            "checks must select smoke,json,needle,tools",
            {"unknown": unknown_checks},
        )
    thinking_mode = _str_arg(args, "thinking_mode") or "default"
    if thinking_mode not in {"default", "enabled", "disabled", "unsupported"}:
        raise ToolError("bad_argument", "thinking_mode has an unsupported value")
    reasoning_effort = _str_arg(args, "reasoning_effort")
    if no_thinking:
        if thinking_mode not in {"default", "disabled"} or reasoning_effort:
            raise ToolError("bad_argument", "no_thinking conflicts with explicit thinking controls")
        thinking_mode = "disabled"
    if reasoning_effort and thinking_mode != "default":
        raise ToolError("bad_argument", "reasoning_effort cannot be combined with thinking_mode")
    try:
        validate_reasoning_control(
            model,
            thinking_mode=thinking_mode,
            no_thinking=no_thinking,
            reasoning_effort=reasoning_effort or None,
        )
    except ValueError as exc:
        raise ToolError("bad_argument", str(exc)) from None
    reasoning_evidence = _str_arg(args, "reasoning_evidence") or "any"
    if reasoning_evidence not in {"any", "required", "forbidden"}:
        raise ToolError("bad_argument", "reasoning_evidence has an unsupported value")
    visible_tokens = _bounded_int_arg(
        args, "visible_answer_tokens", 256, min_value=1, max_value=65536
    )
    reasoning_tokens = _bounded_int_arg(
        args, "reasoning_headroom_tokens", 0, min_value=0, max_value=65536
    )
    if visible_tokens + reasoning_tokens > 65536:
        raise ToolError("bad_argument", "combined completion allocation exceeds 65536")
    allowed_finish_reasons = _str_arg(args, "allowed_finish_reasons") or "stop,tool_calls"
    if not any(item.strip() for item in allowed_finish_reasons.split(",")):
        raise ToolError("bad_argument", "allowed_finish_reasons cannot be empty")
    dry_run = _arg_bool(args.get("dry_run"), False, name="dry_run")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 900, min_value=1, max_value=3600)
    operation_timeout = timeout_seconds * len(selected_checks) + 5
    if operation_timeout > 7200:
        raise ToolError(
            "bad_argument",
            "preflight workload deadline exceeds 7200 seconds; reduce checks or "
            "timeout_seconds",
        )
    argv = [sys.executable, "-m", "anvil_serving.preflight", "--base-url", base_url,
            "--model", model, "--needle-ctx", str(needle_ctx), "--tool-batch", str(tool_batch),
            "--checks", checks, "--thinking-mode", thinking_mode,
            "--visible-answer-tokens", str(visible_tokens),
            "--reasoning-headroom-tokens", str(reasoning_tokens),
            "--reasoning-evidence", reasoning_evidence,
            "--allowed-finish-reasons", allowed_finish_reasons,
            "--timeout-seconds", str(timeout_seconds)]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")
    if reasoning_effort:
        argv += ["--reasoning-effort", reasoning_effort]
    if dry_run:
        argv.append("--dry-run")
        result = _run_argv(argv, confirm=True, timeout=min(operation_timeout, 60))
        return _ok({"applied": False, "dry_run": True, **result})
    result = _run_argv(argv, confirm=confirm, timeout=operation_timeout)
    return _ok({"applied": bool(confirm), "dry_run": not confirm, **result})


def tool_benchmark_probe(args: dict) -> dict:
    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    requests = _bounded_int_arg(args, "requests", 60, min_value=1, max_value=200)
    concurrency = _bounded_int_arg(args, "concurrency", 20, min_value=1, max_value=100)
    max_tokens = _bounded_int_arg(args, "max_tokens", 64, min_value=1, max_value=4096)
    ctx_tokens = _bounded_int_arg(args, "ctx_tokens", 0, min_value=0, max_value=262144)
    no_thinking = _arg_bool(args.get("no_thinking"), False, name="no_thinking")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 900, min_value=1, max_value=1800)
    waves = (requests + concurrency - 1) // concurrency
    operation_timeout = timeout_seconds * waves + 30
    if operation_timeout > 7200:
        raise ToolError(
            "bad_argument",
            "benchmark workload deadline exceeds 7200 seconds; reduce requests or timeout_seconds",
        )
    argv = [sys.executable, "-m", "anvil_serving.benchmark", "capacity",
            "--base-url", base_url,
            "--model", model, "--requests", str(requests), "--concurrency", str(concurrency),
            "--max-tokens", str(max_tokens), "--ctx-tokens", str(ctx_tokens),
            "--timeout-seconds", str(timeout_seconds)]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")
    return _ok(_run_argv(argv, confirm=confirm, timeout=operation_timeout))


def tool_benchmark_artifact(args: dict) -> dict:
    base_url = _safe_probe_url(_str_arg(args, "base_url", required=True))
    model = _str_arg(args, "model", required=True)
    api_key_env = _probe_api_key_env(args)
    artifact_path, allowed_roots = _resolve_benchmark_artifact_path(_str_arg(args, "artifact_path", required=True))
    requests = _bounded_int_arg(args, "requests", 60, min_value=1, max_value=200)
    concurrency = _bounded_int_arg(args, "concurrency", 20, min_value=1, max_value=100)
    burst = _bounded_int_arg(args, "burst", 0, min_value=0, max_value=200)
    max_tokens = _bounded_int_arg(args, "max_tokens", 64, min_value=1, max_value=4096)
    ctx_tokens = _bounded_int_arg(args, "ctx_tokens", 0, min_value=0, max_value=262144)
    max_model_len = _bounded_int_arg(args, "max_model_len", 0, min_value=0, max_value=1048576)
    no_thinking = _arg_bool(args.get("no_thinking"), False, name="no_thinking")
    confirm = _arg_bool(args.get("confirm"), False, name="confirm")
    timeout_seconds = _bounded_int_arg(args, "timeout_seconds", 900, min_value=1, max_value=1800)
    effective_requests = burst or requests
    effective_concurrency = burst or concurrency
    waves = (effective_requests + effective_concurrency - 1) // effective_concurrency
    operation_timeout = timeout_seconds * waves + 30
    if operation_timeout > 7200:
        raise ToolError(
            "bad_argument",
            "benchmark workload deadline exceeds 7200 seconds; reduce requests or timeout_seconds",
        )
    argv = [sys.executable, "-m", "anvil_serving.benchmark", "capacity",
            "--base-url", base_url,
            "--model", model, "--requests", str(requests), "--concurrency", str(concurrency),
            "--max-tokens", str(max_tokens), "--ctx-tokens", str(ctx_tokens),
            "--timeout-seconds", str(timeout_seconds), "--json-out", artifact_path]
    if burst:
        argv += ["--burst", str(burst)]
    if max_model_len:
        argv += ["--max-model-len", str(max_model_len)]
    if api_key_env:
        argv += ["--api-key-env", api_key_env]
    if no_thinking:
        argv.append("--no-thinking")

    if not confirm:
        return _ok({
            "applied": False,
            "dry_run": True,
            "artifact_path": artifact_path,
            "allowed_roots": allowed_roots,
            "command": argv,
        })

    parent = os.path.dirname(artifact_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    before = None
    if os.path.isfile(artifact_path):
        stat = os.stat(artifact_path)
        before = (stat.st_mtime_ns, stat.st_size)
    result = _run_argv(argv, confirm=True, timeout=operation_timeout)
    if before is not None and os.path.isfile(artifact_path):
        stat = os.stat(artifact_path)
        if (stat.st_mtime_ns, stat.st_size) == before:
            raise ToolError(
                "artifact_not_written",
                "benchmark completed but did not replace the existing JSON artifact",
                {"artifact_path": artifact_path},
            )
    summary = _read_benchmark_artifact(artifact_path)
    return _ok({
        "applied": True,
        "dry_run": False,
        "artifact_path": artifact_path,
        "allowed_roots": allowed_roots,
        "key_metrics": _benchmark_key_metrics(summary),
        "summary": summary,
        **result,
    })


def _external_bench_db_path(db_path: str) -> str:
    return _resolve_benchmark_artifact_path(db_path)[0]


def _external_bench_existing_db_path(db_path: str, *, required: bool = True) -> tuple[str, bool]:
    db = _external_bench_db_path(db_path)
    if os.path.isfile(db):
        return db, True
    if required:
        raise ToolError(
            "external_bench_db_not_found",
            "external benchmark DB not found; run benchmark external init/import first",
            {"db": db},
        )
    return db, False


def _external_bench_known_sources() -> list[dict[str, Any]]:
    from .external_benchmarks import store

    rows = []
    for name, info in sorted(store.KNOWN_SOURCES.items()):
        rows.append({
            "name": name,
            "kind": info.get("kind"),
            "homepage_url": info.get("homepage_url"),
            "notes": info.get("notes"),
            "snapshot_id": None,
            "imported_at": None,
            "fetched_at": None,
            "parse_status": None,
            "raw_sha256": None,
        })
    return rows


def _external_bench_read_error(exc: sqlite3.Error, db: str) -> ToolError:
    return ToolError("bad_external_bench_db", "could not read external benchmark DB", {"db": db, "error": str(exc)})


def _external_bench_filters(args: dict, *, default_top: int = 20) -> tuple[str, str, str, int]:
    gpu = _str_arg(args, "gpu", "")
    model = _str_arg(args, "model", "")
    source = _str_arg(args, "source", "")
    top = _bounded_int_arg(args, "top", default_top, min_value=1, max_value=1000)
    return gpu, model, source, top


def _external_bench_envelope(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "advisory_only": True,
        "promotion_quality_evidence": False,
        **data,
    }


def _external_bench_match(item: Any, local: dict[str, Any] | None = None) -> dict[str, Any]:
    score, mismatches, row = item
    match = {
        "score": score,
        "mismatches": list(mismatches),
        "row": dict(row),
    }
    if local is not None:
        match["deltas"] = {
            "throughput_tok_s": _metric_delta(local.get("throughput_tok_s"), row.get("throughput_tok_s")),
            "ttft_ms": _metric_delta(local.get("ttft_ms"), row.get("ttft_ms")),
        }
    return match


def tool_external_bench_sources(args: dict) -> dict:
    from .external_benchmarks import store

    db, exists = _external_bench_existing_db_path(_str_arg(args, "db", store.DEFAULT_DB), required=False)
    if exists:
        try:
            rows = store.list_sources(db, initialize=False)
        except sqlite3.Error as exc:
            raise _external_bench_read_error(exc, db)
    else:
        rows = _external_bench_known_sources()
    return _ok(_external_bench_envelope({"db": db, "db_exists": exists, "sources": rows}))


def tool_external_bench_list(args: dict) -> dict:
    from .external_benchmarks import store

    db, _ = _external_bench_existing_db_path(_str_arg(args, "db", store.DEFAULT_DB))
    gpu, model, source, top = _external_bench_filters(args, default_top=20)
    try:
        rows = store.query_rows(db, gpu=gpu or None, model=model or None, source=source or None, top=top, initialize=False)
    except sqlite3.Error as exc:
        raise _external_bench_read_error(exc, db)
    return _ok(_external_bench_envelope({
        "db": db,
        "filters": {"gpu": gpu or None, "model": model or None, "source": source or None, "top": top},
        "rows": rows,
        "count": len(rows),
    }))


def tool_external_bench_report(args: dict) -> dict:
    from .external_benchmarks import store

    db, _ = _external_bench_existing_db_path(_str_arg(args, "db", store.DEFAULT_DB))
    gpu, model, source, top = _external_bench_filters(args, default_top=100)
    try:
        rows = store.query_rows(db, gpu=gpu or None, model=model or None, source=source or None, top=top, initialize=False)
    except sqlite3.Error as exc:
        raise _external_bench_read_error(exc, db)
    return _ok(_external_bench_envelope({
        "db": db,
        "filters": {"gpu": gpu or None, "model": model or None, "source": source or None, "top": top},
        "columns": [
            "source_name",
            "model_id_normalized",
            "gpu_model",
            "engine",
            "quantization",
            "precision",
            "context_tokens",
            "concurrency",
            "throughput_tok_s",
            "ttft_ms",
        ],
        "rows": rows,
        "count": len(rows),
    }))


def tool_external_bench_compare(args: dict) -> dict:
    from .external_benchmarks import compare, store

    db, _ = _external_bench_existing_db_path(_str_arg(args, "db", store.DEFAULT_DB))
    local_path = _resolve_benchmark_artifact_path(_str_arg(args, "local", required=True))[0]
    if not os.path.isfile(local_path):
        raise ToolError("local_benchmark_not_found", "local benchmark artifact not found", {"local": local_path})
    gpu = _str_arg(args, "gpu", "")
    top = _bounded_int_arg(args, "top", 5, min_value=1, max_value=100)
    try:
        result = compare.compare_local_to_external(db, local_path, gpu=gpu or None, top=top, record=False, initialize=False)
    except sqlite3.Error as exc:
        raise _external_bench_read_error(exc, db)
    local = dict(result["local"])
    chosen = result.get("chosen")
    nearest = [_external_bench_match(item, local) for item in (result.get("nearest") or [])]
    data = {
        "db": db,
        "local_path": local_path,
        "gpu": gpu or None,
        "local": local,
        "fingerprint": result["fingerprint"],
        "exact": bool(result.get("exact")),
        "warnings": list(result.get("warnings") or []),
        "chosen": _external_bench_match(chosen, local) if chosen else None,
        "nearest": nearest,
        "comparison": {
            "match_type": "exact" if result.get("exact") else ("nearest" if chosen else "none"),
            "has_external_prior": bool(chosen),
        },
    }
    return _ok(_external_bench_envelope(data))


def tool_workflow_packet_validate(args: dict) -> dict:
    packet = args.get("packet")
    if packet is None:
        raise ToolError("missing_argument", "missing required argument 'packet'")
    return _ok(validate_workflow_packet(packet))


def operation_declarations() -> list[dict[str, Any]]:
    """Return every command-tree operation declared for controller transport."""

    return _catalog_operation_declarations(COMMAND_TREE.nodes, TOOLS)


TARGET_CONTEXT_SCHEMA = _bounded_tool_schema({
    "type": "object",
    "additionalProperties": False,
    "properties": {
        field: {
            "type": ["string", "boolean", "integer", "number", "null"],
            "maxLength": _MAX_CONTEXT_STRING,
        }
        for field in CONTEXT_FIELDS
    },
})


def _validate_tool_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_arguments(name, arguments, TOOLS)


def validate_tool_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one typed tool call without dispatching it."""
    return _validate_tool_arguments(name, arguments)


def _target_context(value: Any) -> dict[str, Any]:
    return _build_target_context(
        value,
        context_fields=CONTEXT_FIELDS,
        context_schema=TARGET_CONTEXT_SCHEMA,
        context_builder=context_from_plan,
    )


def tool_operation_contracts(args: dict) -> dict:
    if args:
        raise ToolError("bad_argument", "operation_contracts does not accept arguments")
    return _ok({"operations": operation_declarations()})


TOOLS: Dict[str, dict] = _build_catalog({
    "operation_contracts": {
        "description": "List command-tree operations declared for bounded controller transport.",
        "inputSchema": _schema({}),
        "handler": tool_operation_contracts,
    },
    "router_status": {
        "description": "Inspect the deployed anvil router container and loopback health.",
        "inputSchema": _schema({"container": {"type": "string"}}),
        "handler": tool_router_status,
    },
    "router_logs": {
        "description": "Read bounded, redacted docker logs for the deployed router; follow mode is not allowed.",
        "inputSchema": _schema({
            "container": {"type": "string"},
            "tail": _bounded_integer_schema(1, 5000, 200),
            "max_output_bytes": _bounded_integer_schema(1024, 1048576, 65536),
            "since": {"type": "string"},
            "follow": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 600, 60),
        }),
        "handler": tool_router_logs,
    },
    "router_manage": {
        "description": "Preview or run guarded deployed-router lifecycle actions: up, down, restart, or reload.",
        "inputSchema": _schema({
            "action": {"type": "string"},
            "container": {"type": "string"},
            "compose": {"type": "string"},
            "service": {"type": "string"},
            "env_file": {"type": "string"},
            "recreate": {"type": "boolean"},
            "no_verify": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 300),
        }, required=["action"]),
        "handler": tool_router_manage,
    },
    "router_transition": {
        "description": "Inspect, quiesce, drain, or safely readmit a router tier through the authenticated router boundary.",
        "inputSchema": _schema({
            "action": {"type": "string"},
            "tier": {"type": "string"},
            "router_url": {"type": "string"},
            "timeout": _bounded_integer_schema(1, 3600, 60),
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        }, required=["action"]),
        "handler": tool_router_transition,
    },
    "decision_summary": {
        "description": "Summarize recent router decisions without prompts or secrets; defaults to GET /v1/decisions.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "api_key_env": {"type": "string"},
            "records": {"type": "array", "items": {"type": "object"}},
            "path": {"type": "string"},
            "limit": _bounded_integer_schema(1, 500, 20),
            "max_input_bytes": _bounded_integer_schema(1024, 10485760, 1048576),
            "timeout_seconds": _bounded_integer_schema(1, 60, 5),
        }),
        "handler": tool_decision_summary,
    },
    "serves_status": {
        "description": "Inspect model serves from a serves.toml manifest.",
        "inputSchema": _schema({
            "manifest": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
        }),
        "handler": tool_serves_status,
    },
    "reservation_status": {
        "description": "Return the read-only per-gpu_role VRAM reservation ledger (ADR-0017) derived from the serves manifest and docker state.",
        "inputSchema": _schema({
            "manifest": {"type": "string"},
        }),
        "handler": tool_reservation_status,
    },
    "serves_manage": {
        "description": "Preview or run guarded serve lifecycle actions: up, down, rm, or adopt.",
        "inputSchema": _schema({
            "action": {"type": "string"},
            "manifest": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
            "compose": {"type": "string"},
            "recreate": {"type": "boolean"},
            "allow_literal": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 300),
        }, required=["action"]),
        "handler": tool_serves_manage,
    },
    "serves_promote": {
        "description": "Preview or execute the complete guarded serve promotion/rollback transaction.",
        "inputSchema": _schema({
            "manifest": {"type": "string"},
            "plan": {"type": "string"},
            "rollback": {"type": "boolean"},
            "resume": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "human_approved": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 14400, 7200),
        }, required=["plan"]),
        "handler": tool_serves_promote,
    },
    "serves_logs": {
        "description": "Read bounded docker logs for one manifest serve; follow mode is not allowed.",
        "inputSchema": _schema({
            "manifest": {"type": "string"},
            "names": {"type": "array", "items": {"type": "string"}},
            "tail": _bounded_integer_schema(1, 5000, 200),
            "max_output_bytes": _bounded_integer_schema(1024, 1048576, 65536),
            "since": {"type": "string"},
            "follow": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 600, 60),
        }, required=["names"]),
        "handler": tool_serves_logs,
    },
    "voice_manage": {
        "description": "Preview or run guarded voice STT/TTS lifecycle actions with optional voice profile selection.",
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["up", "down", "status", "logs"]},
            "config": {"type": "string"},
            "profile": {"type": "string"},
            "topology": {"type": "string"},
            "ready_timeout": {"type": "number", "minimum": 0.1, "maximum": 60.0, "default": 3.0},
            "tail": _bounded_integer_schema(1, 5000, 200),
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 300),
        }, required=["action"]),
        "handler": tool_voice_manage,
    },
    "voice_proxy_manage": {
        "description": "Manage the persistent Mini-owned Realtime proxy process.",
        "inputSchema": _schema({
            "action": {"type": "string", "enum": ["up", "down", "restart", "status", "logs"]},
            "config": {"type": "string"},
            "profile": {"type": "string"},
            "topology": {"type": "string"},
            "pid_file": {"type": "string"},
            "log_file": {"type": "string"},
            "tail": _bounded_integer_schema(1, 5000, 200),
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 300, 15),
        }, required=["action"]),
        "handler": tool_voice_proxy_manage,
    },
    "doctor_summary": {
        "description": "Run anvil-serving environment checks and return structured results.",
        "inputSchema": _schema({
            "config": {"type": "string"},
            "no_config": {"type": "boolean"},
        }),
        "handler": tool_doctor_summary,
    },
    "host_summary": {
        "description": "Return read-only WSL/Docker/GPU host checks; performs no repair or restart.",
        "inputSchema": _schema({}),
        "handler": tool_host_summary,
    },
    "gpu_inventory": {
        "description": "Return the local NVIDIA GPU inventory with stable UUIDs.",
        "inputSchema": _schema({}),
        "handler": tool_gpu_inventory,
    },
    "observability_collect": {
        "description": "Collect bounded structured telemetry from declared local capabilities.",
        "inputSchema": _schema({
            "capabilities": {
                "type": "array",
                "maxItems": 32,
                "items": {"type": "string", "maxLength": 80},
            },
        }, required=["capabilities"]),
        "handler": tool_observability_collect,
    },
    "host_manage": {
        "description": "Preview or run a bounded host repair operation on the controller host.",
        "inputSchema": _schema({
            "action": {"type": "string"},
            "memory": {"type": "integer", "minimum": 1, "maximum": 4096},
            "swap": {"type": "integer", "minimum": 0, "maximum": 4096},
            "revert": {"type": "boolean"},
            "force": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
        }, required=["action"]),
        "handler": tool_host_manage,
    },
    "models_inventory": {
        "description": "Read the generated model catalog, or preview/run `models sync` to create it.",
        "inputSchema": _schema({
            "catalog_dir": {"type": "string"},
            "hf_roots": {"type": "string"},
            "model_dirs": {"type": "string"},
            "sync": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 1800),
        }),
        "handler": tool_models_inventory,
    },
    "cache_prune_plan": {
        "description": "Return a JSON model-cache prune plan and dry-run report; deletion is not available through MCP.",
        "inputSchema": _schema({
            "mixture": {"type": "array", "items": {"type": "string"}},
            "include_servable": {"type": "boolean"},
        }),
        "handler": tool_cache_prune_plan,
    },
    "openclaw_sync": {
        "description": "Preview or apply an OpenClaw provider config from the router's direct model aliases.",
        "inputSchema": _schema({
            "config": {"type": "string"},
            "base_url": {"type": "string"},
            "api_key_env": {"type": "string"},
            "gateway_host": {"type": "string"},
            "gateway_user": {"type": "string"},
            "gateway_path": {"type": "string"},
            "out": {"type": "string"},
            "voice": {"type": "boolean"},
            "voice_realtime_url": {"type": "string"},
            "voice_model": {"type": "string"},
            "voice_consult_model": {"type": "string"},
            "voice_consult_thinking_level": {
                "type": "string",
                "enum": ["adaptive", "high", "low", "max", "medium", "minimal", "off", "xhigh"],
            },
            "voice_consult_bootstrap_context_mode": {
                "type": "string",
                "enum": ["full", "lightweight"],
            },
            "voice_api_key_env": {"type": "string"},
            "overwrite": {"type": "boolean"},
            "restart": {"type": "boolean"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 120),
        }, required=["config"]),
        "handler": tool_openclaw_sync,
    },
    "openclaw_gateway_restart": {
        "description": "Restart the OpenClaw gateway locally or over SSH. Requires confirm=true.",
        "inputSchema": _schema({
            "gateway_host": {"type": "string"},
            "gateway_user": {"type": "string"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 7200, 120),
        }),
        "handler": tool_openclaw_gateway_restart,
    },
    "openclaw_gateway_status": {
        "description": "Read bounded local OpenClaw gateway status.",
        "inputSchema": _schema({
            "timeout_seconds": _bounded_integer_schema(1, 7200, 120),
            "max_output_bytes": _bounded_integer_schema(1024, 1048576, 65536),
        }),
        "handler": tool_openclaw_gateway_status,
    },
    "preflight_probe": {
        "description": "Preview or run an anvil-serving eval preflight command for a model endpoint.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "api_key_env": {"type": "string"},
            "needle_ctx": _bounded_integer_schema(1, 1000000, 128000),
            "tool_batch": _bounded_integer_schema(1, 128, 20),
            "no_thinking": {"type": "boolean"},
            "checks": {"type": "string"},
            "thinking_mode": {"type": "string", "enum": ["default", "enabled", "disabled", "unsupported"]},
            "reasoning_effort": {"type": "string", "enum": ["none", "minimal", "low", "medium", "high"]},
            "reasoning_evidence": {"type": "string", "enum": ["any", "required", "forbidden"]},
            "visible_answer_tokens": _bounded_integer_schema(1, 65536, 256),
            "reasoning_headroom_tokens": _bounded_integer_schema(0, 65536, 0),
            "allowed_finish_reasons": {"type": "string"},
            "dry_run": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 3600, 900),
        }, required=["base_url", "model"]),
        "handler": tool_preflight_probe,
    },
    "benchmark_probe": {
        "description": "Preview or run a bounded eval benchmark capacity command for a model endpoint.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "api_key_env": {"type": "string"},
            "requests": _bounded_integer_schema(1, 200, 60),
            "concurrency": _bounded_integer_schema(1, 100, 20),
            "max_tokens": _bounded_integer_schema(1, 4096, 64),
            "ctx_tokens": _bounded_integer_schema(0, 262144, 0),
            "no_thinking": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 1800, 900),
        }, required=["base_url", "model"]),
        "handler": tool_benchmark_probe,
    },
    "benchmark_artifact": {
        "description": "Preview or run a bounded eval benchmark capacity command and atomically replace a validated local JSON artifact.",
        "inputSchema": _schema({
            "base_url": {"type": "string"},
            "model": {"type": "string"},
            "artifact_path": {"type": "string"},
            "api_key_env": {"type": "string"},
            "requests": _bounded_integer_schema(1, 200, 60),
            "concurrency": _bounded_integer_schema(1, 100, 20),
            "burst": _bounded_integer_schema(0, 200, 0),
            "max_tokens": _bounded_integer_schema(1, 4096, 64),
            "ctx_tokens": _bounded_integer_schema(0, 262144, 0),
            "max_model_len": _bounded_integer_schema(0, 1048576, 0),
            "no_thinking": {"type": "boolean"},
            "confirm": {"type": "boolean"},
            "timeout_seconds": _bounded_integer_schema(1, 1800, 900),
        }, required=["base_url", "model", "artifact_path"]),
        "handler": tool_benchmark_artifact,
    },
    "workflow_packet_validate": {
        "description": "Validate and normalize an operator-workflow/v1 result packet before using it as evidence.",
        "inputSchema": _schema({
            "packet": {"type": "object"},
        }, required=["packet"]),
        "handler": tool_workflow_packet_validate,
    },
    "external_bench_sources": {
        "description": "List known external benchmark sources and latest snapshots as advisory-only priors.",
        "inputSchema": _schema({
            "db": {"type": "string"},
        }),
        "handler": tool_external_bench_sources,
    },
    "external_bench_list": {
        "description": "List normalized external benchmark rows as advisory-only priors.",
        "inputSchema": _schema({
            "db": {"type": "string"},
            "gpu": {"type": "string"},
            "model": {"type": "string"},
            "source": {"type": "string"},
            "top": _bounded_integer_schema(1, 1000, 20),
        }),
        "handler": tool_external_bench_list,
    },
    "external_bench_report": {
        "description": "Return a structured external benchmark report as advisory-only priors.",
        "inputSchema": _schema({
            "db": {"type": "string"},
            "gpu": {"type": "string"},
            "model": {"type": "string"},
            "source": {"type": "string"},
            "top": _bounded_integer_schema(1, 1000, 100),
        }),
        "handler": tool_external_bench_report,
    },
    "external_bench_compare": {
        "description": "Compare a local benchmark artifact against external advisory priors.",
        "inputSchema": _schema({
            "db": {"type": "string"},
            "local": {"type": "string"},
            "gpu": {"type": "string"},
            "top": _bounded_integer_schema(1, 100, 5),
        }, required=["local"]),
        "handler": tool_external_bench_compare,
    },
})


def list_tools() -> list[dict]:
    return _list_catalog_tools(TOOLS, TARGET_CONTEXT_SCHEMA)


def call_tool(name: str, arguments: Optional[dict] = None) -> dict:
    return _call_catalog_tool(
        TOOLS,
        name,
        arguments,
        validate_arguments=validate_tool_arguments,
        fail=_fail,
        redact_text=_redact_text,
    )


def handle_request(request: dict) -> Optional[dict]:
    return _handle_protocol_request(
        request,
        tools=TOOLS,
        protocol_version=PROTOCOL_VERSION,
        server_info=SERVER_INFO,
        list_tools=list_tools,
        call_tool=call_tool,
        target_context=_target_context,
    )


def handle_proxy_request(request: dict, controller_url: str, token: str) -> Optional[dict]:
    return _handle_proxy_protocol_request(
        request,
        controller_url,
        token,
        tools=TOOLS,
        local_request=handle_request,
        list_tools=list_tools,
        validate_arguments=_validate_tool_arguments,
        target_context=_target_context,
        remote_request=remote_controller_request,
    )


def serve_stdio(
    stdin: Iterable[str] = sys.stdin,
    stdout: Any = sys.stdout,
    *,
    controller_url: str = "",
    controller_token: str = "",
) -> int:
    return _serve_stdio_loop(
        stdin,
        stdout,
        controller_url=controller_url,
        controller_token=controller_token,
        handle_local_request=handle_request,
        handle_remote_request=handle_proxy_request,
    )


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return _stdio_main(
        argv,
        list_tools=list_tools,
        safe_controller_url=_safe_controller_url,
        resolve_controller_token=resolve_controller_token,
        serve=serve_stdio,
    )


if __name__ == "__main__":
    raise SystemExit(main())
