"""Regression coverage for idempotent controller-to-controller mutations."""

from __future__ import annotations

import re

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.controller.operation_context import (
    current_controller_operation_context,
)
from anvil_serving.observability.workload_tools import (
    FLEET_WORKLOADS_TOOL_NAME,
    NODE_WORKLOADS_TOOL_NAME,
)
from tests.control_plane.test_controller_fleet_workloads import (
    LEGACY,
    MEDIA,
    SCOPED,
    _FleetCollector,
    _post,
    _server,
)
from tests.test_controller import CONTEXT, _request, running_controller


_CHILD_KEY_RE = re.compile(r"^anvil-child:[0-9a-f]{64}$")


def _tool_catalog(*names: str):
    return [
        {
            "name": name,
            "description": "Exercise one bounded chained operation.",
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "maxProperties": 4,
                "properties": {
                    "confirm": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                    "step": {"type": "integer"},
                    "value": {"type": "integer"},
                },
                "required": [],
            },
        }
        for name in names
    ]


def _remote_request(name: str, arguments: dict, *, request_id: str | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id or name,
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


def _outer_request(name: str, arguments: dict) -> dict:
    request = _remote_request(name, arguments, request_id="outer")
    request["params"]["context"] = dict(CONTEXT)
    return request


def _structured(response: dict) -> dict:
    return response["result"]["structuredContent"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": "Bearer " + token, "x-api-key": token}


def _workload_list_request() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "workload-list",
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {
                    "name": "workload-contract-test",
                    "version": "1.0",
                },
            },
        },
    }


def test_scoped_mcp_workloads_are_dynamic_sealed_and_match_rest(monkeypatch, tmp_path):
    """Workload declarations exist only in the scoped controller protocol."""
    generic_calls = []
    static_names = {tool["name"] for tool in mcp.list_tools()}
    assert NODE_WORKLOADS_TOOL_NAME not in static_names
    assert FLEET_WORKLOADS_TOOL_NAME not in static_names

    def forbidden_generic_call(*args):
        generic_calls.append(args)
        raise AssertionError("workload calls must not reach generic MCP dispatch")

    with _server(
        tmp_path,
        monkeypatch,
        call_tool_func=forbidden_generic_call,
    ) as (server, collector, _):
        host, port = server.server_address[:2]
        status, _, tools, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=_workload_list_request(),
            headers=_auth(SCOPED),
        )
        assert status == 200
        declared = {
            tool["name"]: tool
            for tool in tools["result"]["tools"]
            if tool["name"] in {NODE_WORKLOADS_TOOL_NAME, FLEET_WORKLOADS_TOOL_NAME}
        }
        assert set(declared) == {NODE_WORKLOADS_TOOL_NAME, FLEET_WORKLOADS_TOOL_NAME}
        for tool in declared.values():
            assert tool["_meta"]["anvil/requiredScope"] == "workloads:read"
            assert tool["inputSchema"]["additionalProperties"] is False
            properties = tool["inputSchema"]["properties"]
            assert not {"context", "target", "confirm", "dry_run"} & set(properties)

        for operation in (NODE_WORKLOADS_TOOL_NAME, FLEET_WORKLOADS_TOOL_NAME):
            status, rest = _post(
                server,
                "/tools/call",
                {"name": operation, "arguments": {}},
            )
            assert status == 200
            status, _, rpc, _ = _request(
                host,
                port,
                "POST",
                "/mcp",
                body=_remote_request(operation, {}),
                headers=_auth(SCOPED),
            )
            assert status == 200
            assert rpc["result"]["structuredContent"] == rest

    assert len(collector.calls) == 2
    assert generic_calls == []


@pytest.mark.parametrize("token", (None, LEGACY, MEDIA, "wrong-workload-scope-token"))
def test_mcp_workload_scope_denies_before_clock_or_collection(monkeypatch, tmp_path, token):
    collector = _FleetCollector()
    clocks = []

    # Keep this test on the real HTTP path while making pre-collection timing observable.
    with _server(
        tmp_path,
        monkeypatch,
        collector=collector,
        workload_clock=lambda: clocks.append(True),
    ) as (server, _collector, _):
        host, port = server.server_address[:2]
        headers = {} if token is None else _auth(token)
        status, _, response, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=_remote_request(FLEET_WORKLOADS_TOOL_NAME, {}),
            headers=headers,
        )

    assert status in {401, 403}
    assert collector.calls == []
    assert clocks == []
    assert "wrong-workload-scope-token" not in repr(response)


def test_mcp_workload_refuses_caller_context_without_collection_or_leak(monkeypatch, tmp_path):
    collector = _FleetCollector()
    private_context = "https://private.invalid/token=not-for-output"
    with _server(tmp_path, monkeypatch, collector=collector) as (server, _collector, audits):
        host, port = server.server_address[:2]
        request = _remote_request(FLEET_WORKLOADS_TOOL_NAME, {})
        request["params"]["context"] = {"private": private_context}
        status, _, response, _ = _request(
            host,
            port,
            "POST",
            "/mcp",
            body=request,
            headers=_auth(SCOPED),
        )

    assert status == 200
    assert response["result"]["structuredContent"]["error"]["code"] == "invalid_workload_request"
    assert collector.calls == []
    assert private_context not in repr(response)
    assert private_context not in repr(audits)


