"""Hermetic controller boundary tests for the sealed node workload operation."""

from __future__ import annotations

import contextlib
import http.client
import json
import threading
from datetime import datetime, timezone

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.controller import server as controller_server
from anvil_serving.control_plane.controller.http import ControllerError
from anvil_serving.control_plane.controller.store import OperationStore
from anvil_serving.observability.workload_tools import (
    FLEET_WORKLOADS_TOOL_NAME,
    NODE_WORKLOADS_TOOL_NAME,
    fleet_workloads_declaration,
    is_exact_fleet_workloads_declaration,
    node_workloads_declaration,
    workload_failure,
    workload_success,
)
from anvil_serving.observability.fleet_workload_collection import build_fleet_workloads
from anvil_serving.observability.workloads import WorkloadQuery, fleet_result_from_dict


_LEGACY = "controller-legacy-token"
_SCOPED = "controller-workload-token"
_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _policy(tmp_path):
    path = tmp_path / "authorization.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "clients": [
                    {
                        "id": "workload-reader",
                        "scopes": ["workloads:read"],
                        "credential_env": "WORKLOAD_TOKEN",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return str(path)


@contextlib.contextmanager
def _running(tmp_path, **kwargs):
    audits: list[dict] = []
    kwargs.setdefault("env", {"ANVIL_CONTROLLER_TOKEN": _LEGACY, "WORKLOAD_TOKEN": _SCOPED})
    kwargs.setdefault("authorization_policy", _policy(tmp_path))
    kwargs.setdefault("idempotency_db_path", str(tmp_path / "operations.sqlite3"))
    kwargs.setdefault("node_id", "node-a")
    kwargs.setdefault("workload_clock", lambda: _NOW)
    kwargs.setdefault("audit_logger", lambda record: audits.append(record))
    httpd = controller_server.make_server("127.0.0.1", 0, **kwargs)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, audits
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _request(httpd, path, body, *, token=_SCOPED, headers=None, mcp_request=False):
    request_headers = {"Content-Type": "application/json"}
    if token is not None:
        request_headers["Authorization"] = "Bearer " + token
    if headers:
        request_headers.update(headers)
    if mcp_request:
        params = body.get("params")
        if isinstance(params, dict):
            params = dict(params)
            metadata = dict(params.get("_meta") or {})
            metadata.update(
                {
                    "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "workload-test",
                        "version": "1",
                    },
                }
            )
            params["_meta"] = metadata
            body = dict(body)
            body["params"] = params
        request_headers.update(
            {
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": mcp.PROTOCOL_VERSION,
                "Mcp-Method": "tools/call",
                "Mcp-Name": NODE_WORKLOADS_TOOL_NAME,
            }
        )
    host, port = httpd.server_address[:2]
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request("POST", path, json.dumps(body), request_headers)
        response = connection.getresponse()
        raw = response.read()
        return (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            json.loads(raw.decode("utf-8")),
            raw,
        )
    finally:
        connection.close()


def test_node_workload_declaration_is_fresh_and_exact():
    first = node_workloads_declaration()
    second = node_workloads_declaration()
    assert first == second
    assert first is not second
    assert first == {
        "name": "node_workloads",
        "description": "Read bounded node-local workload metadata.",
        "inputSchema": {
            "type": "object",
            "properties": first["inputSchema"]["properties"],
            "additionalProperties": False,
            "required": [],
            "maxProperties": 7,
        },
        "_meta": {"anvil/requiredScope": "workloads:read"},
    }
    assert set(first["inputSchema"]["properties"]) == {
        "owner",
        "kind",
        "state",
        "host",
        "active_only",
        "recent_seconds",
        "limit",
    }
    first["name"] = "changed"
    assert second["name"] == NODE_WORKLOADS_TOOL_NAME


def test_fleet_declaration_shares_fresh_closed_query_schema():
    first = fleet_workloads_declaration()
    second = fleet_workloads_declaration()
    node = node_workloads_declaration()
    assert first["name"] == FLEET_WORKLOADS_TOOL_NAME
    assert first["inputSchema"] == node["inputSchema"]
    assert first["_meta"] == {"anvil/requiredScope": "workloads:read"}
    assert is_exact_fleet_workloads_declaration(first)
    first["inputSchema"]["properties"]["owner"]["enum"].append("private-value")
    first["inputSchema"]["required"].append("context")
    assert not is_exact_fleet_workloads_declaration(first)
    assert second["inputSchema"] == node["inputSchema"]
    assert second["inputSchema"]["maxProperties"] == 7
    assert second["inputSchema"]["required"] == []


