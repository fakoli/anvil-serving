"""Realtime WebSocket upgrade: Origin allowlist and unauthenticated Host guard.

WebSockets are exempt from the browser same-origin policy, so any page the
operator visits could open ``ws://127.0.0.1:8765/v1/realtime``. A browser
always sends ``Origin``; native clients (the OpenClaw Gateway relay, the
Realtime SDK demo) send none. DNS rebinding is covered by the Host rule.
"""

from __future__ import annotations

import base64
import os
import socket
import threading

import pytest

from anvil_serving.voice import config as voice_config
from anvil_serving.voice.realtime.ws import make_ws_server


def _start(**kwargs):
    server = make_ws_server("127.0.0.1", 0, lambda conn, path: conn.close(), **kwargs)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    return server, thread


def _status(port: int, *, host: str | None = None, extra: str = "") -> int:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET /v1/realtime HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "%s"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n" % (host or "127.0.0.1:%d" % port, extra, key)
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(5)
        sock.sendall(request)
        return int(sock.recv(4096).split(b" ", 2)[1])


@pytest.fixture
def server():
    servers = []

    def factory(**kwargs):
        srv, thread = _start(**kwargs)
        servers.append((srv, thread))
        return srv.server_address[1]

    yield factory
    for srv, thread in servers:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("origin", ["https://attacker.example", "null", ""])
def test_foreign_origin_is_rejected_before_upgrade(server, origin):
    # "null" is what a sandboxed iframe or file:// page sends; an empty value
    # is still a present Origin header and must not count as absent.
    port = server()
    assert _status(port, extra="Origin: %s\r\n" % origin) == 403


def test_foreign_origin_is_rejected_even_with_a_valid_token(server, monkeypatch):
    monkeypatch.setenv("ANVIL_TEST_WS_TOKEN", "fixture-realtime-token")
    port = server(token_env="ANVIL_TEST_WS_TOKEN")
    extra = "Origin: https://attacker.example\r\nAuthorization: Bearer fixture-realtime-token\r\n"
    assert _status(port, extra=extra) == 403


def test_no_origin_upgrades(server):
    port = server()
    assert _status(port) == 101


def test_allowlisted_origin_upgrades(server):
    port = server(allowed_origins=["https://talk.example.ts.net"])
    assert _status(port, extra="Origin: https://talk.example.ts.net\r\n") == 101
    assert _status(port, extra="Origin: https://other.example.ts.net\r\n") == 403


def test_duplicate_origin_headers_are_rejected(server):
    port = server(allowed_origins=["https://talk.example.ts.net"])
    extra = "Origin: https://talk.example.ts.net\r\nOrigin: https://attacker.example\r\n"
    assert _status(port, extra=extra) == 403


def test_foreign_host_without_token_is_rejected(server):
    port = server()
    assert _status(port, host="attacker.example:%d" % port) == 403


def test_foreign_host_without_token_cannot_read_introspection(server):
    srv, thread = _start(extra_routes={"/pool": lambda: {"size": 1}})
    port = srv.server_address[1]
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall(b"GET /pool HTTP/1.1\r\nHost: attacker.example\r\n\r\n")
            assert sock.recv(4096).startswith(b"HTTP/1.1 403")
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _get_status(port: int, path: str, extra: str = "") -> int:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(5)
        sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n%s\r\n" % (path, port, extra)).encode("ascii"))
        return int(sock.recv(4096).split(b" ", 2)[1])


@pytest.mark.parametrize("path", ["/pool", "/usage"])
def test_introspection_requires_configured_token(server, monkeypatch, path):
    monkeypatch.setenv("ANVIL_TEST_WS_TOKEN", "fixture-realtime-token")
    port = server(
        token_env="ANVIL_TEST_WS_TOKEN",
        extra_routes={"/pool": lambda: {"size": 1}, "/usage": lambda: {"claims_total": 0}},
    )
    assert _get_status(port, path) == 401
    assert _get_status(port, path, "Authorization: Bearer wrong-token\r\n") == 401
    assert _get_status(port, path, "Authorization: Bearer fixture-realtime-token\r\n") == 200


