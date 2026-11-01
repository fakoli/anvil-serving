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
    NODE_WORKLOADS_TOOL_NAME,
    node_workloads_declaration,
    workload_failure,
    workload_success,
)
from anvil_serving.observability.fleet_workload_collection import build_fleet_workloads
from anvil_serving.observability.workloads import WorkloadQuery


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
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


def test_workload_success_refuses_forged_fleet_without_echo():
    fleet = build_fleet_workloads((), WorkloadQuery(), _NOW, {})
    object.__setattr__(fleet, "status", "private-value")
    assert workload_success(fleet) == workload_failure("workload_source_unavailable")


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
