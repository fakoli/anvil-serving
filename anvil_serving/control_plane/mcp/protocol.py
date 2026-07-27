"""MCP JSON-RPC request, result, and controller-proxy mapping."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from ...operator_output import redact
from .errors import ToolError


PROXY_METHODS = frozenset({"tools/list", "tools/call"})


def jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: dict | None = None,
) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def tool_result(
    envelope: dict,
    *,
    context: dict[str, Any] | None = None,
) -> dict:
    result = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(envelope, sort_keys=True),
            }
        ],
        "structuredContent": envelope,
        "isError": not envelope.get("ok", False),
    }
    if context:
        result["_meta"] = {"anvil/context": context}
    return result


def handle_request(
    request: dict,
    *,
    tools: Mapping[str, Mapping[str, Any]],
    protocol_version: str,
    server_info: Mapping[str, Any],
    list_tools: Callable[[], list[dict]],
    call_tool: Callable[[str, dict | None], dict],
    target_context: Callable[[Any], dict[str, Any]],
) -> dict | None:
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    if "id" not in request:
        return None
    request_id = request.get("id")
    if request_id is None:
        return jsonrpc_error(None, -32600, "id must not be null")
    try:
        if method == "initialize":
            result = {
                "protocolVersion": protocol_version,
                "serverInfo": server_info,
                "capabilities": {"tools": {}},
            }
        elif method == "tools/list":
            result = {"tools": list_tools()}
        elif method == "tools/call":
            params = request.get("params", {})
            if params is None:
                params = {}
            if not isinstance(params, dict):
                raise ToolError("bad_params", "params must be an object")
            if params.get("name") not in tools:
                raise ToolError(
                    "unknown_tool",
                    "unknown tool %r" % params.get("name"),
                )
            arguments = params.get("arguments", {})
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                raise ToolError(
                    "bad_arguments",
                    "tool arguments must be an object",
                )
            context = target_context(params.get("context"))
            result = tool_result(
                call_tool(params.get("name"), arguments),
                context=context,
            )
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "method not found"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except ToolError as exc:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32602,
                "message": exc.message,
                "data": {"code": exc.code, **exc.details},
            },
        }


def handle_proxy_request(
    request: dict,
    controller_url: str,
    token: str,
    *,
    tools: Mapping[str, Mapping[str, Any]],
    local_request: Callable[[dict], dict | None],
    list_tools: Callable[[], list[dict]],
    validate_arguments: Callable[[str, Mapping[str, Any]], dict[str, Any]],
    target_context: Callable[[Any], dict[str, Any]],
    remote_request: Callable[[str, dict, str], dict],
) -> dict | None:
    if request.get("method") not in PROXY_METHODS:
        return local_request(request)
    if "id" not in request:
        return None
    request_id = request.get("id")
    if request_id is None:
        return jsonrpc_error(None, -32600, "id must not be null")
    context: dict[str, Any] = {}
    if request.get("method") == "tools/call":
        params = request.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return jsonrpc_error(
                request_id,
                -32602,
                "params must be an object",
            )
        name = params.get("name")
        if name not in tools:
            return jsonrpc_error(
                request_id,
                -32602,
                "unknown tool %r" % name,
                {"code": "unknown_tool"},
            )
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return jsonrpc_error(
                request_id,
                -32602,
                "tool arguments must be an object",
                {"code": "bad_arguments"},
            )
        try:
            validate_arguments(name, arguments)
            context = target_context(params.get("context"))
        except ToolError as exc:
            return jsonrpc_error(
                request_id,
                -32602,
                exc.message,
                {"code": exc.code, **redact(exc.details)},
            )
    try:
        response = remote_request(controller_url, request, token)
    except ToolError as exc:
        return jsonrpc_error(
            request_id,
            -32000,
            exc.message,
            {"code": exc.code, **exc.details},
        )
    if request.get("method") == "tools/list":
        result = response.get("result")
        remote_tools = result.get("tools") if isinstance(result, dict) else None
        local_tools = {tool["name"]: tool for tool in list_tools()}
        if (
            not isinstance(remote_tools, list)
            or any(
                not isinstance(tool, dict)
                or not isinstance(tool.get("name"), str)
                or local_tools.get(tool["name"]) != tool
                for tool in remote_tools
            )
        ):
            return jsonrpc_error(
                request_id,
                -32000,
                "controller MCP operation contracts are not a valid local subset",
                {"code": "operation_contract_mismatch"},
            )
    elif context:
        result = response.get("result")
        if isinstance(result, dict):
            metadata = result.get("_meta")
            if not isinstance(metadata, dict):
                metadata = {}
            result["_meta"] = {**metadata, "anvil/context": context}
    return response
