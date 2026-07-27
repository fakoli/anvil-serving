"""Authenticated, bounded MCP controller proxy requests."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from .errors import ToolError
from .security import ENV_NAME_RE, redact_secret, safe_controller_url


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


def remote_controller_request(
    controller_url: str,
    request: dict,
    token: str,
    *,
    timeout: int = 30,
    opener: Callable[..., Any] | None = None,
) -> dict:
    """POST one JSON-RPC request to a remote controller endpoint."""

    if not token:
        raise ToolError("missing_controller_token", "controller token is required")
    controller_url = safe_controller_url(controller_url)
    if opener is None:
        opener = urlopen_no_proxy_no_redirect
    body = json.dumps(request, separators=(",", ":")).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **controller_auth_headers(token),
    }
    req = urllib.request.Request(
        controller_url,
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with opener(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        details, _ = http_error_details(exc, token)
        raise ToolError(
            "controller_http_error",
            "controller returned HTTP %s" % exc.code,
            details,
        )
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
