"""Realtime WebSocket relay behind /v1/realtime (ADR-0047)."""
from __future__ import annotations

import base64
import os
import socket
import socketserver
import threading
import time
from contextlib import contextmanager
from typing import Dict, List, Optional

import pytest

from tests.router.helpers import StaticBackend
from anvil_serving.router.config import ConfigError, RealtimeRoute, load
from anvil_serving.router.front_door import make_server
from anvil_serving.router.keys import KeyStore, Principal
from anvil_serving.router.realtime import RealtimeError, RealtimeGateway
from anvil_serving.websocket_handshake import compute_accept_key

SERVER_FRAME = b"\x81\x05hello"  # unmasked text frame the upstream sends right after 101
CLIENT_KEY = base64.b64encode(os.urandom(16)).decode()


class FakeUpstream:
    """A raw TCP upstream that records the upgrade request and echoes bytes."""

    def __init__(self, *, status: int = 101, accept: Optional[str] = None, protocol: Optional[str] = None,
                 delay: float = 0.0):
        self.requests: List[Dict[str, object]] = []
        self.received = bytearray()
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                request_line = self.rfile.readline().decode("latin-1").strip()
                headers: Dict[str, List[str]] = {}
                while True:
                    line = self.rfile.readline()
                    if line in (b"\r\n", b"\n", b""):
                        break
                    name, _, value = line.decode("latin-1").partition(":")
                    headers.setdefault(name.strip().lower(), []).append(value.strip())
                outer.requests.append({"line": request_line, "headers": headers})
                key = headers.get("sec-websocket-key", [""])[0]
                time.sleep(delay)
                if status != 101:
                    self.wfile.write(f"HTTP/1.1 {status} Nope\r\nContent-Length: 0\r\n\r\n".encode())
                    return
                head = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept or compute_accept_key(key)}\r\n"
                        + (f"Sec-WebSocket-Protocol: {protocol}\r\n" if protocol else "") + "\r\n")
                self.wfile.write(head.encode() + SERVER_FRAME)  # first frame shares the 101 packet
                while True:
                    data = self.connection.recv(65536)
                    if not data:
                        break
                    outer.received.extend(data)
                    self.wfile.write(data)

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def route(port: int, **kwargs) -> RealtimeRoute:
    return RealtimeRoute(id=kwargs.pop("id", "voice-proxy"), base_url=f"http://127.0.0.1:{port}/v1", **kwargs)


def gateway(*routes, max_concurrency=2, env=None, probe_status=200) -> RealtimeGateway:
    return RealtimeGateway(routes, max_concurrency=max_concurrency,
                           env=env if env is not None else {"VOICE_PROXY_TOKEN": "upstream-secret"},
                           probe=lambda url, timeout: probe_status)


@contextmanager
def router(realtime: RealtimeGateway, token: Optional[str] = "router-token"):
    httpd = make_server("127.0.0.1", 0, StaticBackend("chat"), auth_token=token, realtime=realtime)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def upgrade(httpd, *, query="?model=voice", token="router-token", extra=(), websocket=True,
            key=CLIENT_KEY, pipelined=b""):
    sock = socket.create_connection(httpd.server_address[:2], timeout=5)
    lines = [f"GET /v1/realtime{query} HTTP/1.1", "Host: 127.0.0.1"]
    if websocket:
        lines += ["Upgrade: websocket", "Connection: Upgrade", f"Sec-WebSocket-Key: {key}",
                  "Sec-WebSocket-Version: 13"]
    if token:
        lines.append(f"Authorization: Bearer {token}")
    lines += list(extra)
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode() + pipelined)
    reader = sock.makefile("rb")
    status = int(reader.readline().split()[1])
    headers = {}
    while True:
        line = reader.readline()
        if line in (b"\r\n", b""):
            break
        name, _, value = line.decode().partition(":")
        headers[name.strip().lower()] = value.strip()
    return sock, reader, status, headers


