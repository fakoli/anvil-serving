"""Explicit workflow MCP tool family."""

from __future__ import annotations

import re
from typing import Any, Optional

from ..arguments import (
    schema as _schema,
)
from ..catalog import ToolFamily
from ..errors import ToolError
from ..errors import ok as _ok
from ..evidence import (
    resolve_benchmark_artifact_path as _resolve_benchmark_artifact_path,
)

_WORKFLOW_SCHEMA_VERSION = "operator-workflow/v1"

_WORKFLOW_GATE_STATES = {"not_required", "confirm_required", "human_required", "blocked"}

_WORKFLOW_SOURCE_CLASSES = {"mcp", "controller", "cli", "manual", "fixture"}

_WORKFLOW_RECOMMENDATIONS = {"promote", "do_not_promote", "needs_more_data", "blocked"}

_WORKFLOW_VOICE_ARTIFACT_KINDS = {"voice-benchmark", "voice-sidecar-render"}

_WORKFLOW_VOICE_CONTEXT_RE = re.compile(r"(^|[^a-z0-9])(voice|stt|tts|realtime)([^a-z0-9]|$)", re.I)

_WORKFLOW_SEMANTIC_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]*$", re.I)

_WORKFLOW_DRIVE_RELATIVE_RE = re.compile(r"^[a-z]:", re.I)

_WORKFLOW_PATH_FIELD_RE = re.compile(
    r"(^|_)(path|file|filename|dir|directory|root|roots)(_|$)", re.I
)

_WORKFLOW_DOTTED_DOMAIN_SEGMENTS = frozenset(
    {
        "benchmark",
        "pipeline",
        "realtime",
        "sidecar",
        "stt",
        "tts",
        "voice",
    }
)

_WORKFLOW_VERSION_SEGMENT_RE = re.compile(r"^v[0-9]+$", re.I)


def _workflow_error(
    errors: list[dict[str, Any]], field: str, message: str, details: Optional[dict] = None
) -> None:
    errors.append({"field": field, "message": message, "details": details or {}})


def _workflow_is_dotted_domain_symbol(value: str) -> bool:
    parts = value.split(".")
    if len(parts) < 2 or not all(
        _WORKFLOW_SEMANTIC_IDENTIFIER_RE.fullmatch(part) for part in parts
    ):
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
            if len(parts) < 2 or not all(
                _WORKFLOW_VOICE_CONTEXT_RE.fullmatch(part) for part in parts
            ):
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
    tool_names = (
        [tool.get("name") for tool in tools_used if isinstance(tool, dict)]
        if isinstance(tools_used, list)
        else []
    )
    return (
        _workflow_request_mentions_voice(packet.get("request"))
        or _workflow_targets_mention_voice(packet.get("targets"))
        or _workflow_symbol_mentions_voice(tool_names)
        or _workflow_artifacts_declare_voice(artifacts)
    )


def _normalize_workflow_artifacts(
    packet: dict[str, Any], errors: list[dict[str, Any]]
) -> list[Any]:
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
                _workflow_error(
                    errors, field + ".evidence_scope", "artifact evidence_scope must be a string"
                )
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
    if (
        tool.get("ok") is not True
        or tool.get("dry_run") is not False
        or tool.get("confirmed") is not True
    ):
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
        return {
            "valid": False,
            "errors": [{"field": "packet", "message": "packet must be an object", "details": {}}],
        }

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
        _workflow_error(
            errors, "schema_version", "schema_version must be %r" % _WORKFLOW_SCHEMA_VERSION
        )
    if not isinstance(packet.get("request"), str) or not packet.get("request", "").strip():
        _workflow_error(errors, "request", "request must be a non-empty string")
    if packet.get("gate_state") not in _WORKFLOW_GATE_STATES:
        _workflow_error(
            errors, "gate_state", "invalid gate_state", {"allowed": sorted(_WORKFLOW_GATE_STATES)}
        )
    if packet.get("recommendation") not in _WORKFLOW_RECOMMENDATIONS:
        _workflow_error(
            errors,
            "recommendation",
            "invalid recommendation",
            {"allowed": sorted(_WORKFLOW_RECOMMENDATIONS)},
        )
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
            _workflow_error(
                errors,
                field + ".source_class",
                "invalid source_class",
                {"allowed": sorted(_WORKFLOW_SOURCE_CLASSES)},
            )
        for bool_field in ("ok", "dry_run", "confirmed"):
            if not isinstance(tool.get(bool_field), bool):
                _workflow_error(
                    errors, field + "." + bool_field, "%s must be a boolean" % bool_field
                )
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
                _workflow_error(
                    errors,
                    field + ".advisory_only",
                    "external priors must declare advisory_only=true",
                )
            if prior.get("promotion_quality_evidence") is not False:
                _workflow_error(
                    errors,
                    field + ".promotion_quality_evidence",
                    "external priors must declare promotion_quality_evidence=false",
                )

    has_approved_promote = any(
        _is_approved_promote_tool(tool) for tool in tools_used if isinstance(tool, dict)
    )
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


def tool_workflow_packet_validate(args: dict) -> dict:
    packet = args.get("packet")
    if packet is None:
        raise ToolError("missing_argument", "missing required argument 'packet'")
    return _ok(validate_workflow_packet(packet))


FAMILY = ToolFamily(
    name="workflow",
    tools={
        "workflow_packet_validate": {
            "description": "Validate and normalize an operator-workflow/v1 result packet before using it as evidence.",
            "inputSchema": _schema(
                {
                    "packet": {"type": "object"},
                },
                required=["packet"],
            ),
            "handler": tool_workflow_packet_validate,
        },
    },
)
