"""Bounded authenticated HTTP, JSON-RPC, and REST controller protocol."""

from __future__ import annotations

import base64
import hmac
import math
import os
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Optional, Sequence

from ... import mcp
from ...observability.node_workload_collector import NodeWorkloadCollector
from ...observability.workload_tools import (
    NODE_WORKLOADS_TOOL_NAME,
    is_exact_node_workloads_declaration,
    node_workloads_declaration,
    parse_node_workload_query,
    workload_failure,
    workload_success,
)
from ..authorization import (
    ALLOWED_SCOPES,
    NODE_ADMIN_BOOTSTRAP,
    WORKLOADS_READ,
    AuthorizationPolicy,
    check_scope,
)
from ..mcp import protocol as mcp_protocol
from .catalog import (
    CallToolFunc,
    ListToolsFunc,
    _mcp_tool_name,
    _validated_tool_catalog,
)
from .errors import ControllerError
from .operation_context import controller_operation_context, is_confirmed_mutation
from .security import (
    _REQUEST_ID_HEADER,
    _extract_request_token,
    _json_dumps,
    _redact_secret,
    _safe_request_id,
    _sanitize_persisted_value,
    _strict_json_loads,
)
from .store import (
    OperationStore,
    _idempotency_context,
    _idempotency_key,
    _operation_fingerprint,
    _operation_status_key,
)


DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_READ_TIMEOUT_SECONDS = 30.0

_LEGACY_TOOL_SCOPES = frozenset((None, "media:read", "media:submit", "media:cancel"))
_UNDECLARED_SCOPE = object()

_MAX_BODY_BYTES = int(
    os.environ.get("ANVIL_CONTROLLER_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES))
)
_READ_TIMEOUT_SECONDS = float(
    os.environ.get("ANVIL_CONTROLLER_READ_TIMEOUT_SECONDS", str(DEFAULT_READ_TIMEOUT_SECONDS))
)

AuditLogger = Callable[[dict[str, Any]], None]
JsonLoadsFunc = Callable[[str], Any]
WorkloadClock = Callable[[], Any]

_SAFE_RPC_INTEGER = 2**53 - 1


def _workload_rpc_id(value: object) -> str | int | None:
    if type(value) is int:
        return value if -_SAFE_RPC_INTEGER <= value <= _SAFE_RPC_INTEGER else None
    if type(value) is str and _safe_request_id(value) == value:
        return value
    return None


def _workload_protocol_error(
    request_id: str | int | None,
    *,
    code: int = -32600,
    error_code: str = "invalid_workload_request",
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": "workload protocol request is invalid",
            "data": {"code": error_code},
        },
    }


def _tools_with_node_workloads(
    list_tools_func: ListToolsFunc, *, enabled: bool
) -> ListToolsFunc:
    def list_tools() -> list[dict]:
        tools = list_tools_func()
        if not isinstance(tools, list):
            return tools
        matches = [
            item
            for item in tools
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and _mcp_tool_name(item["name"]) == NODE_WORKLOADS_TOOL_NAME
        ]
        if len(matches) > 1 or (
            len(matches) == 1 and not is_exact_node_workloads_declaration(matches[0])
        ):
            raise ControllerError(
                "reserved_tool_conflict",
                "controller tool catalog conflicts with a reserved operation",
                status=500,
            )
        if not matches and enabled:
            return [*tools, node_workloads_declaration()]
        return tools

    return list_tools


def _default_audit_logger(record: dict[str, Any]) -> None:
    sys.stderr.write(_json_dumps(record) + "\n")
    sys.stderr.flush()


#: Default size cap for one :class:`FileAuditLogger` generation before the
#: single-rotation ``os.replace`` to ``<path>.1``.
DEFAULT_AUDIT_LOG_MAX_BYTES = 16 * 1024 * 1024