def test_realtime_relays_bytes_with_only_the_route_credential():
    upstream = FakeUpstream(protocol="realtime")
    try:
        realtime = gateway(route(upstream.port, auth_env="VOICE_PROXY_TOKEN"))
        with router(realtime) as httpd:
            sock, reader, status, headers = upgrade(httpd, query="?route=voice-proxy&model=voice", extra=(
                "Origin: https://evil.example", "Cookie: session=1",
                "Sec-WebSocket-Protocol: realtime, openai-insecure-api-key.caller-secret"))
            assert status == 101
            assert headers["sec-websocket-accept"] == compute_accept_key(CLIENT_KEY)
            assert headers["sec-websocket-protocol"] == "realtime"
            assert reader.read(len(SERVER_FRAME)) == SERVER_FRAME  # buffered first frame survives
            sock.sendall(b"\x81\x82abcd\x0b\x0b")  # masked client frame bytes, relayed verbatim
            assert reader.read(8) == b"\x81\x82abcd\x0b\x0b"
            assert bytes(upstream.received) == b"\x81\x82abcd\x0b\x0b"
            sent = upstream.requests[0]
            assert sent["line"] == "GET /v1/realtime?model=voice HTTP/1.1"  # route selector stripped
            seen = sent["headers"]
            assert seen["authorization"] == ["Bearer upstream-secret"]
            assert "origin" not in seen and "cookie" not in seen
            assert seen["sec-websocket-protocol"] == ["realtime"]  # credential subprotocol dropped
            sock.close()
    finally:
        upstream.close()


def test_realtime_session_holds_an_audio_admission_permit_until_it_closes():
    upstream = FakeUpstream()
    try:
        with router(gateway(route(upstream.port))) as httpd:
            admission = httpd.anvil_router_admission
            sock, reader, status, _ = upgrade(httpd)
            assert status == 101 and reader.read(len(SERVER_FRAME)) == SERVER_FRAME
            assert admission.status()["counts"]["audio"] == 1  # quiesce/drain see the open session
            reader.close()  # a makefile() reference keeps the socket open otherwise
            sock.close()
            deadline = time.monotonic() + 5
            while admission.status()["counts"]["audio"] and time.monotonic() < deadline:
                time.sleep(0.02)
            assert admission.status()["counts"]["audio"] == 0
    finally:
        upstream.close()


def test_realtime_refusals_happen_before_any_101():
    upstream = FakeUpstream()
    try:
        with router(gateway(route(upstream.port))) as httpd:
            assert upgrade(httpd, token=None)[2] == 401
            assert upgrade(httpd, token="wrong")[2] == 401
            assert upgrade(httpd, websocket=False)[2] == 426
            assert upgrade(httpd, query="?route=nope")[2] == 404
            assert upgrade(httpd, query="?route=a&route=b")[2] == 400
        assert upstream.requests == []
    finally:
        upstream.close()


def test_realtime_concurrency_cap_and_health_gate():
    upstream = FakeUpstream()
    try:
        with router(gateway(route(upstream.port), max_concurrency=1)) as httpd:
            held, reader, status, _ = upgrade(httpd)
            assert status == 101 and reader.read(len(SERVER_FRAME)) == SERVER_FRAME
            assert upgrade(httpd)[2] == 503  # cap reached
            reader.close()
            held.close()
        with router(gateway(route(upstream.port), probe_status=503)) as httpd:
            assert upgrade(httpd)[2] == 503  # upstream /healthz not ready
    finally:
        upstream.close()


@pytest.mark.parametrize("upstream_kwargs", [{"status": 401}, {"accept": "bogus"}, {"protocol": "not-offered"}])
def test_realtime_rejects_bad_upstream_handshakes_with_502(upstream_kwargs):
    upstream = FakeUpstream(**upstream_kwargs)
    try:
        with router(gateway(route(upstream.port))) as httpd:
            assert upgrade(httpd)[2] == 502
    finally:
        upstream.close()


