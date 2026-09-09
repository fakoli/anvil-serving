"""App-side Observatory invariants for the pure Connect migration preview."""
from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import threading
from pathlib import Path

import pytest

from anvil_serving.connect.migration import MigrationError, preview_observatory
from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.dashboard.console import Console, attach_console
from anvil_serving.observability.dashboard.contracts import digest

ROOT = Path(__file__).parents[2]
HOST = "dash.example.test"
ORIGIN = "https://" + HOST


class Owner:
    def __init__(self) -> None:
        self.preview_calls = 0
        self.gate = threading.Event()
        self.gate.set()

    def controls(self, resource: str) -> dict:
        return {"resource_id": resource, "baseline_digest": digest(1), "actions": [{"id": "tier.quiesce", "label": "fixture", "supported": True}], "settings": []}

    def preview(self, resource: str, action: str, *, values=None, parameters=None) -> dict:
        self.preview_calls += 1
        return {"host_id": "host-fixture", "resource_id": resource, "action_id": action, "label": "fixture", "baseline_digest": digest(1), "candidate_digest": digest({}), "effect": "fixture", "diff": [], "recovery": "fixture rollback"}

    def execute(self, preview: dict, intent_key: str) -> dict:
        return {"ok": True, "owner_operation_id": intent_key, "native_state": "succeeded", "execution_outcome": "succeeded"}

    def reconcile(self, preview: dict, intent_key: str):
        return None

    def verify(self, preview: dict, result: dict) -> dict:
        return {"status": "passed", "message": "fixture"}


def _forwarder(target, counts, name):
    class Handler(BaseHTTPRequestHandler):
        def _serve(self):
            counts[name] += 1
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else None
            headers = {key: value for key, value in self.headers.items() if key.lower() not in {"connection", "content-length"}}
            upstream = http.client.HTTPConnection("127.0.0.1", target()[1], timeout=5)
            upstream.request(self.command, self.path, body=body, headers=headers)
            response = upstream.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "date", "server", "transfer-encoding", "content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            upstream.close()

        do_GET = _serve
        do_POST = _serve

        def log_message(self, *_):
            pass
    return HTTPServer(("127.0.0.1", 0), Handler)


@pytest.fixture
def observatory(tmp_path: Path):
    owner = Owner()
    config = {"origin": ORIGIN, "base_path": "/", "operate": True, "fixture": True, "strip_prefix": False, "users": [{"id": "operator-fixture", "username": "operator", "role": "operator", "resources": ["dashboard"], "actions": ["tier.quiesce"]}], "authentication": {}, "state_path": str(tmp_path / "journal.sqlite")}
    console = Console(config, metrics=object(), adapter=owner, authenticate=lambda username, password: username == "operator" and password == "fixture-password")
    app = create_dashboard_server(TelemetryRegistry(), port=0)
    attach_console(app, console)
    counts = {"route-a": 0, "route-b": 0, "front": 0}
    route_a = _forwarder(lambda: app.server_address, counts, "route-a")
    route_b = _forwarder(lambda: app.server_address, counts, "route-b")
    active = {"route": route_a}
    front = _forwarder(lambda: active["route"].server_address, counts, "front")
    servers = (app, route_a, route_b, front)
    threads = [run_server_in_thread(server) for server in servers]
    session: dict[str, str] = {}

    def call(method: str, path: str, body=None, *, host=HOST, origin=ORIGIN, cookie=True, csrf=True, csrf_value=None, extra=None):
        connection = http.client.HTTPConnection("127.0.0.1", front.server_address[1], timeout=5)
        headers = {"Host": host, "Origin": origin}
        if cookie and session:
            headers["Cookie"] = session["cookie"]
        if csrf and session:
            headers["X-CSRF-Token"] = session["csrf"] if csrf_value is None else csrf_value
        raw = None if body is None else json.dumps(body)
        if raw is not None:
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        value = json.loads(response.read())
        if path == "/api/observatory/v1/session" and method == "POST" and response.status == 200:
            session["cookie"] = response.getheader("Set-Cookie").split(";", 1)[0]
            session["csrf"] = value["data"]["csrf_token"]
        status = response.status
        connection.close()
        return status, value

    yield owner, call, active, route_a, route_b, counts
    for server in servers:
        server.shutdown(); server.server_close()
    for thread in threads:
        thread.join()
    console.close()


def deployment(tmp_path: Path) -> tuple[Path, Path, dict]:
    data = json.loads((ROOT / "connect/examples/deployment.json").read_text(encoding="utf-8"))
    # Preview validates schema only; paths remain generic and have no secrets.
    manifest = tmp_path / "deployment.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    config = {"schema": "anvil-observatory/config/v1", "origin": ORIGIN, "base_path": "/", "users": [], "authentication": {}, "inventory": {}, "prometheus_url": "http://127.0.0.1:9090", "state_path": str(tmp_path / "state.sqlite"), "fixture": True, "strip_prefix": False}
    observatory = tmp_path / "observatory.json"
    observatory.write_text(json.dumps(config), encoding="utf-8")
    return manifest, observatory, config