class FileAuditLogger:
    """Durable JSONL audit sink that tees to stderr (ADR-0033).

    Records are the same already-redacted, metadata-only audit dictionaries
    the stderr logger receives. Container logs stay complete (the tee), and
    the file on the operation-state volume survives restarts. Construction
    fails when the path is unwritable — a configured audit sink that cannot
    write is a boot error, not a silent downgrade. Later write failures fall
    back to stderr-only and never fail the request being audited.
    """

    def __init__(
        self, path: str, *, max_bytes: int = DEFAULT_AUDIT_LOG_MAX_BYTES
    ) -> None:
        if max_bytes < 1024:
            raise ControllerError(
                "bad_audit_log_config",
                "audit log max_bytes must be at least 1024",
                status=400,
            )
        self.path = path
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        try:
            with open(path, "a", encoding="utf-8"):
                pass
        except OSError as exc:
            raise ControllerError(
                "audit_log_unwritable",
                "audit log path is not writable",
                status=400,
                details={"path": path, "error": type(exc).__name__},
            ) from exc

    def __call__(self, record: dict[str, Any]) -> None:
        _default_audit_logger(record)
        try:
            line = _json_dumps(record)
        except (TypeError, ValueError):
            return
        try:
            with self._lock:
                self._rotate_if_needed()
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
        except OSError:
            sys.stderr.write(
                _json_dumps({"event": "audit_file_write_failed", "path": self.path}) + "\n"
            )
            sys.stderr.flush()

    def _rotate_if_needed(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return
        if size < self.max_bytes:
            return
        os.replace(self.path, self.path + ".1")


def _content_type_is_json(value: Optional[str]) -> bool:
    if value is None:
        return False
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


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


def _response_with_request_id(
    envelope: dict, request_id: str, auth_token: Optional[str] = None
) -> dict:
    if "request_id" in envelope:
        return _sanitize_persisted_value(dict(envelope), auth_token)
    response = dict(envelope)
    response["request_id"] = request_id
    return _sanitize_persisted_value(response, auth_token)


def _tool_result(envelope: dict) -> dict:
    return mcp_protocol.tool_result(
        envelope,
        server_info=mcp.SERVER_INFO,
    )


def _decode_mcp_header(value: str) -> str:
    value = value.strip(" \t")
    if not (value.startswith("=?base64?") and value.endswith("?=")):
        return value
    encoded = value[len("=?base64?") : -2]
    try:
        decoded = base64.b64decode(encoded, validate=True)
        return decoded.decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ControllerError(
            "mcp_header_mismatch",
            "MCP header contains invalid Base64 sentinel encoding",
            status=400,
        ) from exc


def _mcp_header_error(headers: Any, body: dict[str, Any]) -> dict | None:
    metadata_error = mcp_protocol.request_metadata_error(
        body,
        protocol_version=mcp.PROTOCOL_VERSION,
        check_supported_version=False,
    )
    if metadata_error is not None:
        return metadata_error
    request_id = body.get("id")
    params = body.get("params")
    metadata = params.get("_meta") if isinstance(params, dict) else None
    body_version = (
        metadata.get(mcp_protocol.PROTOCOL_VERSION_META_KEY)
        if isinstance(metadata, dict)
        else None
    )
    header_version = headers.get("MCP-Protocol-Version")
    if not isinstance(header_version, str) or header_version != body_version:
        return mcp_protocol.jsonrpc_error(
            request_id,
            mcp_protocol.HEADER_MISMATCH,
            "MCP-Protocol-Version header is missing or does not match request metadata",
        )
    method = body.get("method")
    if headers.get("Mcp-Method") != method:
        return mcp_protocol.jsonrpc_error(
            request_id,
            mcp_protocol.HEADER_MISMATCH,
            "Mcp-Method header is missing or does not match the request method",
        )
    if method == "tools/call":
        name = params.get("name") if isinstance(params, dict) else None
        header_name = headers.get("Mcp-Name")
        try:
            decoded_name = (
                _decode_mcp_header(header_name)
                if isinstance(header_name, str)
                else None
            )
        except ControllerError:
            decoded_name = None
        if decoded_name != name:
            return mcp_protocol.jsonrpc_error(
                request_id,
                mcp_protocol.HEADER_MISMATCH,
                "Mcp-Name header is missing or does not match the requested tool",
            )
    return mcp_protocol.request_metadata_error(
        body,
        protocol_version=mcp.PROTOCOL_VERSION,
    )


def _mcp_origin_allowed(value: str) -> bool:
    """Accept only explicit loopback browser origins."""

    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in ("http", "https")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        return False
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} and (
        port is None or 0 < port <= 65535
    )


