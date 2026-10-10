"""Stateless MCP facade for supplemental, device-bound memory."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ..control_plane.mcp import protocol
from .config import normalize_model_alias
from .memory import MemoryError, MemoryRouter, tool_schemas

_LEGACY = "2025-11-25"
_OPERATIONS = {
    "memory_retain": "retain",
    "memory_recall": "recall",
    "memory_reflect": "reflect",
    "memory_banks": "banks",
}


class MemoryMCP:
    """Narrow JSON-only Streamable HTTP MCP adapter; no sessions or SSE."""

    def __init__(self, memory: MemoryRouter, principal: str, aliases: Sequence[str]):
        self.memory = memory
        self.principal = principal
        self.aliases = tuple(aliases)
        self.tools = _tools(self.aliases, user_banks=getattr(principal, "owner", None) is not None)

    def handle(self, request: Mapping[str, Any], headers) -> dict | None:
        if not isinstance(request, dict):
            return protocol.jsonrpc_error(None, -32600, "invalid JSON-RPC request")
        request_id = request.get("id")
        if (request.get("jsonrpc") != "2.0"
                or not isinstance(request.get("method"), str) or not request["method"]
                or not isinstance(request.get("params", {}), dict)
                or ("id" in request and (isinstance(request_id, bool)
                    or not isinstance(request_id, (str, int))))):
            return protocol.jsonrpc_error(None, -32600, "invalid JSON-RPC request")
        if "id" not in request:
            return None
        version = headers.get("MCP-Protocol-Version")
        if version == _LEGACY or (version is None and self._legacy_request(request)):
            response = self._legacy(request)
        else:
            response = self._current(request, version)
        # No outputSchema: text carries the complete result without duplicating memory.
        if request["method"] == "tools/call" and "result" in response:
            response["result"].pop("structuredContent", None)
        return response

    @staticmethod
    def _legacy_request(request: Mapping[str, Any]) -> bool:
        params = request.get("params")
        metadata = params.get("_meta") if isinstance(params, Mapping) else None
        return request.get("method") in {"initialize", "ping", "notifications/initialized", "tools/list", "tools/call"} and not isinstance(metadata, dict)

    def _current(self, request: dict, header_version: str | None) -> dict:
        metadata_error = protocol.request_metadata_error(request, protocol_version=protocol.PROTOCOL_VERSION)
        if metadata_error is not None:
            return metadata_error
        body_version = request["params"]["_meta"][protocol.PROTOCOL_VERSION_META_KEY]
        if header_version is not None and header_version != body_version:
            return protocol.jsonrpc_error(request.get("id"), protocol.HEADER_MISMATCH,
                                          "MCP-Protocol-Version header does not match request metadata")
        if request.get("method") == "ping":
            return {"jsonrpc": "2.0", "id": request.get("id"), "result": protocol.complete_result(
                {}, server_info=protocol.SERVER_INFO)}
        if request.get("method") == "server/discover":
            return {"jsonrpc": "2.0", "id": request.get("id"), "result": protocol.complete_result(
                {"supportedVersions": [protocol.PROTOCOL_VERSION], "capabilities": {"tools": {}},
                 "instructions": "Supplemental memory only; existing harness memory remains authoritative. No transcript ingestion occurs automatically."},
                server_info=protocol.SERVER_INFO, cacheable=True)}
        return protocol.handle_request(
            request, tools=self.tools, protocol_version=protocol.PROTOCOL_VERSION,
            server_info=protocol.SERVER_INFO, list_tools=lambda: list(self.tools.values()),
            call_tool=self._call, target_context=lambda _value: {},
        ) or {"jsonrpc": "2.0", "id": request.get("id"), "result": {}}

    def _legacy(self, request: dict) -> dict:
        method, request_id = request.get("method"), request.get("id")
        if method == "initialize":
            params = request.get("params", {})
            if not isinstance(params, dict) or params.get("protocolVersion", _LEGACY) != _LEGACY:
                return protocol.jsonrpc_error(request_id, protocol.UNSUPPORTED_PROTOCOL_VERSION, "unsupported MCP protocol version")
            return {"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": _LEGACY, "capabilities": {"tools": {}}, "serverInfo": protocol.SERVER_INFO, "instructions": "Supplemental memory only; existing harness memory remains authoritative. No transcript ingestion occurs automatically."}}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list(self.tools.values())}}
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return protocol.jsonrpc_error(request_id, -32602, "invalid tool arguments")
            return {"jsonrpc": "2.0", "id": request_id, "result": protocol.tool_result(self._call(params["name"], params.get("arguments")), server_info=protocol.SERVER_INFO)}
        return protocol.jsonrpc_error(request_id, -32601, "method not found")

    def _call(self, name: str, arguments: dict | None) -> dict:
        operation = _OPERATIONS.get(name)
        if operation is None:
            return {"ok": False, "code": "unknown_tool", "message": "unknown memory tool"}
        if not isinstance(arguments, dict):
            return {"ok": False, "code": "invalid_request", "message": "tool arguments must be an object"}
        alias = arguments.get("alias")
        if isinstance(alias, str):
            alias = normalize_model_alias(alias)
        configured = self.memory.aliases(self.principal)
        if isinstance(alias, str) and alias in configured and alias not in self.aliases:
            return {"ok": False, "code": "key_access_denied", "message": "API key does not grant this model"}
        if not isinstance(alias, str):
            return {"ok": False, "code": "memory_forbidden", "message": "memory alias is not available to this principal"}
        try:
            result = self.memory.dispatch({"alias": alias, "operation": operation,
                                           **({"bank": arguments["bank"]} if "bank" in arguments else {}),
                                           "arguments": {key: value for key, value in arguments.items() if key not in {"alias", "bank"}}},
                                          principal=self.principal)
            return {"ok": True, **result}
        except MemoryError as exc:
            return {"ok": False, "code": exc.code, "message": exc.message}


def _tools(aliases: Sequence[str], *, user_banks=False) -> dict[str, dict]:
    return {
        tool["function"]["name"]: {
            "name": tool["function"]["name"],
            "description": "Supplemental memory; existing harness memory remains authoritative.",
            "inputSchema": tool["function"]["parameters"],
            "annotations": {"readOnlyHint": tool["function"]["name"] in {"memory_recall", "memory_reflect", "memory_banks"}},
        }
        for tool in tool_schemas(aliases, user_banks=user_banks)
    }