def test_fleet_success_preserves_canonical_unavailable_inventory():
    fleet = build_fleet_workloads(("node-a",), WorkloadQuery(), _NOW, {})
    envelope = workload_success(fleet)
    assert set(envelope) == {"ok", "data"}
    assert envelope["ok"] is True
    assert fleet_result_from_dict(envelope["data"]) == fleet


@pytest.mark.parametrize("value", (None, {}, "private-value", object()))
def test_workload_success_refuses_noncanonical_objects(value):
    assert workload_success(value) == workload_failure("workload_source_unavailable")


def test_workload_success_refuses_forged_fleet_without_echo():
    fleet = build_fleet_workloads((), WorkloadQuery(), _NOW, {})
    object.__setattr__(fleet, "status", "private-value")
    assert workload_success(fleet) == workload_failure("workload_source_unavailable")


def test_workload_success_revalidates_nested_host_before_public_output():
    fleet = build_fleet_workloads(("node-a",), WorkloadQuery(), _NOW, {})
    object.__setattr__(fleet.nodes[0], "host", "http://100.64.0.10/private")
    assert workload_success(fleet) == workload_failure("workload_source_unavailable")
    assert workload_success(fleet.nodes[0]) == workload_failure("workload_source_unavailable")


@pytest.mark.parametrize(
    "tools",
    [
        [{"name": "node-workloads", "inputSchema": {}}],
        [node_workloads_declaration(), node_workloads_declaration()],
    ],
)
def test_reserved_catalog_conflicts_fail_without_echo(tmp_path, tools):
    with pytest.raises(ControllerError) as exc:
        controller_server.make_server(
            "127.0.0.1",
            0,
            env={"ANVIL_CONTROLLER_TOKEN": _LEGACY},
            node_id="node-a",
            idempotency_db_path=str(tmp_path / "ops.sqlite3"),
            list_tools_func=lambda: tools,
        )
    assert exc.value.code == "reserved_tool_conflict"
    assert "inputSchema" not in exc.value.message


def test_rest_workload_is_canonical_read_only_and_private(tmp_path):
    calls: list[tuple] = []
    private = "private-caller-value"
    store = OperationStore(str(tmp_path / "ops.sqlite3"))

    def forbidden_callback(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("generic callback must not run")

    store.claim = forbidden_callback
    store.complete = forbidden_callback
    store.lookup = forbidden_callback

    with _running(
        tmp_path,
        operation_store=store,
        call_tool_func=forbidden_callback,
    ) as (httpd, audits):
        status, headers, body, raw = _request(
            httpd,
            "/tools/call",
            {"name": "node-workloads", "arguments": {"limit": 7}},
            headers={"X-Request-ID": private},
        )
        collector = httpd.anvil_workload_collector
        assert collector is not None
    assert status == 200
    assert body["ok"] is True
    assert set(body) == {"ok", "data"}
    assert body["data"]["host"] == "node-a"
    assert body["data"]["schema"] == "anvil-workloads/v1"
    assert private.encode() not in raw
    assert headers["x-request-id"] != private
    assert calls == []
    assert collector._closed is True
    workload_audits = [record for record in audits if record.get("event") == "workload_read"]
    assert len(workload_audits) == 1
    assert set(workload_audits[0]) == {
        "event",
        "operation",
        "status",
        "ok",
        "error_code",
        "elapsed_ms",
    }
    assert workload_audits[0]["error_code"] is None
    assert private not in json.dumps(workload_audits)


@pytest.mark.parametrize(
    ("token", "body", "headers", "status", "code"),
    [
        (_LEGACY, {"name": "node_workloads", "arguments": {}}, {}, 403, "authorization_scope_denied"),
        (_SCOPED, {"name": "node_workloads", "arguments": None}, {}, 200, "invalid_workload_request"),
        (_SCOPED, {"name": "node_workloads", "arguments": {"limit": True}}, {}, 200, "invalid_workload_query"),
        (_SCOPED, {"name": "node_workloads", "arguments": {}, "context": {}}, {}, 200, "invalid_workload_request"),
        (_SCOPED, {"name": "node_workloads", "arguments": {}}, {"X-Anvil-Idempotency-Key": ""}, 200, "idempotency_not_supported"),
    ],
)
def test_rest_refusals_precede_clock_and_collection(
    tmp_path, token, body, headers, status, code
):
    clock_calls = []

    def clock():
        clock_calls.append(True)
        return _NOW

    with _running(tmp_path, workload_clock=clock) as (httpd, _):
        actual_status, _, response, _ = _request(
            httpd, "/tools/call", body, token=token, headers=headers
        )
    assert actual_status == status
    assert response == {
        "ok": False,
        "error": {
            "code": code,
            "message": {
                "authorization_scope_denied": "workload request is not authorized",
                "invalid_workload_request": "workload request is invalid",
                "invalid_workload_query": "workload query is invalid",
                "idempotency_not_supported": "workload reads do not support idempotency keys",
            }[code],
        },
    }
    assert clock_calls == []


def test_mcp_matches_rest_data_and_keeps_id_only_in_outer_wrapper(tmp_path):
    private = "private-context-value"
    with _running(tmp_path) as (httpd, audits):
        _, _, rest, _ = _request(
            httpd,
            "/tools/call",
            {"name": "node_workloads", "arguments": {}},
        )
        status, _, response, raw = _request(
            httpd,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": "rpc-7",
                "method": "tools/call",
                "params": {
                    "name": "node_workloads",
                    "arguments": {},
                    "_meta": {"private": private},
                },
            },
            mcp_request=True,
        )
    assert status == 200
    assert response["id"] == "rpc-7"
    structured = response["result"]["structuredContent"]
    assert structured == rest
    assert response["result"]["content"][0]["type"] == "text"
    assert json.loads(response["result"]["content"][0]["text"]) == rest
    assert raw.count(b"rpc-7") == 1
    assert private.encode() not in raw
    assert private not in json.dumps(audits)


