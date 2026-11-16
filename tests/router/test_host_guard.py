"""Unauthenticated front door accepts only loopback/bind Host headers (WP-02).

A browser on the operator's machine can reach a loopback router through DNS
rebinding: the attacker's page resolves its own hostname to 127.0.0.1 and the
browser then sends ``Host: attacker.example``. With no token configured the
Host header is the only signal left, so the router refuses foreign names.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import anvil_serving.router.serve as router_serve
from anvil_serving.http_host import host_header_allowed
from anvil_serving.router.front_door import make_server
from tests.router.helpers import StaticBackend
from tests.conftest import isolated_case

TOKEN = "s3cr3t-router-token"
_CHAT = {"model": "chat", "messages": [{"role": "user", "content": "hi"}], "stream": False}


_HOST_HEADER_ALLOWED_CASES = [
    ("127.0.0.1", "127.0.0.1", True),
    ("127.0.0.1:8000", "127.0.0.1", True),
    ("[::1]", "127.0.0.1", True),
    ("[::1]:8000", "127.0.0.1", True),
    ("::1", "127.0.0.1", True),
    ("100.64.0.5:8000", "100.64.0.5", True),
    ("ROUTER.EXAMPLE.TS.NET", "router.example.ts.net", True),
    ("attacker.example", "127.0.0.1", False),
    ("attacker.example:8000", "127.0.0.1", False),
    ("127.0.0.1.attacker.example", "127.0.0.1", False),
    ("localhost", "127.0.0.1", False),
    ("0.0.0.0:8000", "0.0.0.0", False),  # wildcard bind is not a hostname
    ("[::]:8000", "::", False),
    ("", "127.0.0.1", False),
    (None, "127.0.0.1", False),
    ("127.0.0.1:80:80", "127.0.0.1", False),
    ("[::1", "127.0.0.1", False),
]


def test_host_header_allowed(subtests, tmp_path, monkeypatch):
    for index, (value, bind, allowed) in enumerate(_HOST_HEADER_ALLOWED_CASES):
        with (
            subtests.test(value=value, bind=bind, allowed=allowed),
            isolated_case(tmp_path, monkeypatch, index),
        ):
            _host_header_allowed(value=value, bind=bind, allowed=allowed)


def _host_header_allowed(value, bind, allowed):
    assert host_header_allowed(value, bind) is allowed


@contextmanager
def _server(auth_token):
    httpd = make_server("127.0.0.1", 0, StaticBackend(["ok"]), model_routes=("chat",),
                        auth_token=auth_token)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(port, method, path, host, body=None, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/v1/chat/completions", _CHAT),
    ("GET", "/v1/models", None),
    ("GET", "/healthz", None),
])
def test_unauthenticated_foreign_host_is_403(method, path, body):
    with _server(None) as port:
        status, data = _request(port, method, path, "attacker.example", body)
    assert status == 403
    error = json.loads(data)["error"]
    assert error["type"] == "forbidden_host"
    assert "attacker.example" not in error["message"]


def test_unauthenticated_loopback_host_is_served():
    with _server(None) as port:
        status, _ = _request(port, "POST", "/v1/chat/completions", f"127.0.0.1:{port}", _CHAT)
        models_status, _ = _request(port, "GET", "/v1/models", f"[::1]:{port}")
    assert status == 200
    assert models_status == 200


def test_unauthenticated_missing_or_duplicate_host_is_403(subtests):
    for case, headers in [
        ("missing", b""),
        ("duplicate", b"Host: 127.0.0.1\r\nHost: attacker.example\r\n"),
    ]:
        with subtests.test(host=case):
            with _server(None) as port:
                with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
                    sock.sendall(b"GET /v1/models HTTP/1.1\r\n" + headers
                                 + b"Connection: close\r\n\r\n")
                    raw = b""
                    while chunk := sock.recv(65536):
                        raw += chunk
            assert raw.startswith(b"HTTP/1.1 403 ")


def test_authenticated_token_with_any_host_is_served():
    with _server(TOKEN) as port:
        status, _ = _request(port, "POST", "/v1/chat/completions", "attacker.example", _CHAT, token=TOKEN)
        missing, _ = _request(port, "POST", "/v1/chat/completions", "attacker.example", _CHAT)
    assert status == 200
    assert missing == 401  # token rule unchanged


def _stub_serve(monkeypatch, auth_env):
    started = []
    fake = SimpleNamespace(server_address=("0.0.0.0", 0), anvil_tiers=(), anvil_purpose=None,
                           anvil_audio=None, anvil_gateway=None)
    monkeypatch.setattr(router_serve, "load_server_config", lambda path: SimpleNamespace(auth_env=auth_env))
    monkeypatch.setattr(router_serve, "build_server", lambda path, **kw: started.append(kw) or fake)
    monkeypatch.setattr(router_serve, "serve_until_signal", lambda server: None)
    return started


def test_router_run_public_or_localhost_bind_without_auth_exits_nonzero(
    subtests, tmp_path, monkeypatch, capsys
):
    for index, (host, expected) in enumerate([
        ("0.0.0.0", ["[server].auth_env", "--allow-unauthenticated-public-bind"]),
        ("localhost", ["use 127.0.0.1 (not localhost)", "[server].auth_env"]),
    ]):
        with (
            subtests.test(host=host),
            isolated_case(tmp_path, monkeypatch, index) as (case, mp),
        ):
            config = case / "router.toml"
            config.write_text("[router]\n", encoding="utf-8")
            started = _stub_serve(mp, None)
            assert router_serve.main(["--config", str(config), "--host", host]) != 0
            assert started == []
            err = capsys.readouterr().err
            for text in expected:
                assert text in err


def test_router_run_public_bind_with_flag_starts_and_warns(tmp_path, monkeypatch, capsys):
    config = tmp_path / "router.toml"
    config.write_text("[router]\n", encoding="utf-8")
    started = _stub_serve(monkeypatch, None)
    assert router_serve.main(["--config", str(config), "--host", "0.0.0.0",
                              "--allow-unauthenticated-public-bind"]) == 0
    assert len(started) == 1
    assert "WARNING" in capsys.readouterr().err


def test_router_run_authenticated_public_bind_starts(tmp_path, monkeypatch, capsys):
    config = tmp_path / "router.toml"
    config.write_text("[router]\n", encoding="utf-8")
    started = _stub_serve(monkeypatch, "ROUTER_TOKEN")
    assert router_serve.main(["--config", str(config), "--host", "0.0.0.0"]) == 0
    assert len(started) == 1
    assert "bearer authentication is configured" in capsys.readouterr().err


def test_router_run_loopback_without_auth_starts(tmp_path, monkeypatch):
    config = tmp_path / "router.toml"
    config.write_text("[router]\n", encoding="utf-8")
    started = _stub_serve(monkeypatch, None)
    assert router_serve.main(["--config", str(config)]) == 0
    assert len(started) == 1


def test_router_run_config_error_is_reported_before_bind_refusal(tmp_path, monkeypatch, capsys):
    config = tmp_path / "router.toml"
    config.write_text("[router]\n", encoding="utf-8")
    started = []

    def broken(path):
        raise router_serve.ConfigError("synthetic malformed [server] table")

    monkeypatch.setattr(router_serve, "load_server_config", broken)
    monkeypatch.setattr(router_serve, "build_server", lambda path, **kw: started.append(kw))
    assert router_serve.main(["--config", str(config), "--host", "0.0.0.0"]) == 2
    assert started == []
    err = capsys.readouterr().err
    assert "synthetic malformed [server] table" in err
    assert "refusing to bind" not in err