def _get(port: int, path: str, host: str | None = None) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.settimeout(5)
        request = "GET %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % (path, host or "127.0.0.1:%d" % port)
        sock.sendall(request.encode("ascii"))
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)


def test_healthz_needs_no_token_and_reveals_nothing(server, monkeypatch):
    monkeypatch.setenv("ANVIL_TEST_WS_TOKEN", "fixture-realtime-token")
    port = server(
        token_env="ANVIL_TEST_WS_TOKEN",
        extra_routes={"/pool": lambda: {"size": 7}, "/usage": lambda: {"claims_total": 42}},
    )
    response = _get(port, "/healthz")
    assert response.startswith(b"HTTP/1.1 200")
    assert response.split(b"\r\n\r\n", 1)[1] == b'{"ok": true}'


def test_healthz_without_token_keeps_host_rule(server):
    port = server()
    assert _get(port, "/healthz").startswith(b"HTTP/1.1 200")
    assert _get(port, "/healthz", host="attacker.example").startswith(b"HTTP/1.1 403")


def test_token_configured_proxy_becomes_ready_via_healthz(server, monkeypatch, tmp_path):
    from anvil_serving.voice.serves.proxy import ProxyServe, ProxyServeConfig

    monkeypatch.setenv("ANVIL_TEST_WS_TOKEN", "fixture-realtime-token")
    port = server(token_env="ANVIL_TEST_WS_TOKEN", extra_routes={"/usage": lambda: {"claims_total": 0}})
    config = ProxyServeConfig(
        base_url="http://127.0.0.1:%d" % port, model="proxy", manifest_path=str(tmp_path / "absent.toml")
    )
    assert ProxyServe(config).wait_ready(timeout=5).ready is True


def test_explicit_usage_ready_url_still_works_without_token(server, tmp_path):
    from anvil_serving.voice.serves.proxy import ProxyServe, ProxyServeConfig

    port = server(extra_routes={"/usage": lambda: {"claims_total": 0}})
    config = ProxyServeConfig(
        base_url="http://127.0.0.1:%d" % port, model="proxy",
        manifest_path=str(tmp_path / "absent.toml"), ready_url="http://127.0.0.1:%d/usage" % port,
    )
    assert ProxyServe(config).wait_ready(timeout=5).ready is True


def test_foreign_host_with_valid_token_upgrades(server, monkeypatch):
    monkeypatch.setenv("ANVIL_TEST_WS_TOKEN", "fixture-realtime-token")
    port = server(token_env="ANVIL_TEST_WS_TOKEN")
    extra = "Authorization: Bearer fixture-realtime-token\r\n"
    assert _status(port, host="voice.example.ts.net:%d" % port, extra=extra) == 101


def _manifest(origins):
    return {
        "voice": {
            "realtime_allowed_origins": origins,
            "llm": {"base_url": "http://127.0.0.1:8000/v1", "model": "llm.voice"},
            "stt": {"base_url": "http://127.0.0.1:30010/v1", "model": "stt"},
            "tts": {"base_url": "http://127.0.0.1:30011/v1", "model": "tts"},
        }
    }


def test_manifest_accepts_origin_allowlist():
    voice_config.validate_manifest(_manifest(["https://talk.example.ts.net", "http://127.0.0.1:3000"]))


@pytest.mark.parametrize("bad", [
    "https://talk.example.ts.net/",
    "https://talk.example.ts.net/path",
    "null",
    "*",
    "ftp://talk.example.ts.net",
    "http://localhost:3000",
])
def test_manifest_rejects_malformed_origin(bad):
    with pytest.raises(voice_config.ConfigError):
        voice_config.validate_manifest(_manifest([bad]))


def test_manifest_rejects_non_list_origins():
    with pytest.raises(voice_config.ConfigError):
        voice_config.validate_manifest(_manifest("https://talk.example.ts.net"))