@pytest.mark.parametrize("rpc_id", [None, True, 2**53, "bad id", "x" * 97])
def test_mcp_invalid_correlation_refuses_before_clock(tmp_path, rpc_id):
    clock_calls = []
    with _running(
        tmp_path,
        workload_clock=lambda: clock_calls.append(True) or _NOW,
    ) as (httpd, _):
        status, _, response, _ = _request(
            httpd,
            "/mcp",
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "method": "tools/call",
                "params": {"name": "node_workloads", "arguments": {}},
            },
            mcp_request=True,
        )
    assert status == 200
    assert response == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {
            "code": -32600,
            "message": "workload protocol request is invalid",
            "data": {"code": "invalid_workload_request"},
        },
    }
    assert clock_calls == []


def test_missing_or_invalid_node_disables_collection_but_seals_name(tmp_path):
    for node_id in (None, "bad node"):
        with _running(tmp_path, node_id=node_id) as (httpd, _):
            status, _, response, _ = _request(
                httpd,
                "/tools/call",
                {"name": "node_workloads", "arguments": {}},
            )
            assert httpd.anvil_workload_collector is None
        assert status == 200
        assert response["error"]["code"] == "workload_source_unavailable"


def test_keepalive_workload_mode_does_not_change_following_health_audit(tmp_path):
    with _running(tmp_path) as (httpd, audits):
        host, port = httpd.server_address[:2]
        connection = http.client.HTTPConnection(host, port, timeout=10)
        try:
            headers = {
                "Authorization": "Bearer " + _SCOPED,
                "Content-Type": "application/json",
            }
            connection.request(
                "POST",
                "/tools/call",
                json.dumps({"name": "node_workloads", "arguments": {}}),
                headers,
            )
            assert connection.getresponse().read()
            connection.request("GET", "/health", headers={"Authorization": "Bearer " + _LEGACY})
            health = connection.getresponse()
            assert health.status == 200
            assert health.read()
        finally:
            connection.close()
    assert audits[0]["event"] == "workload_read"
    assert audits[-1]["operation"] == "health"
    assert "request_id" in audits[-1]
    assert set(audits[-1]) != set(audits[0])


def test_exact_supplied_declaration_is_reused_once(tmp_path):
    with _running(
        tmp_path,
        list_tools_func=lambda: [node_workloads_declaration()],
    ) as (httpd, _):
        host, port = httpd.server_address[:2]
        connection = http.client.HTTPConnection(host, port, timeout=10)
        try:
            connection.request("GET", "/tools/list", headers={"Authorization": "Bearer " + _SCOPED})
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()
    assert response.status == 200
    assert body["tools"] == [node_workloads_declaration()]


def test_bind_failure_closes_constructed_collector(tmp_path, monkeypatch):
    instances = []

    class FakeCollector:
        def __init__(self, host, readers, *, monotonic):
            self.closed = False
            instances.append(self)

        def close(self):
            self.closed = True

    class RefusingServer:
        def __init__(self, address, handler):
            raise OSError("private bind detail")

    monkeypatch.setattr(controller_server, "NodeWorkloadCollector", FakeCollector)
    monkeypatch.setattr(controller_server, "build_workload_readers", lambda *args, **kwargs: {})
    with pytest.raises(OSError):
        controller_server.make_server(
            "127.0.0.1",
            0,
            env={"ANVIL_CONTROLLER_TOKEN": _LEGACY},
            node_id="node-a",
            idempotency_db_path=str(tmp_path / "ops.sqlite3"),
            server_class=RefusingServer,
        )
    assert len(instances) == 1
    assert instances[0].closed is True
