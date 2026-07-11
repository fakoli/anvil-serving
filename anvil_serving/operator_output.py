"""Stable CLI output envelopes and human rendering for operator commands.

The public CLI is the only caller of this module.  MCP and controller callers
keep their protocol-specific envelopes and can attach the same context through
``context_from_plan``.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .targets import ExecutionPlan


EXIT_CODES = {
    "success": 0,
    "execution": 1,
    "usage": 2,
    "safety": 3,
    "transport": 4,
    "partial": 5,
}
ENVELOPE_FIELDS = ("ok", "command", "context", "data", "warnings", "error")
CONTEXT_FIELDS = (
    "command",
    "topology",
    "overlay",
    "command_host",
    "command_runtime",
    "target",
    "execution_host",
    "execution_runtime",
    "resource_host",
    "resource_runtime",
    "resource",
    "transport",
    "controller_endpoint",
    "controller_endpoint_kind",
    "resource_endpoint",
    "resource_endpoint_kind",
    "gpu_role",
    "gpu_uuid",
)
_ENVIRONMENT_KEYS = frozenset({"env", "environment", "environ"})
_PRIVATE_PAYLOAD_KEYS = frozenset({"argv", "command", "command_payload", "payload", "stdin"})
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[^\s'\"\\]+"),
    re.compile(r"(?i)\b((?:authorization|x-api-key)\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(
        r"(?i)\b((?:access[_-]?key|api[_-]?key|client[_-]?secret|private[_-]?key|"
        r"secret[_-]?access[_-]?key|session[_-]?token)\s*[:=]\s*)[^\s,;]+"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{8,}\b"),
)
_REDACTED = "<redacted>"


class OperatorError(Exception):
    """A classified error that can be emitted as one CLI envelope."""

    exit_class = "execution"
    code = "execution_failed"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = code or self.code
        self.details = dict(details or {})
        self.exit_code = EXIT_CODES[self.exit_class]
        super().__init__(message)


class UsageError(OperatorError):
    exit_class = "usage"
    code = "usage_error"


class SafetyError(OperatorError):
    exit_class = "safety"
    code = "safety_refused"


class TransportError(OperatorError):
    exit_class = "transport"
    code = "transport_failed"


class PartialResultError(OperatorError):
    exit_class = "partial"
    code = "partial_completion"


ExecutionError = OperatorError
DomainError = OperatorError


@dataclass(frozen=True)
class OutputOptions:
    """Global output switches shared by the CLI parser and renderers."""

    json_mode: bool = False
    quiet: bool = False
    verbose: bool = False

    def __post_init__(self) -> None:
        if self.quiet and self.verbose:
            raise UsageError("--quiet and --verbose cannot be used together")


@dataclass(frozen=True)
class HumanOutput:
    """Separated primary and diagnostic streams for a human CLI invocation."""

    stdout: str
    stderr: str


def context_from_plan(plan: ExecutionPlan | Mapping[str, Any]) -> dict[str, Any]:
    """Return the fixed, redacted execution context shape for CLI envelopes."""
    raw = plan.as_dict() if isinstance(plan, ExecutionPlan) else dict(plan)
    return {field: redact(raw.get(field)) for field in CONTEXT_FIELDS}


def success_envelope(
    command: str,
    context: ExecutionPlan | Mapping[str, Any] | None,
    data: Any = None,
    *,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the stable D013 success envelope."""
    return _envelope(command, context, data, warnings, None)


