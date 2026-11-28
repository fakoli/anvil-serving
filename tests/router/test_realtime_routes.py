"""Realtime WebSocket relay behind /v1/realtime (ADR-0050)."""
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
from tests.router.key_fixtures import tmp_path as tmp_path
from anvil_serving.router.config import ConfigError, RealtimeRoute, ServerConfig, load
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


def test_device_key_realtime_route_grant_matches_exact_spelling(tmp_path):
    # Route ids are case-sensitive in config and selection, so a grant for one
    # spelling must not open a distinct route that differs only by case.
    upstream = FakeUpstream()
    store = KeyStore.initialize(tmp_path / "credentials" / "keys.sqlite3")
    _, secret = store.create("phone", models=["voice"], paths=["/v1/realtime"])
    realtime = gateway(route(upstream.port, id="voice"), route(upstream.port, id="Voice"))
    httpd = make_server("127.0.0.1", 0, StaticBackend("chat"), auth_token="router-token", realtime=realtime,
                        server_config=ServerConfig(api_keys_path=str(store.path)))
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        sock, _, status, _ = upgrade(httpd, query="?route=Voice", token=secret)
        sock.close()
        assert status == 403
        sock, _, status, _ = upgrade(httpd, query="?route=voice", token=secret)
        sock.close()
        assert status == 101
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        upstream.close()


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


def test_realtime_authenticates_before_bodyless_check_and_rejects_duplicate_content_length():
    upstream = FakeUpstream()
    try:
        with router(gateway(route(upstream.port))) as httpd:
            # Unauthenticated request carrying a body must fail 401, not 400.
            sock, _, status, _ = upgrade(httpd, token=None, extra=("Content-Length: 5",))
            sock.close()
            assert status == 401
            sock, _, status, _ = upgrade(httpd, token="wrong", extra=("Transfer-Encoding: chunked",))
            sock.close()
            assert status == 401

            # Authenticated duplicate Content-Length (smuggling) must fail 400.
            sock, _, status, _ = upgrade(httpd, extra=("Content-Length: 0", "Content-Length: 5"))
            sock.close()
            assert status == 400
            sock, _, status, _ = upgrade(httpd, extra=("Content-Length: 0", "Content-Length: 0"))
            sock.close()
            assert status == 400

            # Single Content-Length: 0 still succeeds.
            sock, reader, status, _ = upgrade(httpd, extra=("Content-Length: 0",))
            assert status == 101 and reader.read(len(SERVER_FRAME)) == SERVER_FRAME
            reader.close()
            sock.close()
    finally:
        upstream.close()


def test_device_key_cannot_enumerate_realtime_route_ids_via_404_vs_403(tmp_path):
    upstream = FakeUpstream()
    store = KeyStore.initialize(tmp_path / "credentials" / "keys.sqlite3")
    _, secret = store.create("phone", models=["voice"], paths=["/v1/realtime"])
    # Two routes with no default: unknown route, ungranted route, and missing default
    # must all return 403 for a device key, while operator token still sees 404.
    realtime = gateway(route(upstream.port, id="voice"), route(upstream.port, id="other"))
    httpd = make_server("127.0.0.1", 0, StaticBackend("chat"), auth_token="router-token", realtime=realtime,
                        server_config=ServerConfig(api_keys_path=str(store.path)))
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        for query in ("?route=other", "?route=does-not-exist", ""):
            sock, _, status, _ = upgrade(httpd, query=query, token=secret)
            sock.close()
            assert status == 403
        sock, _, status, _ = upgrade(httpd, query="?route=does-not-exist", token="router-token")
        sock.close()
        assert status == 404
        sock, _, status, _ = upgrade(httpd, query="", token="router-token")
        sock.close()
        assert status == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        upstream.close()


