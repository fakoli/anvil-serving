"""Exercise the real HTTP/router/state seam with independent synthetic serves."""
import http.client
import json
import os
import threading

import pytest

from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router import route_updates
from anvil_serving.router.admission import RouterAdmission
from anvil_serving.router.route_updates import initialize_route_state
from anvil_serving.router import serve as serve_module
from anvil_serving.router.serve import build_server
from anvil_serving.router.keys import KeyStore
from anvil_serving.router.usage_store import UsageStore
from tests.router.helpers import StaticBackend
from tests.router.test_route_state import Runtime, configuration

pytestmark = pytest.mark.skipif(os.name != "posix", reason="route authority requires POSIX durability")


class ExactIdentity:
    def check(self, tier):
        return AvailabilityResult(True, "ready", "identity_passed", tier.model, tier.model)

    def invalidate(self, tier_id):
        pass


@pytest.fixture
def live_routes(tmp_path):
    state_path = tmp_path / "routes.json"
    raw = configuration().replace(b"[server]", f'[server]\nroute_state_path = "{state_path}"'.encode())
    config_path = tmp_path / "router.toml"
    config_path.write_bytes(raw)
    initialize_route_state(raw, state_path)
    server = build_server(str(config_path), port=0, env={"ROUTER_TOKEN": "test-route-operator"},
                          backends={"a": StaticBackend(["first"]), "b": StaticBackend(["second"])},
                          availability=ExactIdentity())
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield server, raw
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_build_server_restores_prepared_replacement_with_usage_ledger_wired_early(tmp_path, monkeypatch):
    state_path = tmp_path / "routes.json"
    keys_path = tmp_path / "keys.sqlite3"
    admission_path = tmp_path / "admission.json"
    raw = configuration().replace(
        b"[server]", (
            b"[server]\nroute_state_path = " + json.dumps(str(state_path)).encode()
            + b"\napi_keys_path = " + json.dumps(str(keys_path)).encode()
            + b"\nadmission_state_path = " + json.dumps(str(admission_path)).encode()
            + b'\nrouter_owner_id = "fixture"\nrouter_owner_roster = ["fixture"]'
            + b'\nusage_domain_id = "fixture"\nusage_enabled = true'
        ),
    )
    config_path = tmp_path / "router.toml"
    config_path.write_bytes(raw)
    initialize_route_state(raw, state_path)

    # Persist a prepared replacement as the predecessor process would. Its
    # Runtime is only a state writer; the restarted path below is build_server.
    stager = route_updates.RouteUpdateManager(Runtime(raw), raw, state_path)
    try:
        stager.restore()
        preview = stager.preview(raw.replace(b'model = "model-a"', b'model = "replacement"'))
        assert stager.apply(preview["preview_id"], confirm=True, prepare_only=True)["state"] == "held"
    finally:
        stager.close()

    usage = UsageStore(KeyStore.initialize(keys_path))
    usage.migrate()
    owner = RouterAdmission("fixture-revision")
    owner.usage_scope = lambda: object()
    owner.usage_run_id = "fixture-run"
    monkeypatch.setattr(serve_module, "managed_router_admission",
                        lambda _settings, _revision: (owner, usage, owner.usage_run_id))
    server = build_server(
        str(config_path), port=0, env={"ROUTER_TOKEN": "test-route-operator"},
        backends={"a": StaticBackend(["first"]), "b": StaticBackend(["second"])},
        availability=ExactIdentity(),
    )
    try:
        assert server.anvil_route_manager.status()["state"] == "held"
    finally:
        server.server_close()


