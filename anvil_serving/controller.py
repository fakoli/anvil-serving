"""HTTP controller transport for the anvil-serving MCP control plane.

The controller is a management-plane HTTP wrapper around ``anvil_serving.mcp``.
It deliberately reuses ``mcp.list_tools()`` and ``mcp.call_tool()`` so stdio MCP
and HTTP controller callers see the same tool schemas and tool semantics.

This module is stdlib-only and safe to test without a long-running listener:
``make_server()`` returns an unstarted server, and both the server class and MCP
tool functions are injectable.
"""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import re
import socket
import sys
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Optional, Sequence

from . import mcp


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_AUTH_TOKEN_ENV = "ANVIL_CONTROLLER_TOKEN"
DEFAULT_MAX_BODY_BYTES = 1024 * 1024

_MAX_BODY_BYTES = int(
    os.environ.get("ANVIL_CONTROLLER_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES))
)
_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_WILDCARD_BINDS = {"", "0.0.0.0", "::"}
_TOKEN_HEADER = "x-api-key"
_REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
AuditLogger = Callable[[dict[str, Any]], None]
ListToolsFunc = Callable[[], list[dict]]
CallToolFunc = Callable[[str, Optional[dict]], dict]


class ControllerError(Exception):
    """Structured controller failure rendered as JSON."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


class BindSafetyError(ControllerError):
    """Raised when a requested bind address violates controller safety rules."""


@dataclass(frozen=True)
class BindAssessment:
    """Result of classifying a controller bind address."""

    host: str
    addresses: tuple[str, ...]
    loopback: bool
    private: bool
    tailscale: bool
    public: bool
    requires_auth: bool


class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _redact_secret(value: Any, secret: Optional[str]) -> Any:
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "<redacted>")
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            _redact_secret(key, secret): _redact_secret(item, secret)
            for key, item in value.items()
        }
    return value


def _default_audit_logger(record: dict[str, Any]) -> None:
    sys.stderr.write(_json_dumps(record) + "\n")
    sys.stderr.flush()


def _is_tailscale_ip(addr: IPAddress) -> bool:
    if addr.version == 4:
        return addr in _TAILSCALE_V4
    return addr in _TAILSCALE_V6


def _is_safe_private_ip(addr: IPAddress) -> bool:
    return bool(addr.is_loopback or addr.is_private or _is_tailscale_ip(addr))


def _resolve_bind_ips(
    host: str,
    *,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
) -> tuple[IPAddress, ...]:
    if host in _WILDCARD_BINDS:
        return ()
    if host.strip().lower() == "localhost":
        raise BindSafetyError(
            "localhost_not_allowed",
            "use 127.0.0.1 or ::1 instead of localhost",
            status=400,
            details={"host": host},
        )
    try:
        return (ipaddress.ip_address(host),)
    except ValueError:
        pass

    getaddrinfo = resolver or socket.getaddrinfo
    try:
        infos = getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BindSafetyError(
            "bind_host_unresolved",
            "could not resolve bind host",
            status=400,
            details={"host": host, "error": str(exc)},
        ) from exc

    addrs: list[IPAddress] = []
    for info in infos:
        try:
            raw_addr = info[4][0]
            addrs.append(ipaddress.ip_address(raw_addr))
        except (IndexError, TypeError, ValueError):
            continue
    if not addrs:
        raise BindSafetyError(
            "bind_host_unresolved",
            "could not resolve bind host to an IP address",
            status=400,
            details={"host": host},
        )
    # Preserve resolver order while removing duplicates.
    seen: set[str] = set()
    unique: list[IPAddress] = []
    for addr in addrs:
        rendered = str(addr)
        if rendered not in seen:
            unique.append(addr)
            seen.add(rendered)
    return tuple(unique)


def _env_has_token(auth_token_env: Optional[str], env: Mapping[str, str]) -> bool:
    if not auth_token_env:
        return False
    return bool(env.get(auth_token_env))


def validate_bind_safety(
    host: str,
    *,
    allow_public_bind: bool = False,
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    env: Optional[Mapping[str, str]] = None,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
) -> BindAssessment:
    """Validate controller bind safety and return the bind classification.

    Allowed without ``allow_public_bind``:
    - loopback IPs: ``127.0.0.1`` and ``::1``
    - private addresses such as RFC1918 IPv4 or IPv6 ULA
    - Tailscale IPv4 CGNAT addresses in ``100.64.0.0/10``

    Public and wildcard binds are refused unless ``allow_public_bind`` is true.
    Any non-loopback bind, including private and Tailscale binds, requires an
    auth token environment variable whose value is set.
    """
    effective_env = os.environ if env is None else env
    addrs = _resolve_bind_ips(host, resolver=resolver)

    if host in _WILDCARD_BINDS:
        loopback = False
        private = False
        tailscale = False
        public = True
        addresses: tuple[str, ...] = (host,)
    else:
        loopback = all(addr.is_loopback for addr in addrs)
        private = all(addr.is_private for addr in addrs)
        tailscale = any(_is_tailscale_ip(addr) for addr in addrs)
        public = any(not _is_safe_private_ip(addr) for addr in addrs)
        addresses = tuple(str(addr) for addr in addrs)

    if public and not allow_public_bind:
        raise BindSafetyError(
            "public_bind_refused",
            "refusing to bind controller to a public address without --allow-public-bind",
            status=400,
            details={"host": host, "addresses": list(addresses)},
        )

    requires_auth = not loopback
    if requires_auth and not _env_has_token(auth_token_env, effective_env):
        raise BindSafetyError(
            "auth_token_required",
            "non-loopback controller binds require an auth token environment variable",
            status=400,
            details={
                "host": host,
                "auth_token_env": auth_token_env or None,
                "addresses": list(addresses),
            },
        )

    return BindAssessment(
        host=host,
        addresses=addresses,
        loopback=loopback,
        private=private,
        tailscale=tailscale,
        public=public,
        requires_auth=requires_auth,
    )


def resolve_auth_token(
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    *,
    env: Optional[Mapping[str, str]] = None,
    required: bool = False,
) -> Optional[str]:
    effective_env = os.environ if env is None else env
    if not auth_token_env:
        if required:
            raise ControllerError(
                "auth_token_required",
                "auth token environment variable name is required",
                status=400,
            )
        return None
    token = effective_env.get(auth_token_env)
    if token:
        return token
    if required:
        raise ControllerError(
            "auth_token_missing",
            "auth token environment variable is not set",
            status=400,
            details={"auth_token_env": auth_token_env},
        )
    return None


def _extract_request_token(headers) -> Optional[str]:
    auth_header = headers.get("Authorization")
    if auth_header:
        scheme, _, value = auth_header.partition(" ")
        if scheme.strip().lower() == "bearer" and value.strip():
            return value.strip()
        return None
    api_key = headers.get(_TOKEN_HEADER)
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def _safe_request_id(value: Optional[str]) -> str:
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _error_body(
    code: str,
    message: str,
    *,
    request_id: str,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "request_id": request_id,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def _response_with_request_id(envelope: dict, request_id: str) -> dict:
    if "request_id" in envelope:
        return dict(envelope)
    response = dict(envelope)
    response["request_id"] = request_id
    return response


def _tool_result(envelope: dict) -> dict:
    return {
        "content": [{"type": "text", "text": _json_dumps(envelope)}],
        "structuredContent": envelope,
        "isError": not envelope.get("ok", False),
    }


def _server_class_for_host(host: str):
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return ThreadingHTTPServer
    if addr.version == 6:
        return IPv6ThreadingHTTPServer
    return ThreadingHTTPServer


def make_handler(
    *,
    list_tools_func: ListToolsFunc = mcp.list_tools,
    call_tool_func: CallToolFunc = mcp.call_tool,
    auth_token: Optional[str] = None,
    audit_logger: Optional[AuditLogger] = None,
    max_body_bytes: int = _MAX_BODY_BYTES,
):
    """Build a request handler class for controller tests or ``make_server``."""

    audit = audit_logger or _default_audit_logger

    class ControllerHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "anvil-controller"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _remote_addr(self) -> str:
            try:
                return str(self.client_address[0])
            except Exception:
                return ""

        def _authenticated(self) -> bool:
            if auth_token is None:
                return True
            supplied = _extract_request_token(self.headers)
            if supplied is None:
                return False
            return hmac.compare_digest(
                supplied.encode("utf-8"), auth_token.encode("utf-8")
            )

        def _send_json(
            self,
            status: int,
            obj: dict[str, Any],
            *,
            request_id: str,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            payload = _json_dumps(_redact_secret(obj, auth_token)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header(_REQUEST_ID_HEADER, request_id)
            self.send_header("Cache-Control", "no-store")
            if self.close_connection:
                self.send_header("Connection", "close")
            if extra_headers:
                for name, value in extra_headers.items():
                    self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _send_no_content(self, *, request_id: str) -> None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header(_REQUEST_ID_HEADER, request_id)
            self.send_header("Cache-Control", "no-store")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()

        def _send_error_json(
            self,
            status: int,
            code: str,
            message: str,
            *,
            request_id: str,
            details: Optional[dict[str, Any]] = None,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            self._send_json(
                status,
                _error_body(code, message, request_id=request_id, details=details),
                request_id=request_id,
                extra_headers=extra_headers,
            )

        def _audit(
            self,
            *,
            request_id: str,
            operation: str,
            status: int,
            started: float,
            ok: bool,
            tool: Optional[str] = None,
            dry_run: Optional[bool] = None,
            confirm: Optional[bool] = None,
            error_code: Optional[str] = None,
        ) -> None:
            record: dict[str, Any] = {
                "request_id": request_id,
                "operation": operation,
                "tool": tool,
                "dry_run": dry_run,
                "confirm": confirm,
                "status": status,
                "ok": ok,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "remote_addr": self._remote_addr(),
            }
            if error_code is not None:
                record["error_code"] = error_code
            try:
                audit(record)
            except Exception:
                pass

        def _read_json_body(self, *, request_id: str) -> dict[str, Any]:
            if self.headers.get_all("Transfer-Encoding"):
                self.close_connection = True
                raise ControllerError(
                    "chunked_not_supported",
                    "chunked request bodies are not supported",
                    status=411,
                )
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return {}
            if not raw_length.isdigit():
                self.close_connection = True
                raise ControllerError(
                    "bad_content_length",
                    "Content-Length must be a non-negative integer",
                    status=400,
                )
            length = int(raw_length)
            if length > max_body_bytes:
                self.close_connection = True
                raise ControllerError(
                    "payload_too_large",
                    "request body is too large",
                    status=413,
                    details={"max_body_bytes": max_body_bytes},
                )
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                obj = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ControllerError(
                    "invalid_json",
                    "request body must be valid UTF-8 JSON",
                    status=400,
                    details={"error": str(exc)},
                ) from exc
            if not isinstance(obj, dict):
                raise ControllerError(
                    "bad_request",
                    "request body must be a JSON object",
                    status=400,
                )
            return obj

        def _jsonrpc_response(self, body: dict[str, Any]) -> Optional[dict[str, Any]]:
            if "id" not in body:
                return None
            req_id = body.get("id")
            method = body.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": mcp.PROTOCOL_VERSION,
                    "serverInfo": mcp.SERVER_INFO,
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {"tools": list_tools_func()}
            elif method == "tools/call":
                params = body.get("params") or {}
                if not isinstance(params, dict):
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": "params must be an object",
                        },
                    }
                result = _tool_result(call_tool_func(
                    params.get("name"),
                    params.get("arguments") or {},
                ))
            elif method == "notifications/initialized":
                return None
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            if req_id is None:
                return None
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        def _auth_or_401(self, *, request_id: str) -> bool:
            if self._authenticated():
                return True
            self.close_connection = True
            self._send_error_json(
                401,
                "authentication_error",
                "invalid or missing API key",
                request_id=request_id,
            )
            return False

        def do_GET(self) -> None:
            request_id = _safe_request_id(self.headers.get(_REQUEST_ID_HEADER))
            started = time.perf_counter()
            route = self.path.split("?", 1)[0].rstrip("/") or "/"
            operation = route.lstrip("/") or "root"
            status = 500
            ok = False
            error_code: Optional[str] = None
            try:
                if not self._auth_or_401(request_id=request_id):
                    status = 401
                    error_code = "authentication_error"
                    return
                if route in ("/health", "/healthz"):
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        {
                            "status": "ok",
                            "service": "anvil-serving-controller",
                            "request_id": request_id,
                        },
                        request_id=request_id,
                    )
                    return
                if route == "/tools/list":
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        {"tools": list_tools_func(), "request_id": request_id},
                        request_id=request_id,
                    )
                    return
                if route == "/tools/call":
                    status = 405
                    error_code = "method_not_allowed"
                    self._send_error_json(
                        status,
                        error_code,
                        "this route only accepts POST requests",
                        request_id=request_id,
                        extra_headers={"Allow": "POST"},
                    )
                    return
                status = 404
                error_code = "not_found"
                self._send_error_json(
                    status,
                    error_code,
                    "unknown controller route",
                    request_id=request_id,
                    details={"path": route},
                )
            except Exception:
                status = 500
                ok = False
                error_code = "internal_error"
                self._send_error_json(
                    status,
                    error_code,
                    "internal error",
                    request_id=request_id,
                )
            finally:
                self._audit(
                    request_id=request_id,
                    operation=operation,
                    status=status,
                    started=started,
                    ok=ok,
                    error_code=error_code,
                )

        def do_POST(self) -> None:
            request_id = _safe_request_id(self.headers.get(_REQUEST_ID_HEADER))
            started = time.perf_counter()
            route = self.path.split("?", 1)[0].rstrip("/") or "/"
            operation = route.lstrip("/") or "root"
            status = 500
            ok = False
            tool: Optional[str] = None
            dry_run: Optional[bool] = None
            confirm: Optional[bool] = None
            error_code: Optional[str] = None
            try:
                if not self._auth_or_401(request_id=request_id):
                    status = 401
                    error_code = "authentication_error"
                    return
                if route == "/tools/list":
                    self._read_json_body(request_id=request_id)
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        {"tools": list_tools_func(), "request_id": request_id},
                        request_id=request_id,
                    )
                    return
                if route in ("/", "/mcp"):
                    body = self._read_json_body(request_id=request_id)
                    if "id" in body and body.get("method") == "tools/call":
                        params = body.get("params") or {}
                        if isinstance(params, dict):
                            raw_arguments = params.get("arguments") or {}
                            if isinstance(raw_arguments, dict):
                                tool = params.get("name") if isinstance(params.get("name"), str) else None
                                if isinstance(raw_arguments.get("dry_run"), bool):
                                    dry_run = raw_arguments["dry_run"]
                                if isinstance(raw_arguments.get("confirm"), bool):
                                    confirm = raw_arguments["confirm"]
                    response = self._jsonrpc_response(body)
                    status = 200
                    ok = response is None or "error" not in response
                    if response is not None:
                        self._send_json(status, response, request_id=request_id)
                    else:
                        status = 204
                        self._send_no_content(request_id=request_id)
                    return

                if route != "/tools/call":
                    status = 405 if route in ("/health", "/healthz") else 404
                    error_code = "method_not_allowed" if route in ("/health", "/healthz") else "not_found"
                    self._send_error_json(
                        status,
                        error_code,
                        (
                            "this route only accepts GET requests"
                            if route in ("/health", "/healthz")
                            else "unknown controller route"
                        ),
                        request_id=request_id,
                        details={} if route in ("/health", "/healthz") else {"path": route},
                        extra_headers={"Allow": "GET"} if route in ("/health", "/healthz") else None,
                    )
                    return

                body = self._read_json_body(request_id=request_id)
                raw_name = body.get("name")
                if not isinstance(raw_name, str) or not raw_name:
                    raise ControllerError(
                        "bad_request",
                        "tools/call requires a non-empty string 'name'",
                        status=400,
                    )
                raw_arguments = body.get("arguments", {})
                if raw_arguments is None:
                    raw_arguments = {}
                if not isinstance(raw_arguments, dict):
                    raise ControllerError(
                        "bad_request",
                        "tools/call 'arguments' must be a JSON object",
                        status=400,
                    )

                tool = raw_name
                if isinstance(raw_arguments.get("dry_run"), bool):
                    dry_run = raw_arguments["dry_run"]
                if isinstance(raw_arguments.get("confirm"), bool):
                    confirm = raw_arguments["confirm"]

                envelope = call_tool_func(raw_name, raw_arguments)
                status = 200
                ok = bool(envelope.get("ok"))
                if not ok:
                    err = envelope.get("error") if isinstance(envelope, dict) else None
                    if isinstance(err, dict) and isinstance(err.get("code"), str):
                        error_code = err["code"]
                self._send_json(
                    status,
                    _response_with_request_id(envelope, request_id),
                    request_id=request_id,
                )
            except ControllerError as exc:
                status = exc.status
                ok = False
                error_code = exc.code
                self._send_error_json(
                    status,
                    exc.code,
                    exc.message,
                    request_id=request_id,
                    details=exc.details,
                )
            except Exception:
                status = 500
                ok = False
                error_code = "internal_error"
                self._send_error_json(
                    status,
                    error_code,
                    "internal error",
                    request_id=request_id,
                )
            finally:
                self._audit(
                    request_id=request_id,
                    operation=operation,
                    status=status,
                    started=started,
                    ok=ok,
                    tool=tool,
                    dry_run=dry_run,
                    confirm=confirm,
                    error_code=error_code,
                )

        def _method_not_allowed(self) -> None:
            request_id = _safe_request_id(self.headers.get(_REQUEST_ID_HEADER))
            started = time.perf_counter()
            route = self.path.split("?", 1)[0].rstrip("/") or "/"
            operation = route.lstrip("/") or "root"
            status = 405
            error_code = "method_not_allowed"
            try:
                if not self._auth_or_401(request_id=request_id):
                    status = 401
                    error_code = "authentication_error"
                    return
                self._send_error_json(
                    status,
                    error_code,
                    "method not allowed",
                    request_id=request_id,
                    extra_headers={"Allow": "GET, POST"},
                )
            finally:
                self._audit(
                    request_id=request_id,
                    operation=operation,
                    status=status,
                    started=started,
                    ok=False,
                    error_code=error_code,
                )

        do_HEAD = _method_not_allowed
        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_OPTIONS = _method_not_allowed

    return ControllerHandler


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    allow_public_bind: bool = False,
    env: Optional[Mapping[str, str]] = None,
    server_class: Optional[type[ThreadingHTTPServer]] = None,
    list_tools_func: ListToolsFunc = mcp.list_tools,
    call_tool_func: CallToolFunc = mcp.call_tool,
    audit_logger: Optional[AuditLogger] = None,
    max_body_bytes: int = _MAX_BODY_BYTES,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
) -> ThreadingHTTPServer:
    """Return an unstarted controller server.

    Tests can pass ``port=0`` for an ephemeral local port, a fake
    ``server_class`` to avoid opening a socket, and fake MCP functions to assert
    that transport behavior does not duplicate tool logic.
    """
    effective_env = os.environ if env is None else env
    assessment = validate_bind_safety(
        host,
        allow_public_bind=allow_public_bind,
        auth_token_env=auth_token_env,
        env=effective_env,
        resolver=resolver,
    )
    token = resolve_auth_token(
        auth_token_env,
        env=effective_env,
        required=assessment.requires_auth,
    )
    handler = make_handler(
        list_tools_func=list_tools_func,
        call_tool_func=call_tool_func,
        auth_token=token,
        audit_logger=audit_logger,
        max_body_bytes=max_body_bytes,
    )
    cls = server_class or _server_class_for_host(host)
    httpd = cls((host, port), handler)
    httpd.anvil_controller_bind = assessment
    httpd.anvil_controller_auth_token_env = auth_token_env
    httpd.anvil_controller_auth_enabled = token is not None
    return httpd


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    allow_public_bind: bool = False,
    server_factory: Callable[..., ThreadingHTTPServer] = make_server,
) -> int:
    httpd = server_factory(
        host=host,
        port=port,
        auth_token_env=auth_token_env,
        allow_public_bind=allow_public_bind,
    )
    actual_host, actual_port = httpd.server_address[:2]
    print(
        "anvil-serving controller listening on http://%s:%s" % (actual_host, actual_port),
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.server_close()
    return 0


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
            "environment variable containing the controller token "
            "(default: ANVIL_CONTROLLER_TOKEN; unset disables auth only on loopback)"
        ),
    )
    serve_parser.add_argument(
        "--allow-public-bind",
        action="store_true",
        help="allow a public or wildcard bind; still requires --auth-token-env to be set",
    )
    return parser


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
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
