from __future__ import annotations


from anvil_serving import mcp


def _rpc(name: str, arguments: dict, request_id: int) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }


def _structured(response: dict) -> dict:
    return response["result"]["structuredContent"]["data"]