def request(server, path, body=None, *, token="test-route-operator"):
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    try:
        connection.request("GET" if body is None else "POST", path,
                           None if body is None else json.dumps(body), headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_http_alias_apply_preserves_boot_identity_and_changes_new_requests(live_routes):
    server, raw = live_routes
    status, before = request(server, "/v1/router/status")
    assert status == 200
    chat = {"model": "llm.primary", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    status, response = request(server, "/v1/chat/completions", chat)
    assert status == 200 and response["choices"][0]["message"]["content"] == "first"
    candidate = raw.replace(b'"llm.primary" = "a"', b'"llm.primary" = "b"')
    status, preview = request(server, "/v1/router/routes/preview", {"config": candidate.decode()})
    assert status == 200 and preview["mode"] == "alias_only"
    status, result = request(server, "/v1/router/routes/apply", {"preview_id": preview["preview_id"], "confirm": True})
    assert status == 200 and result["route_revision"] == 1
    status, response = request(server, "/v1/chat/completions", chat)
    assert status == 200 and response["choices"][0]["message"]["content"] == "second"
    _, after = request(server, "/v1/router/status")
    assert after["config_sha256"] == before["config_sha256"]
    assert after["route_revision"] == 1 and after["effective_route_sha256"] != before["effective_route_sha256"]
    _, capabilities = request(server, "/v1/models/capabilities")
    assert (capabilities["route_revision"], capabilities["effective_route_sha256"]) == (
        after["route_revision"], after["effective_route_sha256"])


def test_http_auth_confirmation_and_body_boundaries(live_routes):
    server, raw = live_routes
    for path, body in (("/v1/router/routes/status", None), ("/v1/router/routes/preview", {"config": raw.decode()})):
        assert request(server, path, body, token=None)[0] == 401
    assert request(server, "/v1/router/routes/preview", {"config_path": "/private/path"})[0] == 400
    assert request(server, "/v1/router/routes/apply", {"preview_id": [], "confirm": True})[0] == 400
    status, preview = request(server, "/v1/router/routes/preview", {"config": raw.decode()})
    assert status == 200
    assert request(server, "/v1/router/routes/apply", {"preview_id": preview["preview_id"], "confirm": "true"})[0] == 400


def test_duplicate_confirmation_is_rejected_without_consuming_preview(live_routes):
    server, raw = live_routes
    candidate = raw.replace(b'"llm.primary" = "a"', b'"llm.primary" = "b"')
    _, preview = request(server, "/v1/router/routes/preview", {"config": candidate.decode()})
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request("POST", "/v1/router/routes/apply",
                           '{"preview_id":"' + preview["preview_id"] + '","confirm":false,"confirm":true}',
                           {"Authorization": "Bearer test-route-operator", "Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 400
        response.read()
    finally:
        connection.close()
    _, unchanged = request(server, "/v1/router/routes/status")
    assert unchanged["route_revision"] == 0
    status, applied = request(server, "/v1/router/routes/apply", {"preview_id": preview["preview_id"], "confirm": True})
    assert status == 200 and applied["route_revision"] == 1


def test_device_key_is_denied_on_every_route_management_endpoint(tmp_path):
    state_path = tmp_path / "routes.json"
    key_path = tmp_path / "keys.sqlite3"
    raw = configuration().replace(
        b"[server]", f'[server]\nroute_state_path = "{state_path}"\napi_keys_path = "{key_path}"'.encode(),
    )
    config_path = tmp_path / "router.toml"
    config_path.write_bytes(raw)
    initialize_route_state(raw, state_path)
    store = KeyStore.initialize(key_path)
    # A real inference credential has no supported management-path grant.
    _meta, device_key = store.create("device", models=["llm.primary"],
                                     paths=["/v1/chat/completions"], rpm=10)
    server = build_server(str(config_path), port=0, env={"ROUTER_TOKEN": "test-route-operator"},
                          backends={"a": StaticBackend(["first"]), "b": StaticBackend(["second"])},
                          availability=ExactIdentity())
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        for path, body in (("/v1/router/routes/status", None),
                           ("/v1/router/routes/preview", {"config": raw.decode()}),
                           ("/v1/router/routes/apply", {"preview_id": "x", "confirm": True}),
                           ("/v1/router/routes/rollback", {"revision": 0}),
                           ("/v1/router/routes/resume", {"transaction_id": "x"})):
            assert request(server, path, body, token=device_key)[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_active_sse_keeps_old_backend_while_alias_switches_new_requests(live_routes):
    server, raw = live_routes

    class BlockingBackend:
        def __init__(self):
            self.started, self.release = threading.Event(), threading.Event()

        def generate(self, request):
            self.started.set()
            yield "old-one"
            assert self.release.wait(5)
            yield "old-two"

    old = BlockingBackend()
    server.anvil_routing._backends["a"] = old
    server.anvil_routing._active_route_runtime = type(server.anvil_routing._active_route_runtime)(
        0, server.anvil_routing._active_route_runtime.effective_route_sha256,
        server.anvil_routing._active_route_runtime.config,
        {**server.anvil_routing._active_route_runtime.backends, "a": old},
    )
    received = []

    def stream_old():
        conn = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            conn.request("POST", "/v1/chat/completions", json.dumps({
                "model": "llm.primary", "messages": [{"role": "user", "content": "hello"}], "stream": True,
            }), {"Authorization": "Bearer test-route-operator", "Content-Type": "application/json"})
            response = conn.getresponse()
            received.append(response.read())
        finally:
            conn.close()

    worker = threading.Thread(target=stream_old, daemon=True)
    worker.start()
    assert old.started.wait(5)
    candidate = raw.replace(b'"llm.primary" = "a"', b'"llm.primary" = "b"')
    status, preview = request(server, "/v1/router/routes/preview", {"config": candidate.decode()})
    assert status == 200 and preview["mode"] == "alias_only"
    assert request(server, "/v1/router/routes/apply", {"preview_id": preview["preview_id"], "confirm": True})[0] == 200
    status, response = request(server, "/v1/chat/completions", {
        "model": "llm.primary", "messages": [{"role": "user", "content": "new"}], "stream": False,
    })
    assert status == 200 and response["choices"][0]["message"]["content"] == "second"
    old.release.set()
    worker.join(5)
    assert not worker.is_alive() and b"old-one" in received[0] and b"old-two" in received[0]
