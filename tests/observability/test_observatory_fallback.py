"""Bounded legacy fallback for one Connect-authenticated Workbench runtime."""

import base64
import http.client
import json

import pytest

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.dashboard import console as console_module
from anvil_serving.observability.dashboard.console import Console, attach_console
from anvil_serving.observability.dashboard.metrics_client import MetricsError, _origin


PRIMARY = "https://console.example.test"
FALLBACK = "https://backup.example.test"
BASE = "/workbench/"


class Metrics:
    def snapshot(self):
        return {"hosts": [], "serves": [], "coverage": {"status": "unknown"}}

    def integration_status(self):
        return {"status": "unknown"}


@pytest.fixture
def fallback_site(tmp_path, monkeypatch):
    logins = []

    def grafana(url):
        assert url == "http://127.0.0.1:3000"
        return lambda username, password: logins.append((username, password)) or (username == "operator" and password == "accepted")

    monkeypatch.setattr(console_module, "GrafanaLogin", grafana)
    config = {
        "origin": PRIMARY,
        "base_path": BASE,
        "operate": True,
        "users": [{"id": "owner", "username": "operator", "role": "operator", "resources": ["*"], "actions": ["playground.request"]}],
        "authentication": {"mode": "connect", "connect": {"resource": "workbench", "keys": [{"id": "key", "secret_env": "SIGNING"}], "principals": {}}},
        "fallback_authentication": {"origin": FALLBACK, "grafana_url": "http://127.0.0.1:3000", "users": ["owner"]},
        "inventory": {"hosts": [], "serves": []},
        "prometheus_url": "http://127.0.0.1:9090",
        "state_path": str(tmp_path / "journal.sqlite"),
        "workbench": {"state_path": str(tmp_path / "workbench.sqlite")},
    }
    console = Console(config, metrics=Metrics(), environment={"SIGNING": base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")})
    server = create_dashboard_server(TelemetryRegistry(), port=0)
    attach_console(server, console)
    thread = run_server_in_thread(server)
    state = {"port": server.server_address[1]}

    def call(method, route, *, host="backup.example.test", origin=FALLBACK, body=None, cookie=None, extra=None):
        target = BASE + "api/observatory/v1/" + route
        headers = {"Host": host, "Origin": origin}
        if cookie:
            headers["Cookie"] = cookie
        headers.update(extra or {})
        raw = json.dumps(body) if body is not None else None
        if raw is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=5)
        connection.request(method, target, body=raw, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        result = response.status, payload, response.getheader("Set-Cookie")
        connection.close()
        return result

    try:
        yield console, call, state, logins
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        console.close()


def test_fallback_owner_login_shares_one_console_store_and_workbench(fallback_site):
    console, call, _state, logins = fallback_site
    status, session, cookie = call("POST", "session", body={"username": "operator", "password": "accepted"})
    assert status == 200 and session["data"]["identity"] == "owner"
    assert session["data"]["authentication_mode"] == "legacy" and logins == [("operator", "accepted")]
    assert console._view_for_host(_headers("backup.example.test")).store is console.store
    assert console._view_for_host(_headers("backup.example.test")).workbench is console.workbench
    assert call("GET", "fleet", cookie=cookie.split(";", 1)[0])[0] == 200


def test_primary_never_accepts_password_and_fallback_never_bootstraps_connect(fallback_site):
    _console, call, _state, logins = fallback_site
    assert call("POST", "session", host="console.example.test", origin=PRIMARY,
                body={"username": "operator", "password": "accepted"})[0] == 405
    assert call("GET", "fleet", extra={"X-Anvil-Connect-Identity": "forged"})[0] == 401
    assert logins == []


def test_fallback_rejects_unlisted_password_cross_origin_and_cross_host_cookies(fallback_site):
    _console, call, _state, _logins = fallback_site
    assert call("POST", "session", body={"username": "other", "password": "accepted"})[0] == 401
    assert call("POST", "session", origin=PRIMARY, body={"username": "operator", "password": "accepted"})[0] == 403
    primary_cookie = "anvil_observatory_session=primary"
    assert call("GET", "fleet", cookie=primary_cookie)[0] == 401
    status, _session, fallback_cookie = call("POST", "session", body={"username": "operator", "password": "accepted"})
    assert status == 200
    assert call("GET", "fleet", host="console.example.test", origin=PRIMARY,
                cookie=fallback_cookie.split(";", 1)[0], extra={"X-Anvil-Connect-Identity": "forged"})[0] == 401


@pytest.mark.parametrize("hosts", [[], ["wrong.example.test"], ["backup.example.test", "backup.example.test"]])
def test_fallback_requires_one_exact_host(fallback_site, hosts):
    _console, _call, state, _logins = fallback_site
    connection = http.client.HTTPConnection("127.0.0.1", state["port"], timeout=5)
    connection.putrequest("GET", BASE, skip_host=True)
    for host in hosts:
        connection.putheader("Host", host)
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 403
    response.read()
    connection.close()


def test_relative_grafana_link_is_only_accepted_for_grafana():
    assert _origin("/grafana", grafana=True) == "/grafana"
    with pytest.raises(MetricsError):
        _origin("/grafana")


def test_fallback_requires_connect_mode_and_loopback_grafana(fallback_site):
    console, _call, _state, _logins = fallback_site
    config = dict(console.config)
    config["authentication"] = {"mode": "legacy", "grafana_url": "http://127.0.0.1:3000"}
    with pytest.raises(ValueError, match="requires Connect"):
        Console(config, metrics=Metrics())
    config = dict(console.config)
    config["fallback_authentication"] = {**config["fallback_authentication"], "grafana_url": "https://grafana.example.test"}
    with pytest.raises(ValueError, match="invalid fallback"):
        Console(config, metrics=Metrics())


def _headers(host):
    from email.message import Message
    result = Message()
    result["Host"] = host
    return result
