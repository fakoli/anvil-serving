"""Controller tests for the sealed declared-fleet workload operation."""

from __future__ import annotations

import contextlib
import http.client
import json
import threading
from datetime import datetime, timezone

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.controller import server as controller_server
from anvil_serving.observability.fleet_workload_collection import build_fleet_workloads


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
LEGACY = "controller-legacy-token"
SCOPED = "controller-workload-token"


class _FleetCollector:
    def __init__(self) -> None:
        self.calls = []
        self.closed = False

    def collect(self, query, now):
        self.calls.append((query, now))
        return build_fleet_workloads(("node-a",), query, now, {})

    def close(self):
        self.closed = True


def _policy(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"schema_version": 1, "clients": [{
        "id": "reader", "scopes": ["workloads:read"], "credential_env": "WORKLOAD_TOKEN",
    }]}), encoding="utf-8")
    return str(path)


@contextlib.contextmanager
def _server(tmp_path, monkeypatch, *, collector=None, topology="fleet.toml", allowed_operations=None):
    collector = collector or _FleetCollector()
    monkeypatch.setattr(controller_server, "create_fleet_workload_collector", lambda *args, **kwargs: collector)
    audits = []
    server = controller_server.make_server(
        "127.0.0.1", 0,
        env={"ANVIL_CONTROLLER_TOKEN": LEGACY, "WORKLOAD_TOKEN": SCOPED},
        authorization_policy=_policy(tmp_path), node_id="node-a",
        workload_fleet_topology=topology, workload_clock=lambda: NOW,
        idempotency_db_path=str(tmp_path / "ops.sqlite3"), audit_logger=audits.append,
        allowed_operations=allowed_operations,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, collector, audits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def _post(server, path, payload, *, token=SCOPED, headers=None):
    request_headers = {"Content-Type": "application/json", "Authorization": "Bearer " + token}
    request_headers.update(headers or {})
    connection = http.client.HTTPConnection(*server.server_address[:2], timeout=5)
    try:
        connection.request("POST", path, json.dumps(payload), request_headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def test_rest_fleet_workload_is_scoped_sealed_and_audited(monkeypatch, tmp_path):
    with _server(tmp_path, monkeypatch) as (server, collector, audits):
        status, body = _post(server, "/tools/call", {"name": "fleet-workloads", "arguments": {}})
    assert status == 200 and body["ok"] is True
    assert len(collector.calls) == 1 and collector.closed
    assert body["data"]["nodes"][0]["host"] == "node-a"
    audit = [entry for entry in audits if entry.get("event") == "workload_read"]
    assert audit[-1]["operation"] == "fleet_workloads"
    assert set(audit[-1]) == {"event", "operation", "status", "ok", "error_code", "elapsed_ms"}


@pytest.mark.parametrize("token", (LEGACY, "wrong-token"))
def test_fleet_scope_denial_precedes_collector(monkeypatch, tmp_path, token):
    with _server(tmp_path, monkeypatch) as (server, collector, _):
        status, body = _post(server, "/tools/call", {"name": "fleet_workloads", "arguments": {}}, token=token)
    assert status in (401, 403)
    assert collector.calls == []
    assert body["error"]["code"] in {"authentication_error", "authorization_scope_denied"}


def test_mcp_fleet_matches_rest_and_uses_only_protocol_correlation(monkeypatch, tmp_path):
    with _server(tmp_path, monkeypatch) as (server, collector, _):
        _, rest = _post(server, "/tools/call", {"name": "fleet_workloads", "arguments": {}})
        status, rpc = _post(
            server, "/mcp", {"jsonrpc": "2.0", "id": "rpc-1", "method": "tools/call", "params": {
                "name": "fleet_workloads", "arguments": {}, "_meta": {
                    "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                },
            }}, headers={
                "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": mcp.PROTOCOL_VERSION,
                "Mcp-Method": "tools/call", "Mcp-Name": "fleet_workloads",
            },
        )
    assert status == 200 and rpc["id"] == "rpc-1"
    assert rpc["result"]["structuredContent"] == rest
    assert len(collector.calls) == 2


def test_absent_or_invalid_fleet_configuration_stays_reserved_unavailable(monkeypatch, tmp_path):
    with _server(tmp_path, monkeypatch, topology=None) as (server, _collector, _):
        status, body = _post(server, "/tools/call", {"name": "fleet_workloads", "arguments": {}})
        assert server.anvil_fleet_workload_collector is None
    assert status == 200
    assert body["error"]["code"] == "workload_source_unavailable"


def test_allowlist_hides_fleet_and_both_collectors_close_when_one_raises(monkeypatch, tmp_path):
    closed = []

    class BadFleet(_FleetCollector):
        def close(self):
            closed.append("fleet")
            raise RuntimeError("private")

    with _server(tmp_path, monkeypatch, collector=BadFleet(), allowed_operations=("controller-status",)) as (server, collector, _):
        status, body = _post(server, "/tools/call", {"name": "fleet_workloads", "arguments": {}})
        assert server.anvil_fleet_workload_collector is collector
    assert status == 200 and body["error"]["code"] == "invalid_workload_request"
    assert closed == ["fleet"]
