"""Controller server construction and foreground lifecycle."""

from __future__ import annotations

import ipaddress
import os
import socket
import sys
from http.server import ThreadingHTTPServer
from typing import Any, Callable, Mapping, Optional, Sequence

from ... import mcp
from .catalog import CallToolFunc, ListToolsFunc
from .http import (
    AuditLogger,
    JsonLoadsFunc,
    _MAX_BODY_BYTES,
    _READ_TIMEOUT_SECONDS,
    make_handler,
)
from .security import (
    DEFAULT_AUTH_TOKEN_ENV,
    _strict_json_loads,
    resolve_auth_token,
    validate_bind_safety,
)
from .store import (
    DEFAULT_IDEMPOTENCY_DB_PATH,
    DEFAULT_IDEMPOTENCY_MAX_RECORDS,
    DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES,
    DEFAULT_IDEMPOTENCY_RETENTION_SECONDS,
    OperationStore,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def _server_class_for_host(host: str) -> type[ThreadingHTTPServer]:
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return ThreadingHTTPServer
    if addr.version == 6:
        return IPv6ThreadingHTTPServer
    return ThreadingHTTPServer


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    allow_public_bind: bool = False,
    allow_unauthenticated_loopback: bool = False,
    env: Optional[Mapping[str, str]] = None,
    server_class: Optional[type[ThreadingHTTPServer]] = None,
    list_tools_func: ListToolsFunc = mcp.list_tools,
    call_tool_func: CallToolFunc = mcp.call_tool,
    audit_logger: Optional[AuditLogger] = None,
    max_body_bytes: int = _MAX_BODY_BYTES,
    read_timeout_seconds: float = _READ_TIMEOUT_SECONDS,
    idempotency_db_path: str = DEFAULT_IDEMPOTENCY_DB_PATH,
    idempotency_retention_seconds: float = DEFAULT_IDEMPOTENCY_RETENTION_SECONDS,
    idempotency_max_records: int = DEFAULT_IDEMPOTENCY_MAX_RECORDS,
    idempotency_max_result_bytes: int = DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES,
    operation_store: Optional[OperationStore] = None,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
    allowed_operations: Optional[Sequence[str]] = None,
    json_loads_func: JsonLoadsFunc = _strict_json_loads,
) -> ThreadingHTTPServer:
    """Return an unstarted controller server."""
    effective_env = os.environ if env is None else env
    assessment = validate_bind_safety(
        host,
        allow_public_bind=allow_public_bind,
        allow_unauthenticated_loopback=allow_unauthenticated_loopback,
        auth_token_env=auth_token_env,
        env=effective_env,
        resolver=resolver,
    )
    token = resolve_auth_token(
        auth_token_env,
        env=effective_env,
        required=assessment.requires_auth,
    )
    store = operation_store or OperationStore(
        idempotency_db_path,
        retention_seconds=idempotency_retention_seconds,
        max_records=idempotency_max_records,
        max_result_bytes=idempotency_max_result_bytes,
    )
    handler = make_handler(
        list_tools_func=list_tools_func,
        call_tool_func=call_tool_func,
        auth_token=token,
        audit_logger=audit_logger,
        max_body_bytes=max_body_bytes,
        read_timeout_seconds=read_timeout_seconds,
        operation_store=store,
        allowed_operations=allowed_operations,
        json_loads_func=json_loads_func,
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
    allow_unauthenticated_loopback: bool = False,
    allowed_operations: Optional[Sequence[str]] = None,
    idempotency_db_path: str = DEFAULT_IDEMPOTENCY_DB_PATH,
    server_factory: Callable[..., ThreadingHTTPServer] = make_server,
) -> int:
    httpd = server_factory(
        host=host,
        port=port,
        auth_token_env=auth_token_env,
        allow_public_bind=allow_public_bind,
        allow_unauthenticated_loopback=allow_unauthenticated_loopback,
        allowed_operations=allowed_operations,
        idempotency_db_path=idempotency_db_path,
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