def test_realtime_rejects_crlf_and_non_latin1_in_auth_env_and_config(tmp_path, capsys):
    # Unsafe auth_env values leave the route unbound without leaking the secret.
    for bad_token in ("good\r\nX-Injected: yes", "bad\x00token", "bad\u2603token"):
        rt = RealtimeGateway(
            [route(1, id="voice", auth_env="VOICE_TOKEN")],
            max_concurrency=1,
            env={"VOICE_TOKEN": bad_token},
        )
        assert len(rt) == 0
        err = capsys.readouterr().err
        assert "invalid header characters" in err
        assert bad_token not in err

    # Non-Latin-1 or control chars reaching open_upstream yield RealtimeError(502), never UnicodeEncodeError.
    from email.message import Message

    bad_route = RealtimeRoute(id="voice", base_url="http://127.0.0.1:9/\u2603")
    rt = RealtimeGateway([bad_route], max_concurrency=1, env={})
    headers = Message()
    headers["Upgrade"] = "websocket"
    headers["Connection"] = "Upgrade"
    headers["Sec-WebSocket-Key"] = CLIENT_KEY
    headers["Sec-WebSocket-Version"] = "13"
    with pytest.raises(RealtimeError) as raised:
        rt.open_upstream(bad_route, headers, "")
    assert raised.value.status == 502

    # Config validation rejects whitespace and control chars in base_url and health_path.
    for block in (
        'base_url = "http://127.0.0.1:8765/v1\\r\\nX-Injected: 1"',
        'base_url = "http://127.0.0.1:8765/v1 bad"',
        'base_url = "http://127.0.0.1:8765/v1"\nhealth_path = "/healthz\\r\\nX-Bad: 1"',
        'base_url = "http://127.0.0.1:8765/v1"\nhealth_path = "/health z"',
    ):
        with pytest.raises(ConfigError):
            load_with(tmp_path, f'\n[[router.realtime_routes]]\nid = "voice"\n{block}\n')


def test_realtime_duplicate_headers_in_upgrade_and_upstream_response():
    from email.message import Message
    from anvil_serving.websocket_handshake import is_websocket_upgrade

    def msg(*pairs):
        m = Message()
        for k, v in pairs:
            m[k] = v
        return m

    # Multi-line Connection: keep-alive + Connection: Upgrade is valid (RFC 9110).
    assert is_websocket_upgrade(msg(
        ("Upgrade", "websocket"),
        ("Connection", "keep-alive"),
        ("Connection", "Upgrade"),
        ("Sec-WebSocket-Key", CLIENT_KEY),
        ("Sec-WebSocket-Version", "13"),
    ))
    # Multiple conflicting Upgrade headers must be rejected.
    assert not is_websocket_upgrade(msg(
        ("Upgrade", "websocket"),
        ("Upgrade", "h2c"),
        ("Connection", "Upgrade"),
        ("Sec-WebSocket-Key", CLIENT_KEY),
        ("Sec-WebSocket-Version", "13"),
    ))

    # Exercise raw upstream responses with duplicate headers.
    def run_custom_upstream(extra_response_lines: list[str], *, request_extra=()):
        class CustomHandler(socketserver.StreamRequestHandler):
            def handle(self):
                self.rfile.readline()
                req_headers = {}
                while True:
                    line = self.rfile.readline()
                    if line in (b"\r\n", b"\n", b""):
                        break
                    name, _, value = line.decode("latin-1").partition(":")
                    req_headers[name.strip().lower()] = value.strip()
                accept = compute_accept_key(req_headers["sec-websocket-key"])
                lines = [
                    "HTTP/1.1 101 Switching Protocols",
                    *[line.format(accept=accept) for line in extra_response_lines],
                ]
                self.wfile.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))

        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), CustomHandler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with router(gateway(route(srv.server_address[1]))) as httpd:
                sock, _, status, _ = upgrade(httpd, extra=request_extra)
                sock.close()
                return status
        finally:
            srv.shutdown()
            srv.server_close()

    # Multi-line Connection header in upstream 101 succeeds.
    assert run_custom_upstream([
        "Upgrade: websocket",
        "Connection: keep-alive",
        "Connection: Upgrade",
        "Sec-WebSocket-Accept: {accept}",
    ]) == 101
    # Trailing empty Sec-WebSocket-Extensions must not erase an earlier non-empty extension.
    assert run_custom_upstream([
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Accept: {accept}",
        "Sec-WebSocket-Extensions: permessage-deflate",
        "Sec-WebSocket-Extensions: ",
    ]) == 502
    # Duplicate Sec-WebSocket-Accept must be rejected.
    assert run_custom_upstream([
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Accept: {accept}",
        "Sec-WebSocket-Accept: {accept}",
    ]) == 502
    # Duplicate Sec-WebSocket-Protocol must be rejected.
    assert run_custom_upstream([
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Accept: {accept}",
        "Sec-WebSocket-Protocol: realtime",
        "Sec-WebSocket-Protocol: realtime",
    ], request_extra=("Sec-WebSocket-Protocol: realtime",)) == 502


