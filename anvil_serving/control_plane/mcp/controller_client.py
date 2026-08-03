"""Authenticated, bounded MCP controller proxy requests."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .errors import ToolError
from .security import ENV_NAME_RE, redact_secret, safe_controller_url


MAX_REMOTE_CONTROLLER_RESPONSE_BYTES = 1024 * 1024
MAX_ERROR_BODY_BYTES = 4096


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urlopen_no_proxy_no_redirect(req, timeout=30):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirectHandler(),
    )
    return opener.open(req, timeout=timeout)


def http_error_details(
    exc: urllib.error.HTTPError,
    token: str = "",
) -> tuple[dict[str, Any], str]:
    details: dict[str, Any] = {"status": exc.code}
    if 300 <= exc.code < 400:
        location = exc.headers.get("Location") if exc.headers else None
        if location:
            details["location"] = location
        return redact_secret(details, token), ""

    raw = ""
    try:
        body = exc.read(MAX_ERROR_BODY_BYTES + 1)
    except Exception as body_exc:
        details["body_error"] = str(body_exc)
    else:
        if body:
            truncated = len(body) > MAX_ERROR_BODY_BYTES
            raw = body[:MAX_ERROR_BODY_BYTES].decode("utf-8", "replace")
            details["body"] = raw
            if truncated:
                details["body_truncated"] = True
    return redact_secret(details, token), raw


def resolve_controller_token(
    auth_env: str,
    environ: dict[str, str] | None = None,
) -> str:
    """Resolve a controller auth token from an env-var name, never a raw value."""

    if not auth_env or not ENV_NAME_RE.fullmatch(auth_env):
        raise ToolError(
            "bad_auth_env",
            "auth-env must name an ENV VAR matching ^[A-Z][A-Z0-9_]*$",
            {"auth_env": auth_env},
        )
    env = os.environ if environ is None else environ
    token = (env.get(auth_env) or "").strip()
    if not token:
        raise ToolError(
            "missing_auth_env",
            "auth env var is unset or empty",
            {"auth_env": auth_env},
        )
    return token


def controller_auth_headers(token: str) -> dict[str, str]:
    """Headers accepted by the controller/front-door token gate."""

    return {
        "Authorization": "Bearer " + token,
        "x-api-key": token,
    }


def _mcp_endpoint(controller_url: str) -> str:
    parsed = urllib.parse.urlsplit(controller_url)
    if parsed.path in ("", "/"):
        parsed = parsed._replace(path="/mcp")
    return urllib.parse.urlunsplit(parsed)


def _mcp_header_value(value: str) -> str:
    plain_ascii = (
        bool(value)
        and value == value.strip()
        and all(0x20 <= ord(char) <= 0x7E for char in value)
        and not (value.startswith("=?base64?") and value.endswith("?="))
    )
    if plain_ascii:
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return "=?base64?%s?=" % encoded


def remote_controller_request(
    controller_url: str,
    request: dict,
    token: str,
    *,
    timeout: int = 30,
    opener: Callable[..., Any] | None = None,
    max_response_bytes: int = MAX_REMOTE_CONTROLLER_RESPONSE_BYTES,
) -> dict:
    """POST one JSON-RPC request to a remote controller endpoint."""

    if not token:
        raise ToolError("missing_controller_token", "controller token is required")
    if (
        isinstance(max_response_bytes, bool)
        or not isinstance(max_response_bytes, int)
        or max_response_bytes < 1
        or max_response_bytes > MAX_REMOTE_CONTROLLER_RESPONSE_BYTES
    ):
        raise ToolError(
            "bad_argument",
            "max_response_bytes must be between 1 and %s"
            % MAX_REMOTE_CONTROLLER_RESPONSE_BYTES,
        )
    controller_url = _mcp_endpoint(safe_controller_url(controller_url))
    if opener is None:
        opener = urlopen_no_proxy_no_redirect
    body = json.dumps(request, separators=(",", ":")).encode("utf-8")
    method = request.get("method")
    params = request.get("params")
    metadata = params.get("_meta") if isinstance(params, dict) else None
    protocol_version = (
        metadata.get("io.modelcontextprotocol/protocolVersion")
        if isinstance(metadata, dict)
        else None
    )
    if not isinstance(method, str) or not isinstance(protocol_version, str):
        raise ToolError(
            "bad_mcp_request",
            "remote MCP requests require method and protocolVersion metadata",
        )
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": protocol_version,
        "Mcp-Method": method,
        **controller_auth_headers(token),
    }
    if method == "tools/call" and isinstance(params, dict):
        name = params.get("name")
        if isinstance(name, str):
            headers["Mcp-Name"] = _mcp_header_value(name)
    req = urllib.request.Request(
        controller_url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with opener(req, timeout=timeout) as response:
            bounded = response.read(max_response_bytes + 1)
            if not isinstance(bounded, bytes):
                raise ToolError(
                    "bad_controller_response",
                    "controller response body must be bytes",
                )
            if len(bounded) > max_response_bytes:
                raise ToolError(
                    "controller_response_too_large",
                    "controller response exceeds the bounded response limit",
                    {"max_response_bytes": max_response_bytes},
                )
            raw = bounded.decode("utf-8")
    except urllib.error.HTTPError as exc:
        details, _ = http_error_details(exc, token)
        raise ToolError(
            "controller_http_error",
            "controller returned HTTP %s" % exc.code,
            details,
        )
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            "controller_request_failed",
            redact_secret(str(exc), token),
            {"controller_url": controller_url},
        )
    try:
        parsed = json.loads(raw or "{}")
    except ValueError as exc:
        raise ToolError(
            "bad_controller_response",
            str(exc),
            {"controller_url": controller_url},
        )
    if not isinstance(parsed, dict):
        raise ToolError(
            "bad_controller_response",
            "controller response must be a JSON object",
        )
    return redact_secret(parsed, token)
