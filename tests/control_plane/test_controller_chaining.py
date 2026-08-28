"""Regression coverage for idempotent controller-to-controller mutations."""

from __future__ import annotations

import json

from anvil_serving import mcp
from tests.test_controller import CONTEXT, _request, running_controller


def _tool_catalog():
    return [
        {
            "name": "proxy_mutation",
            "description": "Proxy one bounded mutation.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "maxProperties": 2,
                "properties": {
                    "confirm": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                },
                "required": [],
            },
        }
    ]


def _remote_request(name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": name,
        "method": "tools/call",
        "params": {
            "name": name,
            "arguments": arguments,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {
                    "name": "controller-chaining-test",
                    "version": "1.0",
                },
            },
        },
    }


def test_confirmed_nested_mutation_forwards_outer_idempotency_identity(tmp_path):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return b'{"jsonrpc":"2.0","id":"nested","result":{"structuredContent":{"ok":true}}}'

    def opener(req, timeout):
        seen.append(
            {
                "headers": {name.lower(): value for name, value in req.header_items()},
                "body": json.loads(req.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return Response()

    def call_tool(_name, _arguments):
        mcp.remote_controller_request(
            "http://127.0.0.1:8765",
            _remote_request("serves_status", {"names": ["media-worker"]}),
            "resource-secret",
            opener=opener,
        )
        mcp.remote_controller_request(
            "http://127.0.0.1:8765",
            _remote_request(
                "serves_manage",
                {
                    "action": "up",
                    "names": ["media-worker"],
                    "dry_run": False,
                    "confirm": True,
                },
            ),
            "resource-secret",
            opener=opener,
        )
        return {"ok": True, "data": {"proxied": True}}

    outer = {
        "jsonrpc": "2.0",
        "id": "outer",
        "method": "tools/call",
        "params": {
            "name": "proxy_mutation",
            "arguments": {"confirm": True, "dry_run": False},
            "context": CONTEXT,
        },
    }
    with running_controller(
        list_tools_func=_tool_catalog,
        call_tool_func=call_tool,
        idempotency_db_path=str(tmp_path / "outer-operations.sqlite3"),
    ) as (host, port):
        status, _, response, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=outer,
            headers={"X-Anvil-Idempotency-Key": "outer-chain-1"},
        )

    assert status == 200
    assert response["result"]["structuredContent"]["ok"] is True
    assert len(seen) == 2
    assert "x-anvil-idempotency-key" not in seen[0]["headers"]
    assert "context" not in seen[0]["body"]["params"]
    assert seen[1]["headers"]["x-anvil-idempotency-key"] == "outer-chain-1"
    assert seen[1]["body"]["params"]["context"] == CONTEXT

    outside = []

    def outside_opener(req, timeout):
        assert timeout == 30
        outside.append(
            {
                "headers": {name.lower(): value for name, value in req.header_items()},
                "body": json.loads(req.data.decode("utf-8")),
            }
        )
        return Response()

    mcp.remote_controller_request(
        "http://127.0.0.1:8765",
        _remote_request(
            "serves_manage",
            {"action": "up", "dry_run": False, "confirm": True},
        ),
        "resource-secret",
        opener=outside_opener,
    )
    assert "x-anvil-idempotency-key" not in outside[0]["headers"]
    assert "context" not in outside[0]["body"]["params"]
