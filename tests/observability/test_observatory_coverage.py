"""Independent telemetry and owner observations through the real fleet facade."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from anvil_serving.observability.dashboard.access import Principal, Session
from anvil_serving.observability.dashboard.console import Console
from anvil_serving.observability.dashboard.contracts import ObservatoryError
from anvil_serving.observability.dashboard.controller_adapter import ControllerAdapter
from anvil_serving.observability.dashboard.metrics_client import MetricsClient
from anvil_serving.transports import TransportError, TransportResult


NOW = 1000000
INVENTORY = {
    "hosts": [{"id": "host-a", "display_name": "Host A", "platform": "linux",
               "metric_host": "metric-host-a", "gpus": []}],
    "serves": [{"id": "serve-a", "host_id": "host-a", "model": "fixture/model-a",
                "engine": "sglang", "metric_serve": "metric-serve-a", "gpu_ids": [],
                "aliases": ["primary"]}],
}


class OwnerTransport:
    """Only owner metadata reads exist in this synthetic transport."""

    def __init__(self, endpoint, **kwargs):
        self.available = True
        self.model = "fixture/model-a"
        self.calls = []

    def tool_catalog(self):
        if not self.available:
            raise TransportError("unavailable", "Synthetic owner is offline")
        return ({"name": "serves_status", "inputSchema": {"type": "object"}},)

    def execute(self, operation, **kwargs):
        self.calls.append(operation)
        assert self.available and operation.name == "serves_status"
        return TransportResult(operation.name, "controller", {"ok": True, "data": {
            "serves": [{"name": "chat", "running": True, "health_status": 200,
                        "model": self.model, "engine": "sglang"}],
        }})


@pytest.fixture
def facade(tmp_path):
    # Exercise real HTTP success/freshness tracking, not a mocked integration status.
    class Prometheus(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            data = {"resultType": "vector", "result": [
                {"metric": {"observatory_field": "value"}, "value": [NOW, "21"]},
                {"metric": {"observatory_field": "timestamp"}, "value": [NOW, str(NOW - 5)]},
            ]}
            body = json.dumps({"status": "success", "data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Prometheus)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    metrics = MetricsClient(prometheus_url=f"http://127.0.0.1:{server.server_port}",
                            inventory=INVENTORY, clock=lambda: NOW)
    owner_config = {
        "controller": {"url": "https://controller.example.invalid", "expected_node": "host-a",
                       "token_env": "FIXTURE_CONTROLLER_TOKEN", "topology": "fixture",
                       "execution_host": "host-a", "execution_runtime": "native"},
        "resources": [{"id": "serve-a", "label": "Chat", "host_id": "host-a", "kind": "serve",
                       "manifest": "/private/fixture/serves.toml", "serve": "chat", "tier": "primary",
                       "aliases": ["primary"], "gpu_ids": []}],
    }
    adapter = ControllerAdapter(owner_config, {}, transport_factory=OwnerTransport, clock=lambda: NOW)
    actions = ["serve.start", "serve.stop", "serve.restart", "serve.probe",
               "tier.quiesce", "tier.drain", "tier.readmit"]
    user = {"id": "operator", "username": "operator", "role": "operator",
            "resources": ["*"], "actions": actions}
    config = {"origin": "https://console.example.invalid", "base_path": "/observatory/",
              "operate": True, "users": [user], "authentication": {},
              "state_path": str(tmp_path / "journal.sqlite"), "controller": owner_config, "fixture": True}
    console = Console(config, adapter=adapter, metrics=metrics, authenticate=lambda u, p: False)
    session = Session("fixture-session", "fixture-csrf",
                      Principal("operator", "operator", "operator", frozenset({"*"}), frozenset(actions)),
                      NOW + 1000)
    try:
        yield console, adapter._transport, session, owner_config
    finally:
        console.close()
        metrics.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_fresh_prometheus_survives_unavailable_controller_with_all_controls_closed(facade):
    console, owner, session, _ = facade
    owner.available = False
    fleet = console.fleet(session)
    host, serve = fleet["hosts"][0], fleet["serves"][0]
    assert console.metrics.integration_status()["status"] == "ok"
    assert fleet["observed_at"] == NOW
    assert host["telemetry"] == {"status": "ok", "observed_at": NOW - 5}
    assert host["resources"]["cpu_utilization"]["value"] == 21
    assert host["controller"]["status"] == "unavailable"
    generation = serve["metrics"]["generation"]
    assert generation["value"] == 21 and generation["status"] == "fresh"
    assert generation["source_timestamp"] == NOW - 5
    assert serve["runtime_state"] == serve["readiness"] == "unknown"
    assert fleet["coverage"]["status"] == "partial"
    assert fleet["coverage"]["controller_unavailable_hosts"] == 1
    assert fleet["coverage"]["reason"]
    actions = console.controls(session, "serve-a")["actions"]
    assert actions
    for action in actions:
        assert action["supported"] is False and action["permitted"] is False
        assert action["reason"]
        with pytest.raises(ObservatoryError, match="not available"):
            console.create_preview(session, {"resource_id": "serve-a", "action_id": action["id"]})
    assert owner.calls == []


def test_changed_owner_model_metadata_preserves_alias_and_unverified_identity(facade):
    console, owner, session, binding = facade
    original_binding = copy.deepcopy(binding)
    before = console.fleet(session)["serves"][0]
    owner.model = "fixture/model-b"
    # The native status model is configuration metadata, not a verified /v1/models observation.
    owner_view = console.adapter.snapshot()["serves"][0]
    after = console.fleet(session)["serves"][0]
    assert owner_view["model"] == "fixture/model-b"
    assert before["aliases"] == owner_view["aliases"] == after["aliases"] == ["primary"]
    assert before["model"] == after["model"] == "fixture/model-a"
    assert before["observed_model"] is owner_view["observed_model"] is after["observed_model"] is None
    assert after["runtime_state"] == "running" and after["readiness"] == "ready"
    assert binding == original_binding
    assert len(owner.calls) == 3
    assert all(call.name == "serves_status" and call.arguments == {
        "manifest": "/private/fixture/serves.toml", "names": ["chat"]} for call in owner.calls)


def test_fleet_exposes_owner_proven_diagnostics_and_withdraws_unavailable_commands(facade, monkeypatch):
    console, _, session, _ = facade
    owner_view = console.adapter.snapshot()
    serve = owner_view["serves"][0]
    serve.update(exec={"status": "available", "commands": ["gpu-status"]}, private_owner_field="not-public")
    monkeypatch.setattr(console.adapter, "snapshot", lambda: owner_view)
    observed = console.fleet(session)["serves"][0]
    assert observed["exec"] == {"status": "available", "commands": ["gpu-status"]}
    assert "private_owner_field" not in observed
    serve["exec"] = {"status": "unavailable", "commands": []}
    assert console.fleet(session)["serves"][0]["exec"] == {"status": "unavailable", "commands": []}
