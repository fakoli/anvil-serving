"""Frame encode/decode + RFC 6455 handshake, exercised with the stdlib test
client (``ws.py``'s own :func:`client_handshake`) -- no ``websockets``
library, no third-party dependency anywhere in this test. Dependency-light:
stdlib ``socket``/``threading`` only.
"""

from __future__ import annotations

import base64
import os
import socket
import threading

import pytest

from anvil_serving.voice.bridge import (
    ForwardingBridgeService,
    TCPBridgeRoute,
)

from anvil_serving.voice.realtime.ws import (
    OP_BINARY,
    OP_CONTINUATION,
    OP_TEXT,
    WebSocketConnection,
    WebSocketError,
    build_frame,
    client_handshake,
    compute_accept_key,
    make_ws_server,
)


def test_forwarding_bridge_lifecycle_is_mini_owned_and_keeps_bounded_logs():
    started = threading.Event()

    def serve(routes, stop_event, *, log):
        assert tuple(routes)[0].name == "stt"
        log("x" * 64)
        started.set()
        stop_event.wait(2)

    bridge = ForwardingBridgeService(
        [TCPBridgeRoute("stt", "127.0.0.1", 30110, "100.64.0.10", 30110)],
        max_log_bytes=16,
        serve=serve,
    )
    assert bridge.start().owner == "mini"
    assert started.wait(1)
    assert bridge.stop(timeout=1).running is False
    logs = bridge.logs()
    assert logs.truncated is True
    assert sum(len(line.encode("utf-8")) for line in logs.lines) <= logs.max_bytes


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "1"])
def test_forwarding_bridge_stop_rejects_invalid_timeout(timeout):
    bridge = _bridge(lambda routes, stop_event, *, log: None)

    with pytest.raises(ValueError, match="timeout must be positive"):
        bridge.stop(timeout=timeout)


def _bridge(serve, *, max_log_bytes=64 * 1024):
    return ForwardingBridgeService(
        [TCPBridgeRoute("stt", "127.0.0.1", 30110, "100.64.0.10", 30110)],
        max_log_bytes=max_log_bytes,
        serve=serve,
    )


