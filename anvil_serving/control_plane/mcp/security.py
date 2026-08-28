"""Secret redaction and fail-closed MCP URL policy."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.parse
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any

from ...operator_output import redact
from ...paths import runtime_url
from ..controller.security import _is_safe_private_ip, _is_tailscale_ip
from .errors import ToolError


MEDIA_SCOPES = frozenset(
    {"media:read", "media:submit", "media:cancel", "media:cross-principal", "operator:media"}
)


@dataclass(frozen=True)
class CallerContext:
    principal: str
    scopes: frozenset[str]


_CALLER_CONTEXT: contextvars.ContextVar[CallerContext | None] = contextvars.ContextVar(
    "anvil_mcp_caller_context", default=None
)


def normalize_caller_context(value: Mapping[str, Any] | None) -> CallerContext | None:
    if value is None:
        return None
    principal = value.get("principal")
    scopes = value.get("scopes")
    if (
        not isinstance(principal, str)
        or not principal
        or len(principal) > 128
        or not isinstance(scopes, (list, tuple, set, frozenset))
        or any(not isinstance(scope, str) or not scope or len(scope) > 128 for scope in scopes)
    ):
        raise ToolError("invalid_caller_context", "authenticated caller context is invalid")
    return CallerContext(principal, frozenset(scopes))


@contextmanager
def caller_context(value: Mapping[str, Any] | None):
    normalized = normalize_caller_context(value)
    token = _CALLER_CONTEXT.set(normalized)
    try:
        yield normalized
    finally:
        _CALLER_CONTEXT.reset(token)


def require_scope(scope: str) -> CallerContext:
    caller = _CALLER_CONTEXT.get()
    if caller is None:
        raise ToolError("authentication_required", "authenticated media caller context is required")
    if scope not in caller.scopes and "operator:media" not in caller.scopes:
        raise ToolError(
            "scope_denied",
            "caller is not authorized for this media operation",
            {"requiredScope": scope},
        )
    return caller


ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
PROBE_API_KEY_ENVS = frozenset({"ANVIL_ROUTER_TOKEN"})
RAW_COMMAND_KEYS = frozenset(
    {"argv", "command", "command_payload", "payload", "shell", "stdin"}
)
RAW_SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "password",
        "private_key",
        "secret",
        "token",
    }
)
SECRET_ENV_RE = re.compile(
    r"(?:^|_)(?:API_KEY|AUTHORIZATION|CREDENTIAL|PASSWORD|PRIVATE_KEY|SECRET|TOKEN)(?:$|_)"
)
LOG_SECRET_PATTERNS = (
    re.compile(r"(?i)\b((?:authorization|x-api-key)\s*[:=]\s*(?:bearer\s+)?)([^\s]+)"),
    re.compile(r'(?i)("(?:authorization|x-api-key)"\s*:\s*"(?:bearer\s+)?)([^"]+)'),
    re.compile(r"(?i)('(?:authorization|x-api-key)'\s*:\s*'(?:bearer\s+)?)([^']+)"),
    re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/\-]{8,})"),
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|KEY)[A-Z0-9_]*\s*[=:]\s*)([^\s]+)"
    ),
    re.compile(
        r'(?i)("[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|KEY)[A-Z0-9_]*"\s*:\s*")([^"]+)'
    ),
    re.compile(
        r"(?i)('[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|KEY)[A-Z0-9_]*'\s*:\s*')([^']+)"
    ),
    re.compile(r"\b(sk-(?:proj-)?[A-Za-z0-9_-]{8,})\b"),
)


def environment_secrets() -> tuple[str, ...]:
    return tuple(
        value
        for name, value in os.environ.items()
        if value and SECRET_ENV_RE.search(name.upper())
    )


def redact_text(value: str) -> str:
    return redact(value, secrets=environment_secrets())


def redact_error_details(value: Any) -> Any:
    if isinstance(value, Mapping):
        safe = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            safe[str(key)] = (
                "<redacted>"
                if normalized in RAW_SECRET_KEYS
                or normalized in {"env", "environment", "environ"}
                else redact_error_details(item)
            )
        return safe
    if isinstance(value, (list, tuple)):
        return [redact_error_details(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_secret(value: Any, token: str) -> Any:
    if not token:
        return value
    if isinstance(value, str):
        return value.replace(token, "<redacted>")
    if isinstance(value, list):
        return [redact_secret(item, token) for item in value]
    if isinstance(value, dict):
        return {
            redact_secret(key, token): redact_secret(item, token)
            for key, item in value.items()
        }
    return value


def redact_log_text(value: str) -> str:
    out = value
    for pattern in LOG_SECRET_PATTERNS:
        if pattern.groups >= 2:
            out = pattern.sub(lambda match: match.group(1) + "<redacted>", out)
        else:
            out = pattern.sub("<redacted>", out)
    return out


def is_tailscale_v4(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return ip.version == 4 and _is_tailscale_ip(ip)


def is_safe_probe_ip(addr: str) -> bool:
    # Same private/tailscale CIDR classification as the controller's bind
    # safety check (control_plane/controller/security.py); shared here to
    # avoid a second copy of the CIDR list drifting out of sync.
    return _is_safe_private_ip(ipaddress.ip_address(addr))


def runtime_probe_url(base_url: str) -> str:
    """Map host-relative loopback to an explicit container-to-host alias."""

    try:
        return runtime_url(base_url)
    except ValueError as exc:
        raise ToolError("bad_loopback_alias", str(exc)) from exc


def safe_probe_url(base_url: str) -> str:
    base_url = runtime_probe_url(base_url)
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError("bad_base_url", "base_url must be an http(s) URL with a host")
    if parsed.username is not None or parsed.password is not None:
        raise ToolError(
            "bad_base_url",
            "base_url must not contain credentials; use api_key_env",
        )
    if parsed.query or parsed.fragment:
        raise ToolError(
            "bad_base_url",
            "base_url must not contain query strings or fragments",
        )
    try:
        parsed.port
    except ValueError as exc:
        raise ToolError(
            "bad_base_url",
            "base_url has an invalid port",
            {"error": str(exc)},
        ) from None
    if parsed.hostname.strip().lower() == "localhost":
        raise ToolError(
            "bad_base_url",
            "use 127.0.0.1 or a private/tailnet host, not localhost",
        )
    host = parsed.hostname
    try:
        if not is_safe_probe_ip(host):
            raise ToolError(
                "unsafe_base_url",
                "probe base_url must resolve to loopback, private, or tailnet addresses",
                {"host": host},
            )
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ToolError(
                "bad_base_url",
                "could not resolve base_url host",
                {"host": host, "error": str(exc)},
            )
        addrs = []
        for info in infos:
            try:
                addrs.append(info[4][0])
            except (IndexError, TypeError):
                pass
        if not addrs or any(not is_safe_probe_ip(addr) for addr in addrs):
            raise ToolError(
                "unsafe_base_url",
                "probe base_url must resolve only to loopback, private, or tailnet addresses",
                {"host": host, "addresses": addrs},
            )
    return base_url


def safe_controller_url(controller_url: str) -> str:
    return safe_probe_url(controller_url)