def test_zero_route_realtime_gateway_returns_404_and_non_device_gets_correlation_id(capsys):
    # A 0-route RealtimeGateway is falsy and must not intercept /v1/realtime.
    empty = RealtimeGateway([], max_concurrency=1, env={})
    with router(empty) as httpd:
        sock, _, status, _ = upgrade(httpd, websocket=False)
        sock.close()
        assert status == 404

    # Non-device operator callers get X-Request-Id forwarded upstream and logged gateway_request_id.
    upstream = FakeUpstream()
    try:
        with router(gateway(route(upstream.port))) as httpd:
            sock, _, status, headers = upgrade(httpd, websocket=False)
            sock.close()
            assert status == 426
            assert headers.get("x-anvil-request-id", "").startswith("req_")

            sock, reader, status, _ = upgrade(httpd)
            assert status == 101 and reader.read(len(SERVER_FRAME)) == SERVER_FRAME
            reader.close()
            sock.close()
            deadline = time.monotonic() + 5
            while "event=realtime_session" not in (err := capsys.readouterr().err) and time.monotonic() < deadline:
                time.sleep(0.02)
            req_ids = upstream.requests[0]["headers"].get("x-request-id", [])
            assert len(req_ids) == 1 and req_ids[0].startswith("req_")
            assert f"gateway_request_id={req_ids[0]}" in err
    finally:
        upstream.close()


