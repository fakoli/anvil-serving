"""Exercise the real HTTP namespace, shared identity and closed request contracts."""

import base64
import hashlib
import hmac
import http.client
import json
import os
import time
import uuid
from types import SimpleNamespace

import pytest

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.dashboard.console import Console, attach_console
from anvil_serving.workbench_app.config import validate_config


class Metrics:
    def snapshot(self):
        return {"hosts": [], "serves": [], "coverage": {"status": "unknown"}}
    def integration_status(self):
        return {"status": "unknown"}


KEY = bytes(range(32))
_SAFE_PROJECT_READS = os.name == "posix" and all(
    hasattr(os, name) for name in ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK")
)


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def assertion(method, target):
    now = int(time.time())
    payload = {"v": 1, "iss": "anvil-connect", "kid": "key", "sub": "alice", "sid": "a" * 32, "sg": 1, "pg": 1,
               "epoch": "b" * 64, "resource": "workbench", "host": "console.example.test", "method": method,
               "target_sha256": hashlib.sha256(target.encode()).hexdigest(), "iat": now, "exp": now + 20,
               "session_exp": now + 300, "jti": uuid.uuid4().hex}
    encoded = b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return "acai1." + encoded + "." + b64(hmac.new(KEY, ("acai1." + encoded).encode(), hashlib.sha256).digest())


