"""MCP JSON-RPC request, result, and controller-proxy mapping."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from ... import __version__
from ...operator_output import redact
from .errors import ToolError


SERVER_INFO = {"name": "anvil-serving", "version": __version__}
PROTOCOL_VERSION = "2026-07-28"
PROXY_METHODS = frozenset({"tools/list", "tools/call"})
RESULT_TYPE_COMPLETE = "complete"
PROTOCOL_VERSION_META_KEY = "io.modelcontextprotocol/protocolVersion"
CLIENT_INFO_META_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_CAPABILITIES_META_KEY = "io.modelcontextprotocol/clientCapabilities"
SERVER_INFO_META_KEY = "io.modelcontextprotocol/serverInfo"
DEFAULT_CACHE_TTL_MS = 30_000
DEFAULT_CACHE_SCOPE = "private"
HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022


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
    server_info: Mapping[str, Any],
    context: dict[str, Any] | None = None,
) -> dict:
    structured = dict(envelope)
    extra_content = structured.pop("_mcpContent", ())
    if not isinstance(extra_content, (list, tuple)) or any(
        not isinstance(item, Mapping) for item in extra_content
    ):
        extra_content = ()
    result = {
        "resultType": RESULT_TYPE_COMPLETE,
        "content": [
            {
                "type": "text",
                "text": json.dumps(structured, sort_keys=True),
            }
        ]
        + [dict(item) for item in extra_content],
        "structuredContent": structured,
        "isError": not structured.get("ok", False),
        "_meta": {SERVER_INFO_META_KEY: dict(server_info)},
    }
    if context:
        result["_meta"]["anvil/context"] = context
    return result


def complete_result(
    payload: Mapping[str, Any],
    *,
    server_info: Mapping[str, Any],
    cacheable: bool = False,
) -> dict[str, Any]:
    """Build one MCP 2026-07-28 ordinary result."""

    result: dict[str, Any] = {
        "resultType": RESULT_TYPE_COMPLETE,
        **dict(payload),
        "_meta": {SERVER_INFO_META_KEY: dict(server_info)},
    }
    if cacheable:
        result["ttlMs"] = DEFAULT_CACHE_TTL_MS
        result["cacheScope"] = DEFAULT_CACHE_SCOPE
    return result


def request_metadata_error(
    request: Mapping[str, Any],
    *,
    protocol_version: str,
    check_supported_version: bool = True,
) -> dict | None:
    """Validate the stateless per-request metadata required by MCP 2026-07-28."""

    request_id = request.get("id")
    if request.get("jsonrpc") != "2.0":
        return jsonrpc_error(request_id, -32600, "jsonrpc must be '2.0'")
    method = request.get("method")
    if not isinstance(method, str) or not method:
        return jsonrpc_error(request_id, -32600, "method must be a non-empty string")
    params = request.get("params")
    if not isinstance(params, dict):
        return jsonrpc_error(request_id, -32602, "params must be an object")
    metadata = params.get("_meta")
    if not isinstance(metadata, dict):
        return jsonrpc_error(
            request_id,
            -32602,
            "request params must include an _meta object",
            {"required": [PROTOCOL_VERSION_META_KEY, CLIENT_CAPABILITIES_META_KEY]},
        )
    requested_version = metadata.get(PROTOCOL_VERSION_META_KEY)
    if not isinstance(requested_version, str):
        return jsonrpc_error(
            request_id,
            -32602,
            "request metadata must include a protocol version",
            {"required": PROTOCOL_VERSION_META_KEY},
        )
    if check_supported_version and requested_version != protocol_version:
        return jsonrpc_error(
            request_id,
            UNSUPPORTED_PROTOCOL_VERSION,
            "unsupported MCP protocol version",
            {
                "requested": requested_version,
                "supported": [protocol_version],
            },
        )
    capabilities = metadata.get(CLIENT_CAPABILITIES_META_KEY)
    if not isinstance(capabilities, dict):
        return jsonrpc_error(
            request_id,
            -32602,
            "request metadata must include client capabilities",
            {"required": CLIENT_CAPABILITIES_META_KEY},
        )
    client_info = metadata.get(CLIENT_INFO_META_KEY)
    if client_info is not None and (
        not isinstance(client_info, dict)
        or not isinstance(client_info.get("name"), str)
        or not isinstance(client_info.get("version"), str)
    ):
        return jsonrpc_error(
            request_id,
            -32602,
            "clientInfo must contain string name and version fields",
        )
    return None


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
    if "id" not in request:
        return None
    request_id = request.get("id")
    if request_id is None:
        return jsonrpc_error(None, -32600, "id must not be null")
    metadata_error = request_metadata_error(
        request,
        protocol_version=protocol_version,
    )
    if metadata_error is not None:
        return metadata_error
    method = request.get("method")
    try:
        if method == "server/discover":
            result = complete_result(
                {
                    "supportedVersions": [protocol_version],
                    "capabilities": {"tools": {}},
                    "instructions": (
                        "Operate Anvil Serving through explicit, bounded tools. "
                        "Mutating tools retain their dry-run, confirmation, and human gates."
                    ),
                },
                server_info=server_info,
                cacheable=True,
            )
        elif method == "tools/list":
            result = complete_result(
                {"tools": list_tools()},
                server_info=server_info,
                cacheable=True,
            )
        elif method == "tools/call":
            params = request["params"]
            raw_tool_name = params.get("name")
            if not isinstance(raw_tool_name, str) or raw_tool_name not in tools:
                raise ToolError(
                    "unknown_tool",
                    "unknown tool %r" % raw_tool_name,
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
                call_tool(raw_tool_name, arguments),
                server_info=server_info,
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