def test_realtime_relay_full_duplex_concurrent_tls_without_race_or_corruption(tmp_path):
    import shutil
    import ssl
    import subprocess
    from anvil_serving.router.realtime import UpstreamSession

    if shutil.which("openssl") is None:
        pytest.skip("openssl binary not available")

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=str(cert), keyfile=str(key))
    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.load_verify_locations(cafile=str(cert))

    def tls_pair():
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        accepted = []

        def accept_one():
            raw, _ = listener.accept()
            accepted.append(server_ctx.wrap_socket(raw, server_side=True))

        t = threading.Thread(target=accept_one, daemon=True)
        t.start()
        raw_client = socket.create_connection(("127.0.0.1", port), timeout=5)
        client_tls = client_ctx.wrap_socket(raw_client, server_hostname="127.0.0.1")
        t.join(timeout=5)
        listener.close()
        return client_tls, accepted[0]

    # Build two real TLS connections joined by RealtimeGateway.relay().
    app_client, relay_client = tls_pair()
    relay_upstream, app_upstream = tls_pair()

    # Deterministic large bursts (> _RELAY_BUFFER_LIMIT) in BOTH directions concurrently.
    up_chunks = [bytes([(i + j) % 251 for j in range(16 * 1024)]) for i in range(32)]
    down_chunks = [bytes([(250 - ((i + j) % 251)) for j in range(16 * 1024)]) for i in range(32)]
    expected_up = b"LEFT-UP:" + b"".join(up_chunks)
    expected_down = b"LEFT-DOWN:" + b"".join(down_chunks)

    got_at_upstream = bytearray()
    got_at_client = bytearray()
    errors: list[BaseException] = []

    def run_endpoint(sock, send_chunks, expected_len: int, dest: bytearray):
        import selectors
        try:
            sock.setblocking(False)
            outbound = bytearray(b"".join(send_chunks))
            deadline = time.monotonic() + 15.0
            with selectors.DefaultSelector() as sel:
                while len(dest) < expected_len or outbound:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"endpoint timed out (sent left={len(outbound)}, recv={len(dest)}/{expected_len})")
                    while isinstance(sock, ssl.SSLSocket) and sock.pending() > 0 and len(dest) < expected_len:
                        chunk = sock.recv(min(32768, expected_len - len(dest)))
                        if not chunk:
                            return
                        dest.extend(chunk)
                    if len(dest) >= expected_len and not outbound:
                        break
                    mask = 0
                    if len(dest) < expected_len:
                        mask |= selectors.EVENT_READ
                    if outbound:
                        mask |= selectors.EVENT_WRITE
                    sel.register(sock, mask)
                    try:
                        events = sel.select(timeout=0.5)
                    finally:
                        sel.unregister(sock)
                    for _, ev in events:
                        if (ev & selectors.EVENT_WRITE) and outbound:
                            try:
                                n = sock.send(outbound[:16384])
                                if n > 0:
                                    del outbound[:n]
                            except (ssl.SSLWantWriteError, ssl.SSLWantReadError, BlockingIOError):
                                pass
                        if (ev & selectors.EVENT_READ) and len(dest) < expected_len:
                            try:
                                chunk = sock.recv(min(32768, expected_len - len(dest)))
                                if not chunk:
                                    return
                                dest.extend(chunk)
                            except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                                pass
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    rt = RealtimeGateway([], max_concurrency=1, env={})
    session = UpstreamSession(sock=relay_upstream, leftover=b"LEFT-DOWN:", response=b"")
    relay_result: list[tuple[int, int]] = []

    def run_relay():
        try:
            relay_result.append(rt.relay(relay_client, b"LEFT-UP:", session, idle_timeout=5.0))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t_relay = threading.Thread(target=run_relay, daemon=True)
    t_client = threading.Thread(target=run_endpoint, args=(app_client, up_chunks, len(expected_down), got_at_client), daemon=True)
    t_upstream = threading.Thread(target=run_endpoint, args=(app_upstream, down_chunks, len(expected_up), got_at_upstream), daemon=True)
    t_relay.start()
    t_client.start()
    t_upstream.start()
    try:
        t_client.join(timeout=15)
        t_upstream.join(timeout=15)
        app_client.close()
        app_upstream.close()
        t_relay.join(timeout=10)
    finally:
        for s in (app_client, relay_client, relay_upstream, app_upstream):
            try:
                s.close()
            except OSError:
                pass

    assert errors == []
    assert bytes(got_at_upstream) == expected_up
    assert bytes(got_at_client) == expected_down
    assert relay_result == [(len(expected_up), len(expected_down))]


def test_realtime_relay_flushes_opposite_buffered_bytes_after_half_close():
    from anvil_serving.router.realtime import UpstreamSession

    app_client, relay_client = socket.socketpair()
    relay_upstream, app_upstream = socket.socketpair()
    try:
        # Half-close the client write side so relay_client hits EOF immediately
        # while both client_leftover and opposite-direction upstream.leftover
        # still need to be flushed cleanly.
        app_client.shutdown(socket.SHUT_WR)
        client_leftover = b"CLIENT-PIPELINED"
        upstream_leftover = b"UPSTREAM-LEFTOVER-" * 2048

        got_client = bytearray()
        got_upstream = bytearray()
        errors: list[BaseException] = []

        def drain(sock: socket.socket, dest: bytearray):
            try:
                sock.settimeout(5)
                while True:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    dest.extend(chunk)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        t_client = threading.Thread(target=drain, args=(app_client, got_client), daemon=True)
        t_upstream = threading.Thread(target=drain, args=(app_upstream, got_upstream), daemon=True)
        t_client.start()
        t_upstream.start()

        rt = RealtimeGateway([], max_concurrency=1, env={})
        session = UpstreamSession(sock=relay_upstream, leftover=upstream_leftover, response=b"")
        sent, received = rt.relay(relay_client, client_leftover, session, idle_timeout=2.0)

        t_client.join(timeout=5)
        t_upstream.join(timeout=5)
        assert errors == []
        assert bytes(got_upstream) == client_leftover
        assert bytes(got_client) == upstream_leftover
        assert (sent, received) == (len(client_leftover), len(upstream_leftover))
    finally:
        for sock in (app_client, relay_client, relay_upstream, app_upstream):
            try:
                sock.close()
            except OSError:
                pass