@pytest.fixture(params=["legacy", "connect"])
def site(tmp_path, request):
    mode = request.param
    auth = {} if mode == "legacy" else {"mode": "connect", "connect": {"resource": "workbench", "keys": [{"id": "key", "secret_env": "WB_SIGNING_KEY"}], "principals": {"alice": "alice"}}}
    config = {"origin": "https://console.example.test", "base_path": "/workbench/", "operate": True,
              "users": [{"id": "alice", "username": "alice", "role": "operator", "resources": ["serve-a"], "actions": ["playground.request"]}],
              "authentication": auth, "state_path": str(tmp_path / "journal.sqlite"),
              "workbench": {"state_path": str(tmp_path / "private.sqlite"), "connectors": [{"id": "selected", "label": "Selected", "resource_id": "serve-a", "models": ["model-a"], "base_url": "http://127.0.0.1:30000/v1", "token_env": "PRIVATE_MODEL_KEY"}]}}
    console = Console(config, metrics=Metrics(), authenticate=lambda u, p: u == "alice" and p == "test-password", environment={"WB_SIGNING_KEY": b64(KEY), "PRIVATE_MODEL_KEY": "private-marker"})
    server = create_dashboard_server(TelemetryRegistry(), port=0)
    attach_console(server, console)
    thread = run_server_in_thread(server)
    saved = {}
    def call(method, route, body=None, *, namespace="workbench", signed=True, extra=None):
        target = f"/workbench/api/{namespace}/v1/{route}"
        headers = {"Host": "console.example.test", "Origin": config["origin"], **saved}
        if signed and mode == "connect":
            headers["X-Anvil-Connect-Identity"] = assertion(method, target)
        headers.update(extra or {})
        raw = None
        if body is not None:
            raw = json.dumps(body)
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(method, target, body=raw, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        cookie = response.getheader("Set-Cookie")
        if route == "session" and response.status == 200 and cookie:
            saved.update(Cookie=cookie.split(";", 1)[0], **{"X-CSRF-Token": data["data"]["csrf_token"]})
        status = response.status
        connection.close()
        return status, data
    if mode == "legacy":
        assert call("POST", "session", {"username": "alice", "password": "test-password"}, namespace="observatory")[0] == 200
    else:
        assert call("GET", "session", namespace="observatory")[0] == 200
    yield console, call, mode
    server.shutdown()
    server.server_close()
    thread.join()
    console.close()


def test_shared_authenticated_namespace_preserves_secret_boundary(site):
    _, call, _ = site
    status, result = call("GET", "catalog")
    assert status == 200
    assert result["data"]["connectors"][0]["models"] == ["model-a"]
    assert "private-marker" not in json.dumps(result) and "base_url" not in json.dumps(result)
    assert call("GET", "session")[0] == 404  # No second login/session bootstrap.


def test_mutations_reject_cross_origin_csrf_and_unrecognized_fields(site):
    _, call, _ = site
    assert call("POST", "preferences", {"density": "compact"}, extra={"Origin": "https://attacker.example.test"})[0] == 403
    assert call("POST", "preferences", {"density": "compact"}, extra={"X-CSRF-Token": "bad"})[0] == 403
    assert call("POST", "preferences", {"command": "danger"})[0] == 400
    assert call("POST", "preferences", {"density": "compact"})[0] == 200
    assert call("GET", "preferences")[1]["data"] == {"density": "compact"}
    assert call("DELETE", "preferences")[0] == 405


def test_connect_requires_fresh_assertion_bound_to_exact_new_target(site):
    _, call, mode = site
    if mode != "connect":
        pytest.skip("Connect signature boundary")
    assert call("GET", "catalog", signed=False)[0] == 401
    wrong = assertion("GET", "/workbench/api/observatory/v1/catalog")
    assert call("GET", "catalog", extra={"X-Anvil-Connect-Identity": wrong})[0] == 401


@pytest.mark.parametrize("route", ["documents/../private", "projects/undeclared", "pi/sessions", "conversations/not-owned", "catalog?host=a&host=b"])
def test_closed_routes_do_not_open_files_or_unconfigured_runners(site, route):
    _, call, _ = site
    assert call("GET", route)[0] in {400, 404, 409}


def test_config_rejects_browser_style_locations_and_unpinned_runner(tmp_path):
    config = {"state_path": str(tmp_path / "private.sqlite"), "connectors": [{"id": "model", "label": "Model", "resource_id": "serve-a", "models": ["specific"], "base_url": "https://user:secret@example.test/v1"}]}
    with pytest.raises(ValueError):
        validate_config(config)
    with pytest.raises(ValueError):
        validate_config({"state_path": "relative"})


def test_encoded_task_identifier_is_decoded_after_exact_target_auth(site):
    console, call, _ = site
    calls = []
    console.workbench.projects.task = lambda session, project, task: calls.append((project, task)) or {"task": {"id": task}}
    assert call("GET", "projects/product/tasks/plan%3AT001")[1]["data"]["task"]["id"] == "plan:T001"
    assert calls == [("product", "plan:T001")]


def test_pi_storage_failure_is_actionable_and_never_claims_a_task(site, monkeypatch):
    from anvil_serving.workbench_app import pi_storage

    console, call, _ = site
    console.workbench.pi = SimpleNamespace(close=lambda: None)
    console.workbench.config["pi"] = {}
    monkeypatch.setattr(console.workbench.projects, "prepare", lambda *_: pytest.fail("storage failure acquired a task"))

    def unavailable(_):
        raise pi_storage.PiStorageError("private pool details")

    monkeypatch.setattr(pi_storage, "validate_pool", unavailable)
    status, result = call("POST", "pi/sessions", {"project_id": "product", "task_id": "plan:T001",
        "request_id": "start-a", "provider_id": "selected", "model_id": "model-a", "thinking_level": "off"})
    assert status == 409
    assert result["error"]["code"] == "pi_storage_unavailable"
    assert "managed pool mount" in result["error"]["message"]
    assert "private pool details" not in json.dumps(result)


@pytest.mark.parametrize("component", ["plan%2FT001", "%2e%2e", "plan%253AT001", "plan%5cT001", "%ff", "plan%00T001"])
def test_encoded_separators_and_double_encoding_never_reach_adapter(site, component):
    console, call, _ = site
    console.workbench.projects.task = lambda *_: pytest.fail("invalid route reached adapter")
    assert call("GET", f"projects/product/tasks/{component}")[0] == 400


def test_project_root_reads_authorize_before_open_and_keep_paths_server_side(site, tmp_path, monkeypatch):
    console, call, _ = site
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "guide.txt").write_text("Project guide", encoding="utf-8")
    root = {"id": "primary", "label": "Source", "owner_id": "local-owner", "runtime_id": "local-runtime",
            "task_access": "read-write", "path": str(checkout)}
    console.workbench.config["projects"] = [
        {"id": "product", "label": "Product", "resource_id": "serve-a", "roots": [root]},
        {"id": "private", "label": "Private", "resource_id": "other", "roots": [root]},
    ]
    prefix = "projects/product/roots/primary"
    assert call("POST", prefix + "/text", {"path": "guide.txt", "content": "changed"})[0] == 405
    with monkeypatch.context() as scoped:
        scoped.setattr(console.workbench.project_files, "_root_fd", lambda *_: pytest.fail("unauthorized root opened"))
        assert call("GET", "projects/private/roots/primary/tree")[0] == 403
        assert call("GET", "projects/unknown/roots/primary/tree")[0] == 404
        assert call("GET", "projects/product/roots/unknown/tree")[0] == 404
    if not _SAFE_PROJECT_READS:
        for suffix in ("/tree", "/text?path=guide.txt"):
            status, result = call("GET", prefix + suffix)
            assert status == 503 and result["error"]["code"] == "project_root_unsupported"
        return

    status, tree = call("GET", prefix + "/tree")
    assert status == 200 and tree["data"]["items"] == [{"name": "guide.txt", "kind": "file"}]
    status, text = call("GET", prefix + "/text?path=guide.txt")
    assert status == 200 and text["data"]["content"] == "Project guide"
    assert str(checkout) not in json.dumps(tree) + json.dumps(text)
    for suffix in ("/text?path=..%252foutside", "/text?path=.env", "/tree?checkout=/tmp", "/text", "/worktree?path=x"):
        assert call("GET", prefix + suffix)[0] in {400, 409}


def test_project_diff_route_passes_only_declared_identifiers_and_scope(site, monkeypatch):
    console, call, _ = site
    calls = []
    monkeypatch.setattr(console.workbench.project_files, "diff",
        lambda session, project, root, path, **kwargs: calls.append((project, root, path, kwargs)) or {"diff": ""})
    prefix = "projects/product/roots/secondary/diff"
    assert call("GET", prefix + "?path=guide.txt&kind=staged")[0] == 200
    assert calls == [("product", "secondary", "guide.txt", {"kind": "staged"})]
    assert call("GET", prefix + "?path=guide.txt&command=anything")[0] == 400
