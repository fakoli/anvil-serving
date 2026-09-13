"""Synthetic HTTP coverage for the Anvil Connect Observatory boundary."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import time
from email.message import Message

import pytest

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.access import ConnectVerifier
from anvil_serving.observability.dashboard.console import Console, attach_console, load_config
from anvil_serving.observability.dashboard.contracts import ObservatoryError


SECRET = bytes(range(32))
SECRET_TEXT = base64.urlsafe_b64encode(SECRET).decode().rstrip("=")
ORIGIN = "https://console.example.test"
BASE = "/observatory/"


class Metrics:
    def snapshot(self):
        return {"hosts": [], "serves": [], "coverage": {"status": "unknown"}}

    def integration_status(self):
        return {"status": "unknown"}

    def chart(self, *_args, **_kwargs):
        return {"series": [], "status": "unknown"}


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def assertion(method: str, target: str, *, subject="connect-fixture", secret=SECRET, **changes) -> str:
    now = int(time.time())
    payload = {
        "v": 1,
        "iss": "anvil-connect",
        "kid": "current",
        "sub": subject,
        "sid": "a" * 32,
        "sg": 1,
        "pg": 1,
        "epoch": "b" * 64,
        "resource": "observatory",
        "host": "console.example.test",
        "method": method,
        "target_sha256": hashlib.sha256(target.encode("ascii")).hexdigest(),
        "iat": now,
        "exp": now + 20,
        "session_exp": now + 300,
        "jti": hashlib.md5((method + target + str(now)).encode(), usedforsecurity=False).hexdigest(),
    }
    payload.update(changes)
    encoded = b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    mac = b64(hmac.new(secret, ("acai1." + encoded).encode("ascii"), hashlib.sha256).digest())
    return "acai1." + encoded + "." + mac


def config(tmp_path, *, mapping=True):
    return {
        "origin": ORIGIN,
        "base_path": BASE,
        "users": [
            {
                "id": "operator-fixture",
                "username": "operator",
                "role": "operator",
                "resources": ["*"],
                "actions": ["configuration.apply"],
            }
        ],
        "authentication": {
            "mode": "connect",
            "connect": {
                "resource": "observatory",
                "keys": [{"id": "current", "secret_env": "CONNECT_ASSERTION_CURRENT"}],
                "principals": {"connect-fixture": "operator-fixture"} if mapping else {},
            },
        },
        "inventory": {"hosts": [], "serves": []},
        "prometheus_url": "http://127.0.0.1:9090",
        "state_path": str(tmp_path / "journal.sqlite"),
        "fixture": True,
    }


@pytest.fixture
def connect_site(tmp_path):
    current = config(tmp_path)
    console = Console(current, metrics=Metrics(), authenticate=lambda *_: pytest.fail("password authentication ran"),
                      environment={"CONNECT_ASSERTION_CURRENT": SECRET_TEXT})
    from anvil_serving.observability.dashboard.app import create_dashboard_server

    server = create_dashboard_server(TelemetryRegistry(), port=0)
    attach_console(server, console)
    thread = run_server_in_thread(server)
    state = {"port": server.server_address[1]}

    def call(method, route, *, signed=True, cookie=True, body=None, extra=None, token=None):
        target = BASE + "api/observatory/v1/" + route
        headers = {"Host": "console.example.test", "Origin": ORIGIN}
        if signed:
            headers["X-Anvil-Connect-Identity"] = token or assertion(method, target)
        if cookie and state.get("cookie"):
            headers["Cookie"] = state["cookie"]
            if method != "GET":
                headers["X-CSRF-Token"] = state["csrf"]
        raw = None
        if body is not None:
            raw = json.dumps(body)
            headers["Content-Type"] = "application/json"
        headers.update(extra or {})
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request(method, target, body=raw, headers=headers)
        response = connection.getresponse()
        raw_response = response.read()
        data = json.loads(raw_response)
        set_cookie = response.getheader("Set-Cookie")
        if route == "session" and method == "GET" and response.status == 200 and set_cookie:
            state["cookie"] = set_cookie.split(";", 1)[0]
            state["csrf"] = data["data"]["csrf_token"]
        status = response.status
        connection.close()
        return status, data

    try:
        yield console, call, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        console.close()


def test_connect_session_bootstraps_and_requires_fresh_bound_assertions(connect_site):
    _console, call, state = connect_site
    status, session = call("GET", "session")
    assert status == 200 and session["data"]["authentication_mode"] == "connect"
    assert state["cookie"] and session["data"]["identity"] == "operator-fixture"
    assert call("GET", "fleet")[0] == 200
    assert call("GET", "fleet", signed=False)[0] == 401

    target = BASE + "api/observatory/v1/fleet"
    wrong_target = assertion("GET", target, target_sha256="0" * 64)
    assert call("GET", "fleet", token=wrong_target)[0] == 401
    wrong_binding = assertion("GET", target, sid="c" * 32)
    assert call("GET", "fleet", token=wrong_binding)[0] == 401


def test_connect_access_advertisement_is_opt_in_and_requires_connect_mode(tmp_path):
    disabled = Console(config(tmp_path), metrics=Metrics(), environment={"CONNECT_ASSERTION_CURRENT": SECRET_TEXT})
    try:
        assert "connect_access_path" not in disabled.session_view(None)
    finally:
        disabled.close()

    current = config(tmp_path)
    current["connect_access"] = True
    console = Console(current, metrics=Metrics(), environment={"CONNECT_ASSERTION_CURRENT": SECRET_TEXT})
    try:
        assert console.session_view(None)["connect_access_path"] == "/observatory/_anvil-connect/access"
    finally:
        console.close()

    source = {**config(tmp_path), "schema": "anvil-observatory/config/v1", "connect_access": True}
    path = tmp_path / "observatory.json"
    path.write_text(json.dumps(source))
    assert load_config(str(path))["connect_access"] is True
    source["authentication"] = {"mode": "legacy", "grafana_url": "https://grafana.example.test"}
    path.write_text(json.dumps(source))
    with pytest.raises(ValueError, match="requires Connect"):
        load_config(str(path))
    source["connect_access"] = "true"
    with pytest.raises(ValueError, match="invalid Connect access"):
        Console(source, metrics=Metrics(), environment={})


def test_connect_access_bookmark_returns_the_shell(connect_site):
    _console, _call, state = connect_site
    connection = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=5)
    connection.request("GET", BASE + "access", headers={"Host": "console.example.test"})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    assert response.status == 200
    assert b"Anvil Workbench" in body


def test_connect_mutation_replay_and_password_logout_routes_are_closed(connect_site):
    _console, call, _state = connect_site
    assert call("GET", "session")[0] == 200
    target = BASE + "api/observatory/v1/drafts"
    token = assertion("POST", target)
    first = call("POST", "drafts", body={}, token=token)
    second = call("POST", "drafts", body={}, token=token)
    assert first[0] == 400 and second[0] == 401
    assert call("POST", "session", signed=False, cookie=False, body={"username": "operator", "password": "fixture"})[0] == 405
    assert call("DELETE", "session")[0] == 405


def test_connect_expiry_removes_the_binding_index(connect_site):
    console, call, state = connect_site
    assert call("GET", "session")[0] == 200
    message = Message()
    message["Cookie"] = state["cookie"]
    console.access.clock = lambda: time.time() + 3600
    assert console.access.session(message, required=False) is None
    assert not console.access._sessions and not console.access._connect_sessions


def test_connect_rebinding_replaces_the_presented_cookie_without_session_growth(connect_site):
    console, call, state = connect_site
    assert call("GET", "session")[0] == 200
    target = BASE + "api/observatory/v1/session"
    for number in range(1, 258):
        message = Message()
        message["Cookie"] = state["cookie"]
        message["X-Anvil-Connect-Identity"] = assertion("GET", target, sid=f"{number:032x}")
        session, issued = console.access.connect_session(message, method="GET", target=target, bootstrap=True)
        assert issued
        state["cookie"] = console.access.cookie(session).split(";", 1)[0]
        assert len(console.access._sessions) == len(console.access._connect_sessions) == 1


def test_connect_profile_has_no_grants_and_only_session_is_available(tmp_path):
    current = config(tmp_path, mapping=False)
    console = Console(current, metrics=Metrics(), environment={"CONNECT_ASSERTION_CURRENT": SECRET_TEXT})
    from anvil_serving.observability.dashboard.app import create_dashboard_server

    server = create_dashboard_server(TelemetryRegistry(), port=0)
    attach_console(server, console)
    thread = run_server_in_thread(server)
    try:
        path = BASE + "api/observatory/v1/"
        token = assertion("GET", path + "session", subject="unmapped-fixture")
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        connection.request("GET", path + "session", headers={"Host": "console.example.test", "X-Anvil-Connect-Identity": token})
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200 and payload["data"]["profile_id"].startswith("connect-")
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        connection.close()
        for route in ("fleet", "settings", "controls", "metrics?chart=generation", "operations", "evidence"):
            target = path + route
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            connection.request("GET", target, headers={"Host": "console.example.test", "Cookie": cookie,
                                                        "X-Anvil-Connect-Identity": assertion("GET", target, subject="unmapped-fixture")})
            response = connection.getresponse()
            result = json.loads(response.read())
            assert response.status == 403 and result["error"]["code"] == "permission_denied"
            connection.close()
        one = console.store.connect_profile("unmapped-fixture")
        two = console.store.connect_profile("unmapped-fixture")
        assert one == two and console.store.db.execute("SELECT count(*) FROM connect_profiles").fetchone()[0] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        console.close()


@pytest.mark.parametrize("mapping", [False, True])
def test_connect_legacy_receiver_preserves_subject_access_with_signed_role(tmp_path, mapping):
    console = Console(config(tmp_path, mapping=mapping), metrics=Metrics(),
                      environment={"CONNECT_ASSERTION_CURRENT": SECRET_TEXT})
    target = BASE + "api/observatory/v1/session"
    try:
        message = Message()
        message[_header_name()] = assertion("GET", target, role="admin")
        session, issued = console.access.connect_session(message, method="GET", target=target, bootstrap=True)
        assert issued
        if mapping:
            assert session.principal.identity == "operator-fixture"
            assert session.principal.can_operate("fixture", "configuration.apply")
        else:
            assert session.profile_id.startswith("connect-")
            assert not session.principal.resources and not session.principal.actions
    finally:
        console.close()


def test_connect_application_roles_use_only_the_explicit_role_mapping(tmp_path):
    current = config(tmp_path)
    current["users"].append({
        "id": "member-fixture", "username": "member", "role": "viewer", "resources": [], "actions": [],
    })
    current["authentication"]["connect"]["roles"] = {"member": "member-fixture", "admin": "operator-fixture"}
    console = Console(current, metrics=Metrics(), environment={"CONNECT_ASSERTION_CURRENT": SECRET_TEXT})
    target = BASE + "api/observatory/v1/session"
    try:
        missing_role = Message()
        missing_role[_header_name()] = assertion("GET", target)
        with pytest.raises(ObservatoryError) as error:
            console.access.connect_session(missing_role, method="GET", target=target, bootstrap=True)
        assert error.value.status == 401
        message = Message()
        message[_header_name()] = assertion("GET", target, role="member")
        session, issued = console.access.connect_session(message, method="GET", target=target, bootstrap=True)
        assert issued and session.principal.identity == "member-fixture"
        assert session.principal.role == "viewer" and not session.principal.resources

        message = Message()
        message[_header_name()] = assertion("GET", target, role="admin")
        session, issued = console.access.connect_session(message, method="GET", target=target, bootstrap=True)
        assert issued and session.principal.identity == "operator-fixture"

        missing = config(tmp_path)
        missing["authentication"]["connect"]["roles"] = {"admin": "operator-fixture"}
        denied = Console(missing, metrics=Metrics(), environment={"CONNECT_ASSERTION_CURRENT": SECRET_TEXT})
        try:
            message = Message()
            message[_header_name()] = assertion("GET", target, role="member")
            with pytest.raises(ObservatoryError) as error:
                denied.access.connect_session(message, method="GET", target=target, bootstrap=True)
            assert error.value.status == 401
        finally:
            denied.close()
    finally:
        console.close()


def test_connect_verifier_rejects_duplicate_headers_signature_schema_and_skew():
    verifier = ConnectVerifier(
        {"resource": "observatory", "keys": [{"id": "current", "secret_env": "CONNECT_ASSERTION_CURRENT"}], "principals": {}},
        origin=ORIGIN,
        environment={"CONNECT_ASSERTION_CURRENT": SECRET_TEXT},
    )
    target = BASE + "api/observatory/v1/session"
    for token in (
        assertion("GET", target, secret=b"x" * 32),
        assertion("GET", target, v=2),
        assertion("GET", target, role="operator"),
        assertion("GET", target, exp=int(time.time()) - 10, iat=int(time.time()) - 20),
    ):
        message = Message()
        message[_header_name()] = token
        with pytest.raises(ObservatoryError) as error:
            verifier.verify(message, method="GET", target=target)
        assert error.value.status == 401
    message = Message()
    message[_header_name()] = assertion("GET", target)
    message[_header_name()] = assertion("GET", target)
    with pytest.raises(ObservatoryError):
        verifier.verify(message, method="GET", target=target)


def test_connect_verifier_accepts_the_gateway_fixed_wire_vector():
    verifier = ConnectVerifier(
        {"resource": "dash", "keys": [{"id": "observatory-v1", "secret_env": "CONNECT_ASSERTION_CURRENT"}], "principals": {}},
        origin="https://dash.example.test",
        environment={"CONNECT_ASSERTION_CURRENT": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"},
        clock=lambda: 1_789_000_000,
    )
    token = (
        "acai1.eyJ2IjoxLCJpc3MiOiJhbnZpbC1jb25uZWN0Iiwia2lkIjoib2JzZXJ2YXRvcnktdjEiLCJzdWIiOiJodW1hbjphYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhIiwic2lkIjoiMTExMTExMTExMTExMTExMTExMTExMTExMTExMTExMTEiLCJzZyI6MywicGciOjcsImVwb2NoIjoiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYmJiYiIsInJlc291cmNlIjoiZGFzaCIsImhvc3QiOiJkYXNoLmV4YW1wbGUudGVzdCIsIm1ldGhvZCI6IkdFVCIsInRhcmdldF9zaGEyNTYiOiIyNTlmYzM1YjI0NGMwMmFkNzQ5YjFkMWI5MjNjZWRlNGRhNDc0MjI3MGMzZGQ3ZDgwYjk3MTY5MzhhODNkNTNjIiwiaWF0IjoxNzg5MDAwMDAwLCJleHAiOjE3ODkwMDAwMzAsInNlc3Npb25fZXhwIjoxNzg5MDAwMTAwLCJqdGkiOiI0MjQyNDI0MjQyNDI0MjQyNDI0MjQyNDI0MjQyNDI0MiJ9.R0uvUBMc8ZiV8yDMsvG0gkzhHzt1wCHIHK2iB6wOAMs"
    )
    message = Message()
    message[_header_name()] = token
    result = verifier.verify(message, method="GET", target="/api/observatory/v1/session?view=current")
    assert result.binding.subject == "human:" + "a" * 64


def _header_name():
    return "X-Anvil-Connect-Identity"
