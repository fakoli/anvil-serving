"""Public MCP compatibility and explicit tool-family composition facade.

Tool implementations live in :mod:`anvil_serving.control_plane.mcp.tools` and
reach callers through the composed ``TOOLS`` catalog and ``call_tool``/
``list_tools`` below. This module additionally re-exports a handful of
``tool_*`` names directly (``tool_openclaw_sync``, ``tool_client_catalog_sync``,
``tool_hermes_media_sync``, ``tool_routed_eval``, ``tool_router_manage``,
``tool_serves_mode``, ``tool_voice_manage``, ``tool_voice_proxy_manage``) plus
``validate_workflow_packet`` for their real callers through this facade;
other tool functions are reachable only via ``call_tool``/``TOOLS``.
"""

# Re-exported compatibility names and subprocess are intentionally module globals.
# ruff: noqa: F401
from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from typing import Any, Iterable, Optional

from . import __version__
from .control_plane.mcp.arguments import (
    MAX_CONTEXT_STRING as _MAX_CONTEXT_STRING,
    bounded_tool_schema as _bounded_tool_schema,
    target_context as _build_target_context,
    validate_tool_arguments as _validate_arguments,
)
from .control_plane.mcp.catalog import (
    call_tool as _call_catalog_tool,
    list_tools as _list_catalog_tools,
)
from .control_plane.mcp.controller_client import (
    remote_controller_request,
    resolve_controller_token,
)
from .control_plane.mcp.errors import ToolError
from .control_plane.mcp.errors import fail as _failure_envelope
from .control_plane.mcp.evidence import (
    resolve_benchmark_artifact_path as _resolve_benchmark_artifact_path,
)
from .control_plane.mcp.protocol import (
    PROTOCOL_VERSION,
    SERVER_INFO,
    handle_proxy_request as _handle_proxy_protocol_request,
    handle_request as _handle_protocol_request,
)
from .control_plane.mcp.runtime import run_argv as _run_argv
from .control_plane.mcp.node_bridge import run_node_bridge as _run_node_bridge
from .control_plane.mcp.security import (
    redact_error_details as _redact_error_details,
    redact_text as _redact_text,
    safe_controller_url as _safe_controller_url,
)
from .control_plane.mcp.stdio import (
    main as _stdio_main,
    serve_stdio as _serve_stdio_loop,
)
from .control_plane.mcp.tools import TOOLS
from .control_plane.mcp.tools.openclaw import (
    tool_client_catalog_sync,
    tool_hermes_media_sync,
    tool_openclaw_sync,
    tool_routed_eval,
)
from .control_plane.mcp.tools.operations import (
    _tool_operation_contracts,
    operation_declarations as _operation_declarations,
)
from .control_plane.mcp.tools.router import tool_router_manage
from .control_plane.mcp.tools.serves import tool_serves_mode
from .control_plane.mcp.tools.voice import (
    tool_voice_manage,
    tool_voice_proxy_manage,
)
from .control_plane.mcp.tools.workflow import validate_workflow_packet
from .operator_output import CONTEXT_FIELDS, context_from_plan


def _fail(code: str, message: str, details: Optional[dict] = None) -> dict:
    return _failure_envelope(
        code,
        message,
        details,
        redact_text=_redact_text,
        redact_details=_redact_error_details,
    )


TARGET_CONTEXT_SCHEMA = _bounded_tool_schema(
    {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            field: {
                "type": ["string", "boolean", "integer", "number", "null"],
                "maxLength": _MAX_CONTEXT_STRING,
            }
            for field in CONTEXT_FIELDS
        },
    }
)


def _validate_tool_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_arguments(name, arguments, TOOLS)


def validate_tool_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one typed tool call without dispatching it."""

    return _validate_tool_arguments(name, arguments)


def _target_context(value: Any) -> dict[str, Any]:
    return _build_target_context(
        value,
        context_fields=CONTEXT_FIELDS,
        context_schema=TARGET_CONTEXT_SCHEMA,
        context_builder=context_from_plan,
    )


def list_tools(
    *,
    caller: Mapping[str, Any] | None = None,
    audience: str | None = None,
) -> list[dict]:
    scopes = None
    if caller is not None:
        raw_scopes = caller.get("scopes", ())
        scopes = frozenset(raw_scopes) if isinstance(raw_scopes, (list, tuple, set, frozenset)) else frozenset()
    return _list_catalog_tools(
        TOOLS,
        TARGET_CONTEXT_SCHEMA,
        granted_scopes=scopes,
        audience=audience,
    )


def call_tool(
    name: str,
    arguments: Optional[dict] = None,
    *,
    caller: Mapping[str, Any] | None = None,
    audience: str | None = None,
) -> dict:
    return _call_catalog_tool(
        TOOLS,
        name,
        arguments,
        validate_arguments=validate_tool_arguments,
        fail=_fail,
        redact_text=_redact_text,
        caller=caller,
        audience=audience,
    )


def operation_declarations() -> list[dict]:
    """Return command-tree operation contracts against the composed catalog."""

    return _operation_declarations(TOOLS)


def tool_operation_contracts(args: dict) -> dict:
    """Preserve the public operation-contract handler surface."""

    return _tool_operation_contracts(args, TOOLS)


def handle_request(
    request: dict,
    *,
    caller: Mapping[str, Any] | None = None,
    audience: str | None = None,
) -> Optional[dict]:
    """Handle one stateless MCP request with optional authenticated identity.

    ``caller`` is supplied out-of-band by an authenticated transport.  It is
    never accepted from JSON-RPC params.  ``audience`` narrows discovery while
    keeping the existing stdio surface unchanged when omitted.
    """
    protocol_tools = (
        TOOLS
        if audience is None
        else {
            name: spec
            for name, spec in TOOLS.items()
            if spec.get("audience") == audience
        }
    )
    return _handle_protocol_request(
        request,
        tools=protocol_tools,
        protocol_version=PROTOCOL_VERSION,
        server_info=SERVER_INFO,
        list_tools=lambda: list_tools(caller=caller, audience=audience),
        call_tool=lambda name, arguments: call_tool(
            name, arguments, caller=caller, audience=audience
        ),
        target_context=_target_context,
    )


def handle_proxy_request(
    request: dict,
    controller_url: str,
    token: str,
) -> Optional[dict]:
    return _handle_proxy_protocol_request(
        request,
        controller_url,
        token,
        tools=TOOLS,
        local_request=handle_request,
        list_tools=list_tools,
        validate_arguments=_validate_tool_arguments,
        target_context=_target_context,
        remote_request=remote_controller_request,
    )


def serve_stdio(
    stdin: Iterable[str] = sys.stdin,
    stdout: Any = sys.stdout,
    *,
    controller_url: str = "",
    controller_token: str = "",
) -> int:
    return _serve_stdio_loop(
        stdin,
        stdout,
        controller_url=controller_url,
        controller_token=controller_token,
        handle_local_request=handle_request,
        handle_remote_request=handle_proxy_request,
    )


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    return _stdio_main(
        argv,
        list_tools=list_tools,
        safe_controller_url=_safe_controller_url,
        resolve_controller_token=resolve_controller_token,
        serve=serve_stdio,
        serve_proxy=lambda controller_url, auth_env: _run_node_bridge(
            controller_url,
            auth_env,
            __version__,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