def test_unresolved_route_credential_leaves_the_route_unbound_without_fallback(capsys):
    secured = route(1, id="secured", auth_env="MISSING_TOKEN", default=True)
    open_route = route(2, id="open")
    realtime = gateway(secured, open_route, env={})
    assert len(realtime) == 1
    with pytest.raises(RealtimeError) as raised:
        realtime.resolve(None)  # the default is unbound; never select "open" instead
    assert raised.value.status == 404
    assert realtime.resolve("open").id == "open"
    assert "MISSING_TOKEN" in capsys.readouterr().err


def test_health_probe_is_cached_and_token_free():
    calls = []
    realtime = RealtimeGateway([route(9, health_path="/ready", auth_env="T")], max_concurrency=1,
                               env={"T": "secret"}, probe=lambda url, timeout: calls.append(url) or 200)
    target = realtime.resolve(None)
    assert realtime.healthy(target) and realtime.healthy(target)
    assert calls == ["http://127.0.0.1:9/ready"]


def test_device_keys_grant_realtime_only_with_its_path():
    granted = Principal("k1", models=("voice-proxy",), paths=("/v1/models", "/v1/realtime"))
    chat_only = Principal("k2", models=("voice-proxy",), paths=("/v1/models", "/v1/chat/completions"))
    assert granted.allows_path("GET", "/v1/realtime?route=voice-proxy")
    assert not chat_only.allows_path("GET", "/v1/realtime")
    assert not granted.allows_path("POST", "/v1/realtime")
    models, paths = KeyStore._grants(["voice-proxy"], ["/v1/realtime"])  # realtime alone is a usable grant
    assert paths == ("/v1/models", "/v1/realtime")


BASE = """
[server]
auth_env = "ANVIL_ROUTER_TOKEN"

[router]
model_routes = { "llm.primary" = "chat" }

[[router.tiers]]
id = "chat"
base_url = "http://127.0.0.1:30000/v1"
model = "chat"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "ANVIL_CHAT_KEY"
"""


def load_with(tmp_path, block: str):
    path = tmp_path / "router.toml"
    path.write_text(BASE + block, encoding="utf-8")
    return load(str(path))


def test_realtime_route_config_parses_with_defaults(tmp_path):
    config = load_with(tmp_path, """
[[router.realtime_routes]]
id = "voice-proxy"
base_url = "http://100.64.0.20:8765/v1"
auth_env = "ANVIL_VOICE_PROXY_TOKEN"
""")
    assert config.realtime_routes == (RealtimeRoute(
        id="voice-proxy", base_url="http://100.64.0.20:8765/v1", auth_env="ANVIL_VOICE_PROXY_TOKEN"),)
    assert config.realtime_routes[0].health_path == "/healthz"
    assert config.realtime_max_concurrency == 4


@pytest.mark.parametrize("block,message", [
    ('base_url = "http://localhost:8765/v1"', "never localhost"),
    ('base_url = "http://8.8.8.8:8765/v1"', "127.0.0.1"),
    ('base_url = "ws://127.0.0.1:8765/v1"', "http:// or"),
    ('base_url = "http://user:pw@127.0.0.1:8765/v1"', "credential-free"),
    ('base_url = "http://127.0.0.1:8765/v1"\nhealth_path = "healthz"', "health_path"),
    ('base_url = "http://127.0.0.1:8765/v1"\nauth_env = "lower case"', "auth_env"),
    ('base_url = "http://127.0.0.1:8765/v1"\nmodel = "x"', "model"),
])
def test_realtime_route_config_rejects_unsafe_values(tmp_path, block, message):
    with pytest.raises(ConfigError, match=message):
        load_with(tmp_path, f'\n[[router.realtime_routes]]\nid = "voice"\n{block}\n')