def test_distinct_child_mutations_get_stable_derived_keys_and_replay(tmp_path):
    child_token = "child-controller-secret"
    child_calls = []
    parent_calls = []

    def child_call(name, arguments):
        operation = current_controller_operation_context()
        assert operation is not None
        child_calls.append(
            {
                "name": name,
                "arguments": arguments,
                "key": operation.idempotency_key,
                "execution": dict(operation.execution),
            }
        )
        return {"ok": True, "data": {"call": len(child_calls), "step": arguments["step"]}}

    with running_controller(
        list_tools_func=lambda: _tool_catalog("child_mutate"),
        call_tool_func=child_call,
        env={"ANVIL_CONTROLLER_TOKEN": child_token},
        allow_unauthenticated_loopback=False,
        idempotency_db_path=str(tmp_path / "child.sqlite3"),
    ) as (child_host, child_port):
        child_url = f"http://{child_host}:{child_port}"

        def parent_call(_name, _arguments):
            parent_calls.append(True)
            first = mcp.remote_controller_request(
                child_url,
                _remote_request(
                    "child_mutate",
                    {"step": 1, "confirm": True, "dry_run": False},
                    request_id="child-first",
                ),
                child_token,
            )
            identical = mcp.remote_controller_request(
                child_url,
                _remote_request(
                    "child-mutate",
                    {"dry_run": False, "confirm": True, "step": 1},
                    request_id="child-retry",
                ),
                child_token,
            )
            second = mcp.remote_controller_request(
                child_url,
                _remote_request(
                    "child_mutate",
                    {"step": 2, "confirm": True, "dry_run": False},
                    request_id="child-second",
                ),
                child_token,
            )
            return {
                "ok": True,
                "data": {
                    "identical_replayed": _structured(identical) == _structured(first),
                    "first": _structured(first)["data"],
                    "second": _structured(second)["data"],
                },
            }

        outer = _outer_request("proxy_mutation", {"confirm": True, "dry_run": False, "step": 1})
        headers = {"X-Anvil-Idempotency-Key": "outer-fanout-1"}
        with running_controller(
            list_tools_func=lambda: _tool_catalog("proxy_mutation"),
            call_tool_func=parent_call,
            idempotency_db_path=str(tmp_path / "parent.sqlite3"),
        ) as (parent_host, parent_port):
            status, _, first_outer, _ = _request(
                parent_host, parent_port, "POST", "/mcp", body=outer, headers=headers
            )
            assert status == 200
            status, _, replayed_outer, _ = _request(
                parent_host, parent_port, "POST", "/mcp", body=outer, headers=headers
            )
            assert status == 200

        assert replayed_outer == first_outer
        assert _structured(first_outer)["data"] == {
            "identical_replayed": True,
            "first": {"call": 1, "step": 1},
            "second": {"call": 2, "step": 2},
        }
        assert parent_calls == [True]
        assert len(child_calls) == 2
        assert child_calls[0]["key"] != child_calls[1]["key"]
        assert all(_CHILD_KEY_RE.fullmatch(call["key"]) for call in child_calls)
        assert all(call["execution"] == CONTEXT for call in child_calls)

        conflicting = _remote_request(
            "child_mutate",
            {"step": 999, "confirm": True, "dry_run": False},
            request_id="forced-conflict",
        )
        conflicting["params"]["context"] = dict(CONTEXT)
        status, _, conflict, _ = _request(
            child_host,
            child_port,
            "POST",
            "/mcp",
            body=conflicting,
            headers={**_auth(child_token), "X-Anvil-Idempotency-Key": child_calls[0]["key"]},
        )
        assert status == 200
        assert conflict["error"]["data"]["code"] == "idempotency_key_conflict"
        assert len(child_calls) == 2


def test_keyed_read_outer_call_cannot_authorize_child_mutation(tmp_path):
    child_token = "child-controller-secret"
    child_calls = []

    with running_controller(
        list_tools_func=lambda: _tool_catalog("child_mutate"),
        call_tool_func=lambda *args: child_calls.append(args) or {"ok": True},
        env={"ANVIL_CONTROLLER_TOKEN": child_token},
        allow_unauthenticated_loopback=False,
        idempotency_db_path=str(tmp_path / "child.sqlite3"),
    ) as (child_host, child_port):
        child_url = f"http://{child_host}:{child_port}"

        def read_parent(_name, _arguments):
            child = mcp.remote_controller_request(
                child_url,
                _remote_request("child_mutate", {"confirm": True, "dry_run": False, "step": 1}),
                child_token,
            )
            return {"ok": True, "data": {"child": child}}

        with running_controller(
            list_tools_func=lambda: _tool_catalog("read_proxy"),
            call_tool_func=read_parent,
            idempotency_db_path=str(tmp_path / "parent.sqlite3"),
        ) as (parent_host, parent_port):
            status, _, outer, _ = _request(
                parent_host,
                parent_port,
                "POST",
                "/mcp",
                body=_outer_request("read_proxy", {}),
                headers={"X-Anvil-Idempotency-Key": "keyed-read-1"},
            )

    assert status == 200
    child_response = _structured(outer)["data"]["child"]
    assert child_response["error"]["data"]["code"] == "idempotency_key_required"
    assert child_calls == []