def test_preview_has_one_canonical_origin_and_ordered_safe_cutover(tmp_path: Path):
    manifest, config, _ = deployment(tmp_path)
    preview = preview_observatory(manifest, config, resource_id="dashboard")
    assert preview["applied"] is False
    assert preview["canonical_origin"] == ORIGIN
    assert preview["resource"] == {"id": "dashboard", "host": HOST, "path_prefix": "/", "base_path": "/", "access": "browser", "native_auth": "passthrough", "connector": "dashboard", "declared_loopback_origin": "http://127.0.0.1:18080"}
    assert [step["order"] for step in preview["steps"]] == [1, 2, 3, 4, 5, 6]
    assert "two-origin" not in json.dumps(preview).lower()
    assert preview["steps"][-1]["action"].startswith("retire-the-prior")


def test_origin_change_is_refused_instead_of_claiming_preserved_sessions(tmp_path: Path):
    manifest, config, values = deployment(tmp_path)
    values["origin"] = "https://old-console.example.test"
    config.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(MigrationError, match="cookie re-login"):
        preview_observatory(manifest, config, resource_id="dashboard")


def test_real_observatory_session_csrf_and_action_grants_hold_across_route_switch_and_rollback(tmp_path: Path, observatory):
    owner, call, active, route_a, route_b, counts = observatory
    manifest, config, _ = deployment(tmp_path)
    preview = preview_observatory(manifest, config, resource_id="dashboard")
    assert preview["rollback"][2]["action"].startswith("retain-Observatory")
    assert call("POST", "/api/observatory/v1/session", {"username": "operator", "password": "fixture-password"})[0] == 200

    for route in (route_a, route_b, route_a):
        active["route"] = route
        assert call("POST", "/api/observatory/v1/previews", {"resource_id": "dashboard", "action_id": "tier.quiesce"})[0] == 200
        assert call("POST", "/api/observatory/v1/previews", {"resource_id": "dashboard", "action_id": "tier.quiesce"}, origin="https://evil.example.test")[0] == 403
        assert call("POST", "/api/observatory/v1/previews", {"resource_id": "dashboard", "action_id": "tier.quiesce"}, csrf=False)[0] == 403
        assert call("POST", "/api/observatory/v1/previews", {"resource_id": "dashboard", "action_id": "tier.quiesce"}, csrf_value="forged")[0] == 403
        assert call("POST", "/api/observatory/v1/previews", {"resource_id": "other", "action_id": "tier.quiesce"})[0] == 403
        assert call("POST", "/api/observatory/v1/previews", {"resource_id": "dashboard", "action_id": "tier.stop"})[0] == 403
        assert call("POST", "/api/observatory/v1/previews", {"resource_id": "dashboard", "action_id": "tier.quiesce"}, cookie=False, csrf=False, extra={"Authorization": "Bearer forged", "X-Api-Key": "forged"})[0] == 401

    # route-a is the restored route. The application instance and session were
    # never replaced; this is an app-side fixed-origin forwarding invariant.
    assert owner.preview_calls == 3
    assert counts["route-a"] > 0 and counts["route-b"] > 0


def test_preview_refuses_symlink_fifo_and_oversized_observatory_config(tmp_path: Path):
    manifest, config, values = deployment(tmp_path)
    target = tmp_path / "target.json"
    target.write_text(json.dumps(values), encoding="utf-8")
    link = tmp_path / "observatory-link.json"
    link.symlink_to(target)
    with pytest.raises(MigrationError, match="unavailable"):
        preview_observatory(manifest, link, resource_id="dashboard")
    fifo = tmp_path / "observatory.fifo"
    os.mkfifo(fifo)
    with pytest.raises(MigrationError, match="regular"):
        preview_observatory(manifest, fifo, resource_id="dashboard")
    config.write_bytes(b"{" + b" " * 262_144)
    with pytest.raises(MigrationError, match="bounded regular|exceeds"):
        preview_observatory(manifest, config, resource_id="dashboard")


def test_preview_requires_exact_non_stripping_path_and_observatory_methods(tmp_path: Path):
    manifest, config, values = deployment(tmp_path)
    values.pop("strip_prefix")
    config.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(MigrationError, match="non-stripping"):
        preview_observatory(manifest, config, resource_id="dashboard")
    values["strip_prefix"] = False
    config.write_text(json.dumps(values), encoding="utf-8")
    deployment_data = json.loads(manifest.read_text())
    deployment_data["gateway"]["gateway"]["resources"][0]["rule"]["methods"] = ["GET"]
    deployment_data["connectors"][0]["resources"][0]["envelope"]["rule"]["methods"] = ["GET"]
    manifest.write_text(json.dumps(deployment_data), encoding="utf-8")
    with pytest.raises(MigrationError, match="GET and POST"):
        preview_observatory(manifest, config, resource_id="dashboard")


@pytest.mark.parametrize("base_path", ("//", "/ops//", "/ops/./", "/ops/../", "/ops%2f/", "/ops\x01/", "/_anvil-connect/"))
def test_preview_refuses_normalized_or_reserved_app_paths(tmp_path: Path, base_path: str):
    manifest, config, values = deployment(tmp_path)
    values["base_path"] = base_path
    config.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(MigrationError, match="path mapping|reserved Connect"):
        preview_observatory(manifest, config, resource_id="dashboard")
