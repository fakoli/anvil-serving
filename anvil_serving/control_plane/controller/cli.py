"""Controller parser, status probe, and entrypoint coordination."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from typing import Any, Callable, Mapping, Optional, Sequence

from ...envfile import env_sources, resolve_env_value
from ...graceful import DEFAULT_DRAIN_SECONDS
from .catalog import _mcp_tool_name
from .errors import ControllerError
from .http import DEFAULT_MAX_BODY_BYTES
from .security import DEFAULT_AUTH_TOKEN_ENV, _json_dumps
from .server import DEFAULT_HOST, DEFAULT_PORT, serve
from .store import DEFAULT_IDEMPOTENCY_DB_PATH


DEFAULT_STATUS_URL = "http://127.0.0.1:8765"
DEFAULT_STATUS_MAX_RESPONSE_BYTES = 64 * 1024


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving controller")
    subparsers = parser.add_subparsers(dest="command")
    serve_parser = subparsers.add_parser("serve", help="start the HTTP controller")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_parser.add_argument(
        "--auth-token-env",
        default=DEFAULT_AUTH_TOKEN_ENV,
        help=(
            "environment variable containing the controller token (default: ANVIL_CONTROLLER_TOKEN)"
        ),
    )
    serve_parser.add_argument(
        "--allow-public-bind",
        action="store_true",
        help="allow a public or wildcard bind; still requires --auth-token-env to be set",
    )
    serve_parser.add_argument(
        "--allow-operation",
        action="append",
        default=None,
        help="restrict the controller to a declared operation (repeatable)",
    )
    serve_parser.add_argument(
        "--state-db",
        default=DEFAULT_IDEMPOTENCY_DB_PATH,
        help="durable controller operation-state database path",
    )
    serve_parser.add_argument(
        "--drain-seconds",
        type=float,
        default=DEFAULT_DRAIN_SECONDS,
        help="bounded in-flight drain budget on SIGTERM (default: 20)",
    )
    serve_parser.add_argument(
        "--audit-log",
        default=None,
        help=(
            "durable JSONL audit sink (tees to stderr); place it on the "
            "operation-state volume (default: stderr only)"
        ),
    )
    serve_parser.add_argument(
        "--node-id",
        default=None,
        help=(
            "topology host id this controller asserts on /health; clients "
            "with a matching transport expected_node verify it before dispatch"
        ),
    )
    serve_parser.add_argument(
        "--authorization-policy",
        default=None,
        help="optional local scoped-authorization policy for new controller surfaces",
    )
    serve_parser.add_argument(
        "--allow-unauthenticated-loopback",
        action="store_true",
        help=(
            "permit an unauthenticated strictly-loopback bind (development "
            "only; non-loopback binds always require the token)"
        ),
    )
    status_parser = subparsers.add_parser("status", help="probe controller health")
    status_parser.add_argument("--url", default=DEFAULT_STATUS_URL)
    status_parser.add_argument("--auth-token-env", default=DEFAULT_AUTH_TOKEN_ENV)
    status_parser.add_argument("--timeout", type=float, default=5.0)
    status_parser.add_argument(
        "--max-response-bytes", type=int, default=DEFAULT_STATUS_MAX_RESPONSE_BYTES
    )
    status_parser.add_argument(
        "--require-operation",
        action="append",
        default=[],
        help="require a declared controller capability (repeatable)",
    )
    return parser


def _status_payload(
    url: str,
    path: str,
    *,
    token: str,
    timeout: float,
    max_response_bytes: int,
    _open: Callable[..., Any],
) -> dict[str, Any]:
    request = urllib.request.Request(
        url.rstrip("/") + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    with _open(request, timeout=timeout) as response:
        raw = response.read(max_response_bytes + 1)
    if not isinstance(raw, bytes):
        raise ValueError("controller status response body must be bytes")
    if len(raw) > max_response_bytes:
        raise ValueError("controller status response exceeds the configured limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("controller status response must be an object")
    return payload


def status(
    url: str = DEFAULT_STATUS_URL,
    *,
    auth_token_env: str = DEFAULT_AUTH_TOKEN_ENV,
    timeout: float = 5.0,
    max_response_bytes: int = DEFAULT_STATUS_MAX_RESPONSE_BYTES,
    required_operations: Sequence[str] = (),
    environment: Optional[Mapping[str, str]] = None,
    _open: Callable[..., Any] = urllib.request.urlopen,
) -> int:
    """Probe bounded authenticated controller health and capabilities."""
    if timeout <= 0 or timeout > 60:
        print("controller status: timeout must be between 0 and 60 seconds", file=sys.stderr)
        return 2
    if max_response_bytes < 1 or max_response_bytes > DEFAULT_MAX_BODY_BYTES:
        print(
            "controller status: max response bytes must be between 1 and %s"
            % DEFAULT_MAX_BODY_BYTES,
            file=sys.stderr,
        )
        return 2
    token, _source = resolve_env_value(auth_token_env, env=environment)
    if not token:
        print(
            "controller status: token %s is unset or empty; checked %s"
            % (auth_token_env, ", ".join(env_sources(auth_token_env))),
            file=sys.stderr,
        )
        return 3
    try:
        health = _status_payload(
            url,
            "/health",
            token=token,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            _open=_open,
        )
        if health.get("status") != "ok" or health.get("service") != "anvil-serving-controller":
            raise ValueError("controller health identity is invalid")
        capabilities = _status_payload(
            url,
            "/tools/list",
            token=token,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            _open=_open,
        )
        tools = capabilities.get("tools")
        if not isinstance(tools, list):
            raise ValueError("controller capability response has no tools list")
        tool_names = sorted(
            tool["name"]
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        )
        if len(tool_names) != len(tools) or len(set(tool_names)) != len(tool_names):
            raise ValueError("controller capability response contains invalid tool declarations")
        required = {_mcp_tool_name(name) for name in required_operations}
        missing = sorted(required - {_mcp_tool_name(name) for name in tool_names})
        if missing:
            raise ValueError("controller is missing required operations: %s" % ", ".join(missing))
    except (OSError, ValueError) as exc:
        print("controller status: %s" % exc, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "service": "anvil-serving-controller",
                "capabilities": {"tool_count": len(tool_names), "tools": tool_names},
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        return 2
    args = parser.parse_args(argv)
    if args.command == "serve":
        try:
            return serve(
                host=args.host,
                port=args.port,
                auth_token_env=args.auth_token_env,
                allow_public_bind=args.allow_public_bind,
                allow_unauthenticated_loopback=args.allow_unauthenticated_loopback,
                allowed_operations=args.allow_operation,
                idempotency_db_path=args.state_db,
                drain_seconds=args.drain_seconds,
                audit_log_path=args.audit_log,
                node_id=args.node_id,
                authorization_policy=args.authorization_policy,
            )
        except ControllerError as exc:
            print(
                _json_dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        },
                    }
                ),
                file=sys.stderr,
            )
            return 2
    if args.command == "status":
        return status(
            args.url,
            auth_token_env=args.auth_token_env,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            required_operations=args.require_operation,
        )
    parser.print_help(sys.stderr)
    return 2