class _BufReader:
    """Feeds :func:`parse_frame` from an in-memory ``bytes`` buffer."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def __call__(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        if len(chunk) != n:
            raise WebSocketError("buffer underrun in test reader")
        self._pos += n
        return chunk


# --------------------------------------------------------------------------- #
# compute_accept_key -- the official RFC 6455 s1.3 worked example
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# is_websocket_upgrade
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# frame encode/decode round trips
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# parse_frame / recv: RFC 6455 s5.1 masking-direction enforcement
# --------------------------------------------------------------------------- #


def test_recv_rejects_masked_frame_from_server_client_side():
    """The client side (is_client=True) MUST fail the connection if it
    receives a masked frame from the server (RFC 6455 s5.1)."""
    server_sock, client_sock = socket.socketpair()
    try:
        client_conn = WebSocketConnection(client_sock, is_client=True)
        server_sock.sendall(build_frame(OP_TEXT, b"hello", mask=True))
        assert client_conn.recv() is None
        assert client_conn.closed is True
    finally:
        server_sock.close()
        client_sock.close()


# --------------------------------------------------------------------------- #
# Full client<->server round trip over a real loopback socket
# --------------------------------------------------------------------------- #


def _start_echo_server():
    received = []

    def on_connect(conn: WebSocketConnection, path: str) -> None:
        while True:
            got = conn.recv()
            if got is None:
                break
            opcode, payload = got
            received.append((opcode, payload))
            if opcode == OP_TEXT:
                conn.send_text(payload.decode("utf-8") + "-echo")
            else:
                conn.send_binary(payload + b"-echo")

    server = make_ws_server("127.0.0.1", 0, on_connect)
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    return server, thread, received


def _send_handshake_response_with_headers(sock: socket.socket, header_lines: list[str]) -> None:
    request = bytearray()
    while b"\r\n\r\n" not in request:
        chunk = sock.recv(1)
        if not chunk:
            return
        request.extend(chunk)
    headers = {}
    for line in request.decode("iso-8859-1").split("\r\n"):
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    accept = compute_accept_key(headers["sec-websocket-key"])
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        + "\r\n".join(line.format(accept=accept) for line in header_lines)
        + "\r\n\r\n"
    ).encode("ascii")
    sock.sendall(response)


# --------------------------------------------------------------------------- #
# F1 -- fragmented-message caps (MAX_MESSAGE_BYTES / MAX_MESSAGE_FRAGMENTS)
# --------------------------------------------------------------------------- #


def test_recv_rejects_fragmented_message_exceeding_max_fragment_count(monkeypatch):
    """Regression for F1's fragment-count cap: many tiny fragments that never
    trip the byte cap must still be bounded by a fragment-count cap."""
    import anvil_serving.voice.realtime.ws as ws_mod

    monkeypatch.setattr(ws_mod, "MAX_MESSAGE_FRAGMENTS", 2)
    server_sock, client_sock = socket.socketpair()
    try:
        server_conn = WebSocketConnection(server_sock, is_client=False)
        client_sock.sendall(build_frame(OP_BINARY, b"a", fin=False, mask=True))
        client_sock.sendall(build_frame(OP_CONTINUATION, b"b", fin=False, mask=True))
        client_sock.sendall(build_frame(OP_CONTINUATION, b"c", fin=False, mask=True))

        with pytest.raises(WebSocketError):
            server_conn.recv()
        assert server_conn.closed is True
    finally:
        server_sock.close()
        client_sock.close()


# --------------------------------------------------------------------------- #
# F2 -- non-loopback bind requires a bearer token
# --------------------------------------------------------------------------- #


def test_loopback_without_token_upgrades_without_auth():
    """Loopback + no token stays allowed (trusted-local default)."""
    server, thread, received = _start_echo_server()
    host, port = server.server_address[:2]
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.settimeout(5)
        conn = client_handshake(sock, host=host, port=port, path="/v1/realtime")
        conn.send_text("no-auth-needed")
        assert conn.recv_text() == "no-auth-needed-echo"
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _raw_upgrade_request(port: int, *, extra_headers: str = "") -> bytes:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    return (
        "GET /v1/realtime HTTP/1.1\r\n"
        "Host: 127.0.0.1:%d\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "%s"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n" % (port, extra_headers, key)
    ).encode("ascii")


def test_non_loopback_bind_with_token_gates_the_upgrade_on_a_valid_bearer_token(monkeypatch):
    """Non-loopback + token: construction succeeds; a missing or wrong bearer
    token is rejected with 401 BEFORE the 101 upgrade; the correct token
    upgrades normally. Connects via 127.0.0.1 -- binding 0.0.0.0 also accepts
    loopback connections, so this stays hermetic (no real LAN needed).
    """
    monkeypatch.setenv("ANVIL_TEST_WS_TOKEN", "s3cr3t-realtime-token")
    server = make_ws_server(
        "0.0.0.0",
        0,
        lambda conn, path: conn.close(),
        token_env="ANVIL_TEST_WS_TOKEN",
    )
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        # Missing token -> 401.
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.settimeout(5)
        sock.sendall(_raw_upgrade_request(port))
        assert sock.recv(4096).startswith(b"HTTP/1.1 401")
        sock.close()

        # Wrong token -> 401.
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.settimeout(5)
        sock.sendall(
            _raw_upgrade_request(port, extra_headers="Authorization: Bearer wrong-token\r\n")
        )
        assert sock.recv(4096).startswith(b"HTTP/1.1 401")
        sock.close()

        # Correct token -> 101 Switching Protocols.
        sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        sock.settimeout(5)
        sock.sendall(
            _raw_upgrade_request(
                port, extra_headers="Authorization: Bearer s3cr3t-realtime-token\r\n"
            )
        )
        assert sock.recv(4096).startswith(b"HTTP/1.1 101")
        sock.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# F4 -- idle-socket read timeout so an abandoned connection frees its thread
# --------------------------------------------------------------------------- #


def test_recv_returns_none_cleanly_on_idle_socket_timeout():
    """Low-level regression via a real socketpair: with a short socket-level
    read timeout set (mirrors what `make_ws_server`'s `idle_timeout` applies
    to an accepted connection), a peer that never sends anything must cause
    `recv()` to return `None` (a clean close) instead of hanging forever or
    raising an uncaught `socket.timeout`.
    """
    server_sock, client_sock = socket.socketpair()
    server_sock.settimeout(0.2)
    try:
        server_conn = WebSocketConnection(server_sock, is_client=False)
        assert server_conn.recv() is None
        assert server_conn.closed is True
    finally:
        server_sock.close()
        client_sock.close()


def test_idle_connection_is_closed_by_make_ws_server_idle_timeout():
    """Integration regression: a real accepted connection that completes the
    handshake and then sends nothing must have its `on_connect` handler's
    `recv()` return (thanks to the idle timeout), freeing the handler thread,
    instead of blocking forever.
    """
    connect_done = threading.Event()

    def on_connect(conn, path):
        result = conn.recv()  # blocks until the idle timeout fires
        assert result is None
        connect_done.set()

    server = make_ws_server("127.0.0.1", 0, on_connect, idle_timeout=0.3)
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.settimeout(5)
        client_handshake(sock, host=host, port=port, path="/v1/realtime")
        # Deliberately never send another byte.
        assert connect_done.wait(timeout=3), "idle timeout never fired"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_introspection_route_served_as_json():
    server = make_ws_server(
        "127.0.0.1",
        0,
        lambda conn, path: conn.close(),
        extra_routes={"/usage": lambda: {"claims_total": 3}},
    )
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        import urllib.request

        with urllib.request.urlopen("http://%s:%d/usage" % (host, port), timeout=5) as resp:
            import json

            body = json.loads(resp.read().decode("utf-8"))
        assert body == {"claims_total": 3}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
