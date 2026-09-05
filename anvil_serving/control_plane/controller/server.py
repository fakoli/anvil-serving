"""Controller server construction and foreground lifecycle."""

from __future__ import annotations

import ipaddress
import os
import socket
import sys
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from typing import Any, Callable, Mapping, Optional, Sequence

from ... import mcp
from ..authorization import AuthorizationError, AuthorizationPolicy, load_authorization_policy
from ...graceful import DEFAULT_DRAIN_SECONDS, serve_until_signal
from ...observability.node_workload_collector import NodeWorkloadCollector
from ...observability.workloads import WorkloadQuery
from .catalog import CallToolFunc, ListToolsFunc
from .http import (
    AuditLogger,
    FileAuditLogger,
    JsonLoadsFunc,
    _MAX_BODY_BYTES,
    _READ_TIMEOUT_SECONDS,
    _default_audit_logger,
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
from .workload_sources import build_workload_readers


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
    audit_log_path: Optional[str] = None,
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
    node_id: Optional[str] = None,
    authorization_policy: Optional[str] = None,
    workload_benchmark_db: Optional[str] = None,
    workload_media_db: Optional[str] = None,
    workload_recipe_registry: Optional[str] = None,
    workload_manifest: Optional[str] = None,
    workload_topology: Optional[str] = None,
    workload_router_resource: Optional[str] = None,
    workload_router_auth_env: Optional[str] = None,
    workload_clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    workload_monotonic: Callable[[], object] = time.monotonic,
) -> ThreadingHTTPServer:
    """Return an unstarted controller server."""
    # ``env=None`` stays None: the real process environment may fall back to
    # the operator dotenv chain for the token (ADR-0033); injected mappings
    # remain hermetic.
    assessment = validate_bind_safety(
        host,
        allow_public_bind=allow_public_bind,
        allow_unauthenticated_loopback=allow_unauthenticated_loopback,
        auth_token_env=auth_token_env,
        env=env,
        resolver=resolver,
    )
    token = resolve_auth_token(
        auth_token_env,
        env=env,
        required=assessment.requires_auth,
    )
    scoped_policy: Optional[AuthorizationPolicy] = None
    if authorization_policy is not None:
        try:
            scoped_policy = load_authorization_policy(
                authorization_policy,
                env=os.environ if env is None else env,
                legacy_token=token,
            )
        except AuthorizationError:
            # A bad optional policy disables only new scoped surfaces.  The
            # established controller token remains available for legacy APIs.
            scoped_policy = None
    store = operation_store or OperationStore(
        idempotency_db_path,
        retention_seconds=idempotency_retention_seconds,
        max_records=idempotency_max_records,
        max_result_bytes=idempotency_max_result_bytes,
    )
    if audit_logger is None and audit_log_path:
        audit_logger = FileAuditLogger(audit_log_path)
    # ADR-0033 boot reconciliation: operations interrupted by a crash become
    # typed failures instead of blocking their idempotency keys until
    # retention lapses. Runs for injected stores too; the pass is idempotent.
    audit = audit_logger or _default_audit_logger
    for interrupted in store.recover_interrupted():
        audit(
            {
                "event": "operation_interrupted_recovered",
                "key": interrupted["key"],
                "request_id": interrupted["request_id"],
            }
        )
    collector: Optional[NodeWorkloadCollector] = None
    try:
        valid_node = type(node_id) is str and WorkloadQuery(host=node_id).host == node_id
    except Exception:
        valid_node = False
    if valid_node:
        readers = build_workload_readers(
            node_id,
            store,
            benchmark_db=workload_benchmark_db,
            media_db=workload_media_db,
            recipe_registry=workload_recipe_registry,
            manifest=workload_manifest,
            router_topology=workload_topology,
            router_resource=workload_router_resource,
            router_auth_env=workload_router_auth_env,
            environment=env,
        )
        collector = NodeWorkloadCollector(
            node_id, readers, monotonic=workload_monotonic
        )
    try:
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
            node_id=node_id,
            authorization_policy=scoped_policy,
            workload_collector=collector,
            workload_clock=workload_clock,
        )
        cls = server_class or _server_class_for_host(host)
        httpd = cls((host, port), handler)
    except Exception:
        if collector is not None:
            collector.close()
        raise
    original_server_close = httpd.server_close

    def server_close() -> None:
        if collector is not None:
            collector.close()
        original_server_close()

    httpd.server_close = server_close
    httpd.anvil_workload_collector = collector
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
    drain_seconds: float = DEFAULT_DRAIN_SECONDS,
    audit_log_path: Optional[str] = None,
    node_id: Optional[str] = None,
    authorization_policy: Optional[str] = None,
    workload_benchmark_db: Optional[str] = None,
    workload_media_db: Optional[str] = None,
    workload_recipe_registry: Optional[str] = None,
    workload_manifest: Optional[str] = None,
    workload_topology: Optional[str] = None,
    workload_router_resource: Optional[str] = None,
    workload_router_auth_env: Optional[str] = None,
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
        audit_log_path=audit_log_path,
        node_id=node_id,
        authorization_policy=authorization_policy,
        workload_benchmark_db=workload_benchmark_db,
        workload_media_db=workload_media_db,
        workload_recipe_registry=workload_recipe_registry,
        workload_manifest=workload_manifest,
        workload_topology=workload_topology,
        workload_router_resource=workload_router_resource,
        workload_router_auth_env=workload_router_auth_env,
    )
    actual_host, actual_port = httpd.server_address[:2]
    print(
        "anvil-serving controller listening on http://%s:%s" % (actual_host, actual_port),
        file=sys.stderr,
    )
    serve_until_signal(httpd, drain_seconds=drain_seconds)
    return 0
