"""Compatibility facade for the authenticated HTTP controller.

The implementation lives in :mod:`anvil_serving.control_plane.controller`.
This module retains the established import and ``python -m`` surfaces used by
the CLI, tests, and external callers.
"""

from __future__ import annotations

# Re-exported compatibility names are intentionally imported into this module.
# ruff: noqa: F401

from . import mcp as mcp
from .control_plane.controller.catalog import (
    CallToolFunc,
    ListToolsFunc,
    _mcp_tool_name,
    _validated_tool_catalog,
)
from .control_plane.controller.cli import (
    DEFAULT_STATUS_MAX_RESPONSE_BYTES,
    DEFAULT_STATUS_URL,
    _build_parser,
    _status_payload,
    main,
    status,
)
from .control_plane.controller.errors import ControllerError
from .control_plane.controller.http import (
    AuditLogger,
    JsonLoadsFunc,
    DEFAULT_MAX_BODY_BYTES,
    DEFAULT_READ_TIMEOUT_SECONDS,
    _MAX_BODY_BYTES,
    _READ_TIMEOUT_SECONDS,
    _content_type_is_json,
    _default_audit_logger,
    _error_body,
    _response_with_request_id,
    _tool_result,
    make_handler,
)
from .control_plane.controller.security import (
    DEFAULT_AUTH_TOKEN_ENV,
    IPAddress,
    BindAssessment,
    BindSafetyError,
    _DOCUMENTATION_V4,
    _REQUEST_ID_HEADER,
    _REQUEST_ID_RE,
    _RFC1918_V4,
    _SECRET_TEXT_PATTERNS,
    _TAILSCALE_V4,
    _TAILSCALE_V6,
    _TOKEN_HEADER,
    _ULA_V6,
    _WILDCARD_BINDS,
    _env_has_token,
    _extract_request_token,
    _is_forbidden_bind_ip,
    _is_safe_private_ip,
    _is_sensitive_key,
    _is_tailscale_ip,
    _json_dumps,
    _redact_secret,
    _resolve_bind_ips,
    _safe_request_id,
    _sanitize_persisted_value,
    _strict_json_loads,
    resolve_auth_token,
    validate_bind_safety,
)
from .control_plane.controller.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    IPv6ThreadingHTTPServer,
    _server_class_for_host,
    make_server,
    serve,
)
from .control_plane.controller.store import (
    Clock,
    DEFAULT_IDEMPOTENCY_DB_PATH,
    DEFAULT_IDEMPOTENCY_MAX_RECORDS,
    DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES,
    DEFAULT_IDEMPOTENCY_RETENTION_SECONDS,
    OperationStore,
    _IDEMPOTENCY_CONTEXT_FIELDS,
    _IDEMPOTENCY_KEY_HEADER,
    _IDEMPOTENCY_KEY_RE,
    _TOMBSTONE_BYTES_PER_RECORD,
    _TOMBSTONE_HASH_COUNT,
    _TOMBSTONE_MIN_BYTES,
    _bounded_persisted_value,
    _idempotency_context,
    _idempotency_key,
    _is_persistence_failure,
    _operation_fingerprint,
    _operation_status_key,
)


__all__ = [
    "BindAssessment",
    "BindSafetyError",
    "ControllerError",
    "DEFAULT_AUTH_TOKEN_ENV",
    "DEFAULT_HOST",
    "DEFAULT_IDEMPOTENCY_DB_PATH",
    "DEFAULT_IDEMPOTENCY_MAX_RECORDS",
    "DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES",
    "DEFAULT_IDEMPOTENCY_RETENTION_SECONDS",
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_PORT",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "DEFAULT_STATUS_MAX_RESPONSE_BYTES",
    "DEFAULT_STATUS_URL",
    "IPv6ThreadingHTTPServer",
    "OperationStore",
    "main",
    "make_handler",
    "make_server",
    "resolve_auth_token",
    "serve",
    "status",
    "validate_bind_safety",
]


if __name__ == "__main__":
    raise SystemExit(main())
