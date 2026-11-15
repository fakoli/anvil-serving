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
MEDIA = "controller-media-only-token"


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
        "id": "reader",
        "scopes": ["workloads:read"],
        "credential_env": "WORKLOAD_TOKEN",
    }]}), encoding="utf-8")
    return str(path)


@contextlib.contextmanager
def _server(
    tmp_path,
    monkeypatch,
    *,
    collector=None,
    topology="fleet.toml",
    allowed_operations=None,
    call_tool_func=mcp.call_tool,
    workload_clock=lambda: NOW,
):
    collector = collector or _FleetCollector()
    monkeypatch.setattr(controller_server, "create_fleet_workload_collector", lambda *args, **kwargs: collector)
    audits = []
    server = controller_server.make_server(
        "127.0.0.1", 0,
        env={
            "ANVIL_CONTROLLER_TOKEN": LEGACY,
            "WORKLOAD_TOKEN": SCOPED,
            "MEDIA_TOKEN": MEDIA,
        },
        authorization_policy=_policy(tmp_path), node_id="node-a",
        workload_fleet_topology=topology, workload_clock=workload_clock,
        idempotency_db_path=str(tmp_path / "ops.sqlite3"), audit_logger=audits.append,
        allowed_operations=allowed_operations, call_tool_func=call_tool_func,
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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