def test_realtime_routes_need_one_default_and_unique_ids(tmp_path):
    two = """
[[router.realtime_routes]]
id = "a"
base_url = "http://127.0.0.1:8765/v1"
[[router.realtime_routes]]
id = "b"
base_url = "http://127.0.0.1:8766/v1"
"""
    with pytest.raises(ConfigError, match="exactly one"):
        load_with(tmp_path, two)
    with pytest.raises(ConfigError, match="duplicate realtime route id"):
        load_with(tmp_path, '\n[[router.realtime_routes]]\nid = "chat"\nbase_url = "http://127.0.0.1:8765/v1"\n')
    path = tmp_path / "cap.toml"
    path.write_text(BASE.replace("[router]\n", "[router]\nrealtime_max_concurrency = 0\n"), encoding="utf-8")
    with pytest.raises(ConfigError, match="realtime_max_concurrency"):
        load(str(path))


def test_build_server_rejects_realtime_routes_without_front_door_auth(tmp_path, monkeypatch):
    from anvil_serving.router.serve import build_server
    monkeypatch.delenv("ANVIL_ROUTER_TOKEN", raising=False)
    path = tmp_path / "router.toml"
    path.write_text(BASE.replace('[server]\nauth_env = "ANVIL_ROUTER_TOKEN"\n', "") + """
[[router.realtime_routes]]
id = "voice"
base_url = "http://127.0.0.1:8765/v1"
""", encoding="utf-8")
    with pytest.raises(ConfigError, match="realtime_routes.*auth_env"):
        build_server(str(path), port=0, backends={"chat": StaticBackend("chat")})


def test_realtime_forwards_bytes_the_caller_pipelined_after_its_request():
    upstream = FakeUpstream()
    try:
        with router(gateway(route(upstream.port))) as httpd:
            sock, reader, status, _ = upgrade(httpd, pipelined=b"\x81\x80mask")
            assert status == 101 and reader.read(len(SERVER_FRAME)) == SERVER_FRAME
            deadline = time.monotonic() + 5
            while bytes(upstream.received) != b"\x81\x80mask" and time.monotonic() < deadline:
                time.sleep(0.02)
            assert bytes(upstream.received) == b"\x81\x80mask"
            reader.close()
            sock.close()
    finally:
        upstream.close()


def test_realtime_rejects_a_malformed_key_before_contacting_upstream():
    upstream = FakeUpstream()
    try:
        with router(gateway(route(upstream.port))) as httpd:
            assert upgrade(httpd, key="not-a-16-byte-nonce")[2] == 400
            assert upgrade(httpd, key=CLIENT_KEY[:-2] + "\u00e9=")[2] in (400, 426)
        assert upstream.requests == []
    finally:
        upstream.close()


def test_realtime_handshake_has_one_total_deadline(monkeypatch):
    from anvil_serving.router import realtime as module
    monkeypatch.setattr(module, "HANDSHAKE_TIMEOUT_SECONDS", 0.3)
    upstream = FakeUpstream(delay=1.0)
    try:
        with router(gateway(route(upstream.port))) as httpd:
            started = time.monotonic()
            assert upgrade(httpd)[2] == 504
            assert time.monotonic() - started < 1.0
    finally:
        upstream.close()


def test_realtime_session_ends_and_frees_its_permit_when_both_sides_idle(monkeypatch):
    from anvil_serving.router import realtime as module
    monkeypatch.setattr(module, "IDLE_TIMEOUT_SECONDS", 0.4)
    upstream = FakeUpstream()
    try:
        with router(gateway(route(upstream.port))) as httpd:
            admission = httpd.anvil_router_admission
            sock, reader, status, _ = upgrade(httpd)
            assert status == 101 and reader.read(len(SERVER_FRAME)) == SERVER_FRAME
            assert reader.read(1) == b""  # nobody speaks: the router closes the session
            deadline = time.monotonic() + 5
            while admission.status()["counts"]["audio"] and time.monotonic() < deadline:
                time.sleep(0.02)
            assert admission.status()["counts"]["audio"] == 0
            reader.close()
            sock.close()
    finally:
        upstream.close()


def test_make_server_refuses_realtime_without_front_door_auth():
    with pytest.raises(ValueError, match="front-door authentication"):
        make_server("127.0.0.1", 0, StaticBackend("chat"), auth_token=None, realtime=gateway(route(1)))