def make_handler(
    *,
    list_tools_func: ListToolsFunc = mcp.list_tools,
    call_tool_func: CallToolFunc = mcp.call_tool,
    auth_token: Optional[str] = None,
    audit_logger: Optional[AuditLogger] = None,
    max_body_bytes: int = _MAX_BODY_BYTES,
    read_timeout_seconds: float = _READ_TIMEOUT_SECONDS,
    operation_store: Optional[OperationStore] = None,
    allowed_operations: Optional[Sequence[str]] = None,
    json_loads_func: JsonLoadsFunc = _strict_json_loads,
    node_id: Optional[str] = None,
    authorization_policy: Optional[AuthorizationPolicy] = None,
    workload_collector: Optional[NodeWorkloadCollector] = None,
    workload_clock: WorkloadClock = time.time,
):
    """Build a request handler class for controller tests or ``make_server``."""

    audit = audit_logger or _default_audit_logger
    allowlist_enabled = allowed_operations is not None
    declared_tools, declared_name_by_normalized = _validated_tool_catalog(
        _tools_with_node_workloads(
            list_tools_func, enabled=workload_collector is not None
        ),
        allowed_operations,
    )
    node_workloads_allowed = (
        not allowlist_enabled
        or NODE_WORKLOADS_TOOL_NAME in declared_name_by_normalized
    )
    tool_scope_by_normalized: dict[str, str | None | object] = {}
    for declaration in declared_tools:
        name = declaration.get("name") if isinstance(declaration, dict) else None
        metadata = declaration.get("_meta") if isinstance(declaration, dict) else None
        scope = metadata.get("anvil/requiredScope") if isinstance(metadata, dict) else None
        if isinstance(name, str):
            tool_scope_by_normalized[_mcp_tool_name(name)] = scope
    store = operation_store or OperationStore()

    class ControllerHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "anvil-controller"
        sys_version = ""

        def setup(self) -> None:
            super().setup()
            self._workload_request = False
            self._workload_request_id: Optional[str] = None
            if read_timeout_seconds > 0:
                self.connection.settimeout(read_timeout_seconds)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _remote_addr(self) -> str:
            try:
                return str(self.client_address[0])
            except Exception:
                return ""

        def _begin_workload_request(self) -> str:
            if not self._workload_request:
                self._workload_request = True
                self._workload_request_id = _safe_request_id(None)
            assert self._workload_request_id is not None
            return self._workload_request_id

        def _reset_workload_request(self) -> None:
            self._workload_request = False
            self._workload_request_id = None

        def _authenticated(self) -> bool:
            self._principal_kind = None
            self._presented_token = None
            if auth_token is None:
                self._principal_kind = "legacy"
                return True
            supplied = _extract_request_token(self.headers)
            if supplied is None:
                return False
            try:
                legacy_match = hmac.compare_digest(
                    supplied.encode("utf-8"), auth_token.encode("utf-8")
                )
            except UnicodeEncodeError:
                return False
            if legacy_match:
                self._principal_kind = "legacy"
                return True
            workload = check_scope(authorization_policy, supplied, WORKLOADS_READ)
            bootstrap = check_scope(authorization_policy, supplied, NODE_ADMIN_BOOTSTRAP)
            if workload.allowed or bootstrap.allowed:
                self._principal_kind = "scoped"
                self._presented_token = supplied
                return True
            return False

        def _tool_scope(self, normalized_name: str) -> str | None | object:
            return tool_scope_by_normalized.get(normalized_name, _UNDECLARED_SCOPE)

        def _authorize_scope(self, scope: str | None | object) -> None:
            if isinstance(scope, str) and scope in ALLOWED_SCOPES:
                decision = check_scope(authorization_policy, self._presented_token, scope)
                if decision.allowed:
                    return
            elif scope is None or (
                isinstance(scope, str)
                and scope in _LEGACY_TOOL_SCOPES
                and self._principal_kind == "legacy"
            ):
                if self._principal_kind == "legacy":
                    return
            raise ControllerError(
                "authorization_scope_denied",
                "authorization scope is denied",
                status=403,
            )

        def _authorize_tool(self, tool_name: str) -> None:
            normalized_name = _mcp_tool_name(tool_name)
            scope = self._tool_scope(normalized_name)
            if scope is _UNDECLARED_SCOPE and self._principal_kind == "legacy":
                return
            self._authorize_scope(scope)

        def _visible_tools(self) -> list[dict]:
            visible: list[dict] = []
            for declaration in declared_tools:
                name = declaration.get("name") if isinstance(declaration, dict) else None
                if not isinstance(name, str):
                    continue
                try:
                    self._authorize_scope(self._tool_scope(_mcp_tool_name(name)))
                except ControllerError:
                    continue
                visible.append(declaration)
            return visible

        def _sanitize_response(self, value: Any) -> Any:
            value = _sanitize_persisted_value(value, auth_token)
            return _sanitize_persisted_value(value, self._presented_token)

        def _send_json(
            self,
            status: int,
            obj: dict[str, Any],
            *,
            request_id: str,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            payload = _json_dumps(
                _redact_secret(_redact_secret(obj, auth_token), self._presented_token)
            ).encode("utf-8")
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
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    self.close_connection = True

        def _send_no_content(self, *, request_id: str, status: int = 204) -> None:
            self.send_response(status)
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

        def _audit_workload(
            self,
            *,
            status: int,
            started: float,
            ok: bool,
            error_code: Optional[str],
        ) -> None:
            try:
                elapsed = (time.perf_counter() - started) * 1000.0
                if not math.isfinite(elapsed):
                    elapsed = 0.0
                elapsed = round(min(max(elapsed, 0.0), 3_600_000.0), 3)
                audit(
                    {
                        "event": "workload_read",
                        "operation": NODE_WORKLOADS_TOOL_NAME,
                        "status": status,
                        "ok": ok,
                        "error_code": error_code,
                        "elapsed_ms": elapsed,
                    }
                )
            except Exception:
                pass

        def _dispatch_node_workloads(
            self,
            arguments: object,
            *,
            idempotency_present: bool,
            valid_outer: bool,
        ) -> dict[str, Any]:
            self._authorize_scope(WORKLOADS_READ)
            if not node_workloads_allowed or not valid_outer:
                return workload_failure("invalid_workload_request")
            if idempotency_present:
                return workload_failure("idempotency_not_supported")
            try:
                query = parse_node_workload_query(arguments)
            except Exception:
                return workload_failure("invalid_workload_query")
            if workload_collector is None:
                return workload_failure("workload_source_unavailable")
            try:
                now = workload_clock()
                result = workload_collector.collect(query, now)
                return workload_success(result)
            except Exception:
                return workload_failure("workload_source_unavailable")

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
            if len(self.headers.get_all("Content-Length") or []) != 1:
                self.close_connection = True
                raise ControllerError(
                    "bad_content_length",
                    "exactly one Content-Length header is required",
                    status=400,
                )
            if not _content_type_is_json(self.headers.get("Content-Type")):
                self.close_connection = True
                raise ControllerError(
                    "unsupported_media_type",
                    "POST request bodies must use Content-Type: application/json",
                    status=415,
                )
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
            chunks: list[bytes] = []
            remaining = length
            deadline = (
                time.perf_counter() + read_timeout_seconds if read_timeout_seconds > 0 else None
            )
            try:
                while remaining > 0:
                    if deadline is not None:
                        seconds_left = deadline - time.perf_counter()
                        if seconds_left <= 0:
                            self.close_connection = True
                            raise ControllerError(
                                "request_timeout",
                                "request body read timed out",
                                status=408,
                                details={"read_timeout_seconds": read_timeout_seconds},
                            )
                        self.connection.settimeout(seconds_left)
                    reader = self.rfile.read1 if hasattr(self.rfile, "read1") else self.rfile.read
                    chunk = reader(min(remaining, 65536))
                    if not chunk:
                        self.close_connection = True
                        raise ControllerError(
                            "incomplete_body",
                            "request body ended before Content-Length bytes were received",
                            status=400,
                            details={
                                "expected_body_bytes": length,
                                "received_body_bytes": length - remaining,
                            },
                        )
                    chunks.append(chunk)
                    remaining -= len(chunk)
            except socket.timeout as exc:
                self.close_connection = True
                raise ControllerError(
                    "request_timeout",
                    "request body read timed out",
                    status=408,
                    details={"read_timeout_seconds": read_timeout_seconds},
                ) from exc
            finally:
                if read_timeout_seconds > 0:
                    self.connection.settimeout(read_timeout_seconds)
            raw = b"".join(chunks)
            try:
                obj = json_loads_func(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError) as exc:
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

        def _dispatch_tool(
            self,
            tool_name: str,
            arguments: dict[str, Any],
            *,
            request_id: str,
            idempotency_key: Optional[str],
            idempotency_context: Any = None,
        ) -> tuple[dict[str, Any], int]:
            if _mcp_tool_name(tool_name) == NODE_WORKLOADS_TOOL_NAME:
                return (
                    self._dispatch_node_workloads(
                        arguments,
                        idempotency_present=idempotency_key is not None,
                        valid_outer=idempotency_context is None,
                    ),
                    200,
                )
            self._authorize_tool(tool_name)
            if (
                arguments.get("confirm") is True
                and arguments.get("dry_run") is not True
                and idempotency_key is None
            ):
                raise ControllerError(
                    "idempotency_key_required",
                    "confirmed mutation operations require an idempotency key",
                    status=409,
                )
            if idempotency_key is None:
                return self._sanitize_response(
                    _response_with_request_id(call_tool_func(tool_name, arguments), request_id, auth_token)
                ), 200

            context = _idempotency_context(idempotency_context)
            disposition, record = store.claim(
                idempotency_key,
                _operation_fingerprint(tool_name, arguments, context),
                request_id,
            )
            if disposition == "conflict":
                raise ControllerError(
                    "idempotency_key_conflict",
                    "idempotency key was already used for a different operation",
                    status=409,
                    details={"key": idempotency_key},
                )
            if disposition == "full":
                raise ControllerError(
                    "idempotency_store_full",
                    "operation status store is at capacity",
                    status=503,
                )
            if disposition == "expired":
                raise ControllerError(
                    "idempotency_key_expired",
                    "idempotency key is expired and cannot be reused",
                    status=409,
                    details={"key": idempotency_key},
                )
            if disposition == "existing":
                assert record is not None
                if record["status"] == "running":
                    return (
                        _error_body(
                            "operation_running",
                            "operation with this idempotency key is still running",
                            request_id=request_id,
                            details={"key": idempotency_key},
                        ),
                        202,
                    )
                response = record.get("response")
                if isinstance(response, dict):
                    return response, 200
                raise ControllerError(
                    "idempotency_record_unavailable",
                    "operation record is not available for replay",
                    status=503,
                )

            with store.executing(idempotency_key):
                try:
                    if is_confirmed_mutation(arguments):
                        with controller_operation_context(idempotency_key, context):
                            envelope = self._sanitize_response(_response_with_request_id(
                                call_tool_func(tool_name, arguments), request_id, auth_token
                            ))
                    else:
                        envelope = self._sanitize_response(_response_with_request_id(
                            call_tool_func(tool_name, arguments), request_id, auth_token
                        ))
                    if not isinstance(envelope, dict):
                        raise TypeError("MCP tool result must be an object")
                except Exception:
                    failure = _error_body(
                        "internal_error",
                        "internal error",
                        request_id=request_id,
                    )
                    store.complete(idempotency_key, "failed", failure, auth_token)
                    raise
                store.complete(
                    idempotency_key,
                    "succeeded" if envelope.get("ok") else "failed",
                    envelope,
                    auth_token,
                )
            return envelope, 200

        def _jsonrpc_response(
            self,
            body: dict[str, Any],
            *,
            request_id: str,
            idempotency_key: Optional[str],
            idempotency_present: bool = False,
        ) -> Optional[dict[str, Any]]:
            if "id" not in body:
                return None
            req_id = body.get("id")
            if req_id is None:
                params = body.get("params")
                raw_name = params.get("name") if isinstance(params, dict) else None
                if (
                    body.get("method") == "tools/call"
                    and isinstance(raw_name, str)
                    and _mcp_tool_name(raw_name) == NODE_WORKLOADS_TOOL_NAME
                ):
                    return _workload_protocol_error(None)
                return {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "id must not be null"},
                }
            method = body.get("method")
            if method == "server/discover":
                result = mcp_protocol.complete_result(
                    {
                        "supportedVersions": [mcp.PROTOCOL_VERSION],
                        "capabilities": {"tools": {}},
                        "instructions": (
                            "Operate Anvil Serving through explicit, bounded tools. "
                            "Mutating tools retain their dry-run, confirmation, and human gates."
                        ),
                    },
                    server_info=mcp.SERVER_INFO,
                    cacheable=True,
                )
            elif method == "tools/list":
                result = mcp_protocol.complete_result(
                    {"tools": self._visible_tools()},
                    server_info=mcp.SERVER_INFO,
                    cacheable=True,
                )
            elif method == "tools/call":
                params = body["params"]
                raw_tool_name = params.get("name")
                normalized_name = (
                    _mcp_tool_name(raw_tool_name) if isinstance(raw_tool_name, str) else None
                )
                if normalized_name == NODE_WORKLOADS_TOOL_NAME:
                    correlation_id = _workload_rpc_id(req_id)
                    if correlation_id is None:
                        return _workload_protocol_error(None)
                    valid_outer = (
                        set(params) in ({"name", "arguments"}, {"name", "arguments", "_meta"})
                        and type(params.get("arguments")) is dict
                    )
                    envelope = self._dispatch_node_workloads(
                        params.get("arguments"),
                        idempotency_present=idempotency_present,
                        valid_outer=valid_outer,
                    )
                    return {
                        "jsonrpc": "2.0",
                        "id": correlation_id,
                        "result": _tool_result(envelope),
                    }
                tool_name = (
                    declared_name_by_normalized.get(normalized_name)
                    if normalized_name is not None
                    else None
                )
                if tool_name is None:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": "unknown tool %r" % normalized_name,
                            "data": {"code": "unknown_tool"},
                        },
                    }
                arguments = params.get("arguments", {})
                if arguments is None:
                    arguments = {}
                if not isinstance(arguments, dict):
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": "tool arguments must be an object",
                            "data": {"code": "bad_arguments"},
                        },
                    }
                try:
                    envelope, _ = self._dispatch_tool(
                        tool_name,
                        arguments,
                        request_id=request_id,
                        idempotency_key=idempotency_key,
                        idempotency_context=params.get("context"),
                    )
                except ControllerError as exc:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32009,
                            "message": exc.message,
                            "data": {"code": exc.code, "details": exc.details},
                        },
                    }
                result = _tool_result(envelope)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
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

        def _authorize_mcp_headers_before_body(self) -> None:
            methods = self.headers.get_all("Mcp-Method") or []
            if len(methods) != 1:
                if any(method == "tools/call" for method in methods):
                    raise ControllerError("header_mismatch", "MCP request headers are invalid", status=400)
                return
            if not isinstance(methods[0], str):
                raise ControllerError("header_mismatch", "MCP request headers are invalid", status=400)
            if methods[0] != "tools/call":
                return
            names = self.headers.get_all("Mcp-Name") or []
            if len(names) == 0:
                return
            if len(names) != 1 or not isinstance(names[0], str):
                raise ControllerError("header_mismatch", "MCP request headers are invalid", status=400)
            try:
                raw_name = _decode_mcp_header(names[0])
            except ControllerError:
                raise ControllerError("header_mismatch", "MCP request headers are invalid", status=400) from None
            normalized_name = _mcp_tool_name(raw_name)
            if normalized_name == NODE_WORKLOADS_TOOL_NAME:
                self._begin_workload_request()
                self._authorize_scope(WORKLOADS_READ)
                return
            if normalized_name not in declared_name_by_normalized and self._principal_kind == "legacy":
                return
            self._authorize_scope(self._tool_scope(normalized_name))

        def do_GET(self) -> None:
            self._reset_workload_request()
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
                    health_payload = {
                        "status": "ok",
                        "service": "anvil-serving-controller",
                        "request_id": request_id,
                    }
                    # ADR-0033 node identity: declared by the operator via
                    # --node-id; clients with a matching transport
                    # expected_node verify it fail-closed before dispatch.
                    if node_id:
                        health_payload["node"] = node_id
                    self._send_json(
                        status,
                        health_payload,
                        request_id=request_id,
                    )
                    return
                if route == "/tools/list":
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        {"tools": self._visible_tools(), "request_id": request_id},
                        request_id=request_id,
                    )
                    return
                if route.startswith("/operations/"):
                    if self._principal_kind != "legacy":
                        raise ControllerError(
                            "authorization_scope_denied",
                            "authorization scope is denied",
                            status=403,
                        )
                    key = _operation_status_key(route[len("/operations/") :])
                    record = store.lookup(key)
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        (
                            record
                            if record is not None
                            else {"key": key, "status": "unknown", "request_id": request_id}
                        ),
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
                if route == "/mcp":
                    status = 405
                    error_code = "method_not_allowed"
                    self._send_error_json(
                        status,
                        error_code,
                        "the MCP endpoint only accepts POST requests",
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
            except ControllerError as exc:
                status = exc.status
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
            self._reset_workload_request()
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
                        {"tools": self._visible_tools(), "request_id": request_id},
                        request_id=request_id,
                    )
                    return
                if route == "/mcp":
                    try:
                        self._authorize_mcp_headers_before_body()
                    except ControllerError:
                        # The declared body has deliberately not been read;
                        # avoid parsing it as a second request on keepalive.
                        self.close_connection = True
                        if self._workload_request:
                            request_id = self._begin_workload_request()
                        raise
                    if self._workload_request:
                        request_id = self._begin_workload_request()
                    body = self._read_json_body(request_id=request_id)
                    params = body.get("params")
                    requested_name = (
                        params.get("name") if isinstance(params, dict) else None
                    )
                    if (
                        isinstance(requested_name, str)
                        and _mcp_tool_name(requested_name) == NODE_WORKLOADS_TOOL_NAME
                    ):
                        request_id = self._begin_workload_request()
                    origin = self.headers.get("Origin")
                    if origin is not None and not _mcp_origin_allowed(origin):
                        status = 403
                        error_code = "origin_not_allowed"
                        response_error = (
                            _workload_protocol_error(
                                _workload_rpc_id(body.get("id")),
                                error_code="origin_not_allowed",
                            )
                            if self._workload_request
                            else mcp_protocol.jsonrpc_error(
                                body.get("id"),
                                -32600,
                                "Origin is not allowed by this controller",
                            )
                        )
                        self._send_json(
                            status,
                            response_error,
                            request_id=request_id,
                        )
                        return
                    protocol_error = _mcp_header_error(self.headers, body)
                    if protocol_error is not None:
                        error = protocol_error.get("error")
                        code = error.get("code") if isinstance(error, dict) else None
                        status = (
                            404
                            if code == -32601
                            else 400
                        )
                        error_code = (
                            "header_mismatch"
                            if code == mcp_protocol.HEADER_MISMATCH
                            else "unsupported_protocol_version"
                            if code == mcp_protocol.UNSUPPORTED_PROTOCOL_VERSION
                            else "missing_required_client_capability"
                            if code == mcp_protocol.MISSING_REQUIRED_CLIENT_CAPABILITY
                            else "invalid_request"
                        )
                        if self._workload_request:
                            protocol_error = _workload_protocol_error(
                                _workload_rpc_id(body.get("id")),
                                code=code if type(code) is int else -32600,
                                error_code=error_code or "invalid_workload_request",
                            )
                        self._send_json(
                            status,
                            protocol_error,
                            request_id=request_id,
                        )
                        return
                    if "id" in body and body.get("method") == "tools/call":
                        params = body.get("params", {})
                        if params is None:
                            params = {}
                        if isinstance(params, dict):
                            raw_arguments = params.get("arguments", {})
                            if raw_arguments is None:
                                raw_arguments = {}
                            if isinstance(raw_arguments, dict):
                                tool = (
                                    params.get("name")
                                    if isinstance(params.get("name"), str)
                                    else None
                                )
                                if isinstance(raw_arguments.get("dry_run"), bool):
                                    dry_run = raw_arguments["dry_run"]
                                if isinstance(raw_arguments.get("confirm"), bool):
                                    confirm = raw_arguments["confirm"]
                    idempotency_present = bool(
                        self.headers.get_all("X-Anvil-Idempotency-Key")
                    )
                    idempotency_key = None
                    if body.get("method") == "tools/call" and not self._workload_request:
                        idempotency_key = _idempotency_key(self.headers)
                    response = self._jsonrpc_response(
                        body,
                        request_id=request_id,
                        idempotency_key=idempotency_key,
                        idempotency_present=idempotency_present,
                    )
                    status = 200
                    ok = response is None
                    if response is not None:
                        if "error" in response:
                            ok = False
                            error = response.get("error")
                            data = error.get("data") if isinstance(error, dict) else None
                            code = error.get("code") if isinstance(error, dict) else None
                            if code == -32601:
                                status = 404
                            elif code in {
                                mcp_protocol.HEADER_MISMATCH,
                                mcp_protocol.MISSING_REQUIRED_CLIENT_CAPABILITY,
                                mcp_protocol.UNSUPPORTED_PROTOCOL_VERSION,
                            }:
                                status = 400
                            if isinstance(data, dict) and isinstance(data.get("code"), str):
                                error_code = data["code"]
                            elif isinstance(error, dict) and isinstance(error.get("message"), str):
                                error_code = error["message"]
                        else:
                            ok = True
                            result = response.get("result")
                            structured = (
                                result.get("structuredContent")
                                if isinstance(result, dict)
                                else None
                            )
                            if isinstance(structured, dict) and structured.get("ok") is False:
                                ok = False
                                err = structured.get("error")
                                if isinstance(err, dict) and isinstance(err.get("code"), str):
                                    error_code = err["code"]
                    if response is not None:
                        self._send_json(status, response, request_id=request_id)
                    else:
                        status = 202
                        self._send_no_content(request_id=request_id, status=status)
                    return

                if route != "/tools/call":
                    status = 405 if route in ("/health", "/healthz") else 404
                    error_code = (
                        "method_not_allowed" if route in ("/health", "/healthz") else "not_found"
                    )
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
                        extra_headers={"Allow": "GET"}
                        if route in ("/health", "/healthz")
                        else None,
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
                normalized_name = _mcp_tool_name(raw_name)
                if normalized_name == NODE_WORKLOADS_TOOL_NAME:
                    request_id = self._begin_workload_request()
                    raw_arguments = body.get("arguments")
                    envelope = self._dispatch_node_workloads(
                        raw_arguments,
                        idempotency_present=bool(
                            self.headers.get_all("X-Anvil-Idempotency-Key")
                        ),
                        valid_outer=(
                            set(body) == {"name", "arguments"}
                            and type(raw_arguments) is dict
                        ),
                    )
                    status = 200
                    ok = bool(envelope.get("ok"))
                    if not ok:
                        error = envelope.get("error")
                        if isinstance(error, dict) and isinstance(error.get("code"), str):
                            error_code = error["code"]
                    tool = NODE_WORKLOADS_TOOL_NAME
                    self._send_json(status, envelope, request_id=request_id)
                    return
                raw_arguments = body.get("arguments", {})
                if raw_arguments is None:
                    raw_arguments = {}
                if not isinstance(raw_arguments, dict):
                    raise ControllerError(
                        "bad_request",
                        "tools/call 'arguments' must be a JSON object",
                        status=400,
                    )

                tool = declared_name_by_normalized.get(normalized_name)
                if tool is None and allowlist_enabled:
                    raise ControllerError(
                        "unknown_tool",
                        "unknown tool %r" % normalized_name,
                        status=400,
                    )
                if tool is None:
                    if self._principal_kind == "scoped":
                        self._authorize_scope(_UNDECLARED_SCOPE)
                    tool = normalized_name
                if isinstance(raw_arguments.get("dry_run"), bool):
                    dry_run = raw_arguments["dry_run"]
                if isinstance(raw_arguments.get("confirm"), bool):
                    confirm = raw_arguments["confirm"]

                envelope, status = self._dispatch_tool(
                    tool,
                    raw_arguments,
                    request_id=request_id,
                    idempotency_key=_idempotency_key(self.headers),
                    idempotency_context=body.get("context"),
                )
                ok = bool(envelope.get("ok"))
                if not ok:
                    err = envelope.get("error") if isinstance(envelope, dict) else None
                    if isinstance(err, dict) and isinstance(err.get("code"), str):
                        error_code = err["code"]
                self._send_json(
                    status,
                    envelope,
                    request_id=request_id,
                )
            except ControllerError as exc:
                status = exc.status
                error_code = exc.code
                if self._workload_request:
                    request_id = self._begin_workload_request()
                    protocol_code = (
                        exc.code
                        if exc.code
                        in {"authentication_error", "authorization_scope_denied"}
                        else "invalid_workload_request"
                    )
                    protocol_message = (
                        "workload request is not authorized"
                        if protocol_code == "authorization_scope_denied"
                        else "workload request is invalid"
                    )
                    self._send_json(
                        status,
                        {
                            "ok": False,
                            "error": {
                                "code": protocol_code,
                                "message": protocol_message,
                            },
                        },
                        request_id=request_id,
                    )
                else:
                    self._send_error_json(
                        status,
                        exc.code,
                        exc.message,
                        request_id=request_id,
                        details=exc.details,
                    )
            except Exception:
                status = 500
                error_code = "internal_error"
                self._send_error_json(
                    status,
                    error_code,
                    "internal error",
                    request_id=request_id,
                )
            finally:
                if self._workload_request:
                    self._audit_workload(
                        status=status,
                        started=started,
                        ok=ok,
                        error_code=error_code,
                    )
                else:
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