def error_envelope(
    command: str,
    context: ExecutionPlan | Mapping[str, Any] | None,
    error: BaseException,
    *,
    data: Any = None,
    warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """Build the stable D013 error envelope from a classified exception."""
    classified = classify_error(error)
    error_data: dict[str, Any] = {
        "class": classified.exit_class,
        "code": classified.code,
        "message": redact(classified.message),
        "details": redact(classified.details),
    }
    return _envelope(command, context, data, warnings, error_data)


def classify_error(error: BaseException) -> OperatorError:
    """Convert known exit metadata and unknown failures into a CLI error class."""
    if isinstance(error, OperatorError):
        return error
    execution_state = getattr(error, "execution_state", None)
    may_have_executed = getattr(error, "may_have_executed", False) is True
    exit_class = getattr(error, "exit_class", "execution")
    if execution_state == "partial_result" or may_have_executed:
        exit_class = "partial"
    if exit_class not in EXIT_CODES or exit_class == "success":
        exit_class = "execution"
    error_type = {
        "execution": OperatorError,
        "usage": UsageError,
        "safety": SafetyError,
        "transport": TransportError,
        "partial": PartialResultError,
    }[exit_class]
    details = getattr(error, "metadata", None)
    if not isinstance(details, Mapping):
        details = getattr(error, "details", {})
    if not isinstance(details, Mapping):
        details = {}
    rendered_details = dict(details)
    if isinstance(execution_state, str):
        rendered_details["execution_state"] = execution_state
    if hasattr(error, "may_have_executed"):
        rendered_details["may_have_executed"] = may_have_executed
    code = getattr(error, "code", None)
    return error_type(
        str(error),
        code=code if isinstance(code, str) else None,
        details=rendered_details,
    )


def exit_code(envelope: Mapping[str, Any]) -> int:
    """Return the D014 process exit code for a D013 envelope."""
    if envelope.get("ok") is True:
        return EXIT_CODES["success"]
    error = envelope.get("error")
    if isinstance(error, Mapping) and error.get("class") in EXIT_CODES:
        return EXIT_CODES[error["class"]]
    return EXIT_CODES["execution"]


def render_json(envelope: Mapping[str, Any]) -> str:
    """Serialize one redacted, deterministic envelope for stdout."""
    return json.dumps(
        _bounded_envelope(envelope), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def render_human(
    envelope: Mapping[str, Any],
    *,
    options: OutputOptions | None = None,
    data_renderer: Callable[[Any], str] | None = None,
) -> HumanOutput:
    """Render bounded results to stdout and all diagnostics to stderr."""
    options = options or OutputOptions()
    bounded = _bounded_envelope(envelope)
    if options.json_mode:
        return HumanOutput(render_json(bounded) + "\n", "")

    context = bounded["context"]
    warnings = bounded["warnings"]
    if bounded["ok"]:
        stdout_parts: list[str] = []
        if not options.quiet and (_is_remote(context) or options.verbose):
            stdout_parts.append(_context_header(context))
        if not options.quiet and bounded["data"] is not None:
            render = data_renderer or _default_data_renderer
            stdout_parts.append(render(bounded["data"]))
        stderr = _diagnostics(warnings)
        return HumanOutput(_join_lines(stdout_parts), stderr)

    error = bounded["error"]
    assert isinstance(error, Mapping)
    stderr_parts = []
    if _is_remote(context):
        stderr_parts.append(_context_header(context))
    stderr_parts.append(str(error["message"]))
    stderr_parts.extend(warnings)
    return HumanOutput("", _join_lines(stderr_parts))


def redact(value: Any, *, secrets: Sequence[str] = ()) -> Any:
    """Return a recursive copy with secrets, environment values, and payloads removed."""
    known_secrets = tuple(secret for secret in secrets if secret)
    return _redact(value, known_secrets)


def _envelope(
    command: str,
    context: ExecutionPlan | Mapping[str, Any] | None,
    data: Any,
    warnings: Sequence[str],
    error: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "ok": error is None,
        "command": redact(command),
        "context": context_from_plan(context or {}),
        "data": redact(data),
        "warnings": [redact(str(warning)) for warning in warnings],
        "error": error,
    }


def _bounded_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    context = envelope.get("context")
    if not isinstance(context, Mapping):
        context = {}
    error = envelope.get("error")
    if not isinstance(error, Mapping):
        error = None
    warnings = envelope.get("warnings")
    if not isinstance(warnings, Sequence) or isinstance(warnings, (str, bytes)):
        warnings = ()
    return {
        "ok": error is None and envelope.get("ok") is True,
        "command": redact(str(envelope.get("command", ""))),
        "context": context_from_plan(context),
        "data": redact(envelope.get("data")),
        "warnings": [redact(str(warning)) for warning in warnings],
        "error": _error_payload(error),
    }


def _error_payload(error: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if error is None:
        return None
    error_class = error.get("class")
    if error_class not in EXIT_CODES or error_class == "success":
        error_class = "execution"
    return {
        "class": error_class,
        "code": redact(str(error.get("code", "execution_failed"))),
        "message": redact(str(error.get("message", "command failed"))),
        "details": redact(error.get("details", {})),
    }


def _redact(value: Any, known_secrets: Sequence[str]) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            safe_key = _redact(key_text, known_secrets)
            normalized = key_text.lower().replace("-", "_")
            if normalized in _ENVIRONMENT_KEYS:
                result[safe_key] = (
                    {str(name): _REDACTED for name in item}
                    if isinstance(item, Mapping)
                    else _REDACTED
                )
            elif normalized in _PRIVATE_PAYLOAD_KEYS or _is_secret_key(key_text):
                result[safe_key] = _REDACTED
            else:
                result[safe_key] = _redact(item, known_secrets)
        return result
    if isinstance(value, (list, tuple)):
        return [_redact(item, known_secrets) for item in value]
    if isinstance(value, (set, frozenset)):
        rendered = [_redact(item, known_secrets) for item in value]
        return sorted(
            rendered,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), allow_nan=False
            ),
        )
    if isinstance(value, str):
        text = value
        for secret in known_secrets:
            text = text.replace(secret, _REDACTED)
        for pattern in _SECRET_TEXT_PATTERNS:
            text = pattern.sub(
                lambda match: match.group(1) + _REDACTED if match.lastindex else _REDACTED, text
            )
        return text
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("operator output must contain finite numbers")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact(str(value), known_secrets)


def _is_secret_key(key: str) -> bool:
    parts = tuple(part for part in re.split(r"[^a-z0-9]+", key.lower()) if part)
    compact = "".join(parts)
    if set(parts) & {"authorization", "credential", "credentials", "password", "secret", "token"}:
        return True
    return any(
        shape in compact
        for shape in (
            "accesskey",
            "accesstoken",
            "apikey",
            "authorization",
            "bearertoken",
            "clientsecret",
            "privatekey",
            "refreshtoken",
            "secretaccesskey",
            "sessiontoken",
        )
    )


def _is_remote(context: Mapping[str, Any]) -> bool:
    return context.get("transport") in {"controller", "ssh"}


def _context_header(context: Mapping[str, Any]) -> str:
    fields = (
        ("topology", context.get("topology")),
        ("command", context.get("command")),
        ("execution", context.get("execution_host")),
        ("transport", context.get("transport")),
        ("controller", context.get("controller_endpoint")),
        ("resource", context.get("resource_endpoint")),
    )
    values = [f"{label}={value}" for label, value in fields if value is not None]
    return "Context: " + " ".join(values)


def _default_data_renderer(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, sort_keys=True, indent=2, allow_nan=False)


def _diagnostics(warnings: Sequence[str]) -> str:
    return _join_lines(list(warnings)) if warnings else ""


def _join_lines(parts: Sequence[str]) -> str:
    return "\n".join(part for part in parts if part) + ("\n" if any(parts) else "")
