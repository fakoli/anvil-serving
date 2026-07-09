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

from anvil_serving.voice.realtime.ws import (
    OP_BINARY,
    OP_CONTINUATION,
    OP_TEXT,
    WebSocketConnection,
    WebSocketError,
    build_frame,
    client_handshake,
    compute_accept_key,
    is_websocket_upgrade,
    make_ws_server,
    parse_frame,
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


def test_compute_accept_key_matches_rfc6455_worked_example():
    assert compute_accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


# --------------------------------------------------------------------------- #
# is_websocket_upgrade
# --------------------------------------------------------------------------- #


def test_is_websocket_upgrade_accepts_valid_headers():
    headers = {
        "Upgrade": "websocket",
        "Connection": "keep-alive, Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
    }
    assert is_websocket_upgrade(headers) is True


@pytest.mark.parametrize(
    "missing",
    ["Upgrade", "Connection", "Sec-WebSocket-Key", "Sec-WebSocket-Version"],
)
def test_is_websocket_upgrade_rejects_missing_header(missing):
    headers = {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "13",
    }
    del headers[missing]
    assert is_websocket_upgrade(headers) is False


def test_is_websocket_upgrade_rejects_wrong_version():
    headers = {
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
        "Sec-WebSocket-Version": "8",
    }
    assert is_websocket_upgrade(headers) is False


# --------------------------------------------------------------------------- #
# frame encode/decode round trips
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("opcode", [OP_TEXT, OP_BINARY])
@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"hello",
        b"x" * 125,          # boundary: last length that fits the 7-bit form
        b"x" * 126,          # boundary: first length needing the 16-bit form
        b"y" * 70000,        # boundary: needs the 64-bit extended-length form
    ],
    ids=["empty", "short", "len125", "len126", "len70000"],
)
def test_build_and_parse_frame_round_trip_unmasked(opcode, payload):
    frame_bytes = build_frame(opcode, payload, mask=False)
    frame = parse_frame(_BufReader(frame_bytes))
    assert frame.fin is True
    assert frame.opcode == opcode
    assert frame.payload == payload


def test_build_and_parse_frame_round_trip_masked():
    payload = b"masked payload from a client"
    frame_bytes = build_frame(OP_TEXT, payload, mask=True)
    frame = parse_frame(_BufReader(frame_bytes))
    assert frame.payload == payload
    # A masked frame's on-wire bytes never contain the raw payload verbatim
    # (extremely unlikely to coincide by chance for this payload length).
    assert payload not in frame_bytes


def test_build_frame_masked_sets_mask_bit_and_differs_between_calls():
    payload = b"same payload"
    first = build_frame(OP_TEXT, payload, mask=True)
    second = build_frame(OP_TEXT, payload, mask=True)
    assert first[1] & 0x80  # MASK bit set in the length byte
    # Random masking key per call -> encoded bytes differ even for identical
    # payloads (defense-in-depth check that we're not reusing a fixed key).
    assert first != second


def test_parse_frame_rejects_oversized_payload(monkeypatch):
    import anvil_serving.voice.realtime.ws as ws_mod

    monkeypatch.setattr(ws_mod, "MAX_FRAME_PAYLOAD_BYTES", 10)
    frame_bytes = build_frame(OP_BINARY, b"x" * 11, mask=False)
    with pytest.raises(WebSocketError):
        parse_frame(_BufReader(frame_bytes))


# --------------------------------------------------------------------------- #
# parse_frame / recv: RFC 6455 s5.1 masking-direction enforcement
# --------------------------------------------------------------------------- #


def test_parse_frame_expect_masked_none_skips_the_direction_check():
    # Default behavior (no expect_masked given): both directions accepted,
    # matching every other round-trip test above.
    unmasked = build_frame(OP_TEXT, b"hi", mask=False)
    masked = build_frame(OP_TEXT, b"hi", mask=True)
    assert parse_frame(_BufReader(unmasked)).payload == b"hi"
    assert parse_frame(_BufReader(masked)).payload == b"hi"


def test_parse_frame_accepts_correctly_masked_client_frame():
    frame_bytes = build_frame(OP_TEXT, b"hi", mask=True)
    frame = parse_frame(_BufReader(frame_bytes), expect_masked=True)
    assert frame.payload == b"hi"


def test_parse_frame_rejects_client_frame_missing_mask():
    frame_bytes = build_frame(OP_TEXT, b"hi", mask=False)
    with pytest.raises(WebSocketError):
        parse_frame(_BufReader(frame_bytes), expect_masked=True)


def test_parse_frame_accepts_correctly_unmasked_server_frame():
    frame_bytes = build_frame(OP_TEXT, b"hi", mask=False)
    frame = parse_frame(_BufReader(frame_bytes), expect_masked=False)
    assert frame.payload == b"hi"


def test_parse_frame_rejects_server_frame_that_is_masked():
    frame_bytes = build_frame(OP_TEXT, b"hi", mask=True)
    with pytest.raises(WebSocketError):
        parse_frame(_BufReader(frame_bytes), expect_masked=False)


def test_recv_rejects_unmasked_frame_from_client_server_side():
    """The server side (is_client=False) MUST fail the connection if it
    receives an unmasked frame from a client (RFC 6455 s5.1)."""
    server_sock, client_sock = socket.socketpair()
    try:
        server_conn = WebSocketConnection(server_sock, is_client=False)
        client_sock.sendall(build_frame(OP_TEXT, b"hello", mask=False))
        assert server_conn.recv() is None
        assert server_conn.closed is True
    finally:
        server_sock.close()
        client_sock.close()


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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, received


def test_handshake_and_text_binary_frames_over_real_socket():
    server, thread, received = _start_echo_server()
    host, port = server.server_address[:2]
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.settimeout(5)
        conn = client_handshake(sock, host=host, port=port, path="/v1/realtime")

        conn.send_text("hello there")
        assert conn.recv_text() == "hello there-echo"

        conn.send_binary(b"\x00\x01\x02binary")
        opcode, payload = conn.recv()
        assert opcode == OP_BINARY
        assert payload == b"\x00\x01\x02binary-echo"

        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert (OP_TEXT, b"hello there") in received
    assert (OP_BINARY, b"\x00\x01\x02binary") in received


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


@pytest.mark.parametrize(
    ("header_lines", "match"),
    [
        (
            ["Connection: Upgrade", "Sec-WebSocket-Accept: {accept}"],
            "Upgrade",
        ),
        (
            ["Upgrade: websocket", "Sec-WebSocket-Accept: {accept}"],
            "Connection",
        ),
    ],
)
def test_client_handshake_requires_upgrade_response_headers(header_lines, match):
    server_sock, client_sock = socket.socketpair()
    thread = threading.Thread(
        target=_send_handshake_response_with_headers,
        args=(server_sock, header_lines),
        daemon=True,
    )
    thread.start()
    try:
        with pytest.raises(WebSocketError, match=match):
            client_handshake(client_sock, host="127.0.0.1", port=8765, path="/v1/realtime")
    finally:
        server_sock.close()
        client_sock.close()
        thread.join(timeout=5)


def test_handshake_upgrade_does_not_drop_a_pipelined_client_frame():
    """Regression test: a client that sends its first WS frame in the SAME
    write as the Upgrade request (never waiting for the 101 response) must
    not have that frame silently dropped. Before the fix, ``_handle_upgrade``
    handed the raw socket to ``WebSocketConnection`` and read via
    ``sock.recv()``, bypassing whatever ``BaseHTTPRequestHandler``'s own
    buffered ``self.rfile`` had already pulled off the wire while parsing the
    request headers -- which, for a small pipelined write like this one,
    is the entire thing (see ``WebSocketConnection``'s ``rfile`` docstring).
    """
    server, thread, received = _start_echo_server()
    host, port = server.server_address[:2]
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.settimeout(5)

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET /v1/realtime HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n" % (host, port, key)
        ).encode("ascii")
        pipelined_frame = build_frame(OP_TEXT, b"pipelined hello", mask=True)
        # ONE write carrying the handshake request AND the first WS frame --
        # nothing waits for the 101 response before it's sent.
        sock.sendall(request + pipelined_frame)

        # Read (and discard) the 101 response headers off the raw socket.
        buf = bytearray()
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(1)

        conn = WebSocketConnection(sock, is_client=True)
        opcode, payload = conn.recv()
        assert opcode == OP_TEXT
        assert payload == b"pipelined hello-echo"
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert (OP_TEXT, b"pipelined hello") in received


def test_concurrent_sends_from_two_threads_are_serialized_and_never_interleave():
    """Regression test for the concurrent-sender corruption risk flagged
    against ``scripts/voice/realtime_sdk_client_demo.py``'s ``build_server``
    (a recv-loop thread sending immediate replies + a separate sender-drain
    thread forwarding buffered events, both writing to ONE
    ``WebSocketConnection``). Without ``_send_frame``'s internal lock, two
    threads' ``sock.sendall(build_frame(...))`` calls could interleave and
    corrupt the frame stream; with it, every frame the reader gets back must
    be intact and match one of the two known payloads -- never a mangled
    mix of both, and never a framing error.
    """
    server_sock, client_sock = socket.socketpair()
    sender_conn = WebSocketConnection(server_sock, is_client=False)
    reader_conn = WebSocketConnection(client_sock, is_client=True)
    try:
        n_per_thread = 100
        payload_a = b"A" * 40
        payload_b = b"B" * 55
        counts = {payload_a: 0, payload_b: 0}
        counts_lock = threading.Lock()
        unexpected = []

        def send(payload):
            for _ in range(n_per_thread):
                sender_conn.send_binary(payload)

        def read_loop():
            for _ in range(2 * n_per_thread):
                got = reader_conn.recv()
                if got is None:
                    break
                _, payload = got
                with counts_lock:
                    if payload in counts:
                        counts[payload] += 1
                    else:
                        unexpected.append(payload)

        t_read = threading.Thread(target=read_loop)
        t_a = threading.Thread(target=send, args=(payload_a,))
        t_b = threading.Thread(target=send, args=(payload_b,))
        t_read.start()
        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)
        t_read.join(timeout=10)

        assert not unexpected, "corrupted/unexpected frame payload(s) received: %r" % (unexpected,)
        assert counts[payload_a] == n_per_thread
        assert counts[payload_b] == n_per_thread
    finally:
        server_sock.close()
        client_sock.close()


def test_handshake_rejects_non_upgrade_get_request():
    server = make_ws_server("127.0.0.1", 0, lambda conn, path: conn.close())
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.settimeout(5)
        sock.sendall(b"GET /v1/realtime HTTP/1.1\r\nHost: %s:%d\r\n\r\n" % (host.encode(), port))
        response = sock.recv(4096)
        assert response.startswith(b"HTTP/1.1 400")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# F1 -- fragmented-message caps (MAX_MESSAGE_BYTES / MAX_MESSAGE_FRAGMENTS)
# --------------------------------------------------------------------------- #


def test_recv_rejects_fragmented_message_exceeding_max_message_bytes(monkeypatch):
    """Regression for F1: continuation (`fin=0`) frames each individually
    under MAX_FRAME_PAYLOAD_BYTES must still be capped by a CUMULATIVE total
    across one fragmented message -- otherwise a client can stream frames
    forever and force unbounded growth of `recv()`'s `parts` list. Exceeding
    the cap must close the connection (WS status 1009) and RAISE, not just
    quietly return None like a normal disconnect.
    """
    import anvil_serving.voice.realtime.ws as ws_mod

    monkeypatch.setattr(ws_mod, "MAX_MESSAGE_BYTES", 20)
    server_sock, client_sock = socket.socketpair()
    try:
        server_conn = WebSocketConnection(server_sock, is_client=False)
        # Masked initial fragment (fin=0): 10 bytes, well under both caps alone.
        client_sock.sendall(build_frame(OP_BINARY, b"x" * 10, fin=False, mask=True))
        # Continuation frames pushing the running total past the 20-byte cap.
        client_sock.sendall(build_frame(OP_CONTINUATION, b"y" * 10, fin=False, mask=True))
        client_sock.sendall(build_frame(OP_CONTINUATION, b"z" * 10, fin=False, mask=True))

        with pytest.raises(WebSocketError):
            server_conn.recv()
        assert server_conn.closed is True
    finally:
        server_sock.close()
        client_sock.close()


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


def test_recv_accepts_a_fragmented_message_within_both_caps():
    """Sanity check alongside the two rejection tests above: a fragmented
    message that stays within both caps must still assemble normally."""
    server_sock, client_sock = socket.socketpair()
    try:
        server_conn = WebSocketConnection(server_sock, is_client=False)
        client_sock.sendall(build_frame(OP_TEXT, b"hel", fin=False, mask=True))
        client_sock.sendall(build_frame(OP_CONTINUATION, b"lo", fin=True, mask=True))
        opcode, payload = server_conn.recv()
        assert opcode == OP_TEXT
        assert payload == b"hello"
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


def test_make_ws_server_refuses_non_loopback_host_without_token():
    """Construction itself must be refused -- no socket should even be bound."""
    with pytest.raises(ValueError):
        make_ws_server("0.0.0.0", 0, lambda conn, path: conn.close())


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
        "0.0.0.0", 0, lambda conn, path: conn.close(), token_env="ANVIL_TEST_WS_TOKEN",
    )
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
        sock.sendall(_raw_upgrade_request(port, extra_headers="Authorization: Bearer wrong-token\r\n"))
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


def test_make_ws_server_raises_when_token_env_is_unset(monkeypatch):
    monkeypatch.delenv("ANVIL_TEST_WS_TOKEN_UNSET", raising=False)
    with pytest.raises(ValueError):
        make_ws_server(
            "127.0.0.1", 0, lambda conn, path: conn.close(), token_env="ANVIL_TEST_WS_TOKEN_UNSET",
        )


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
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
        "127.0.0.1", 0, lambda conn, path: conn.close(),
        extra_routes={"/usage": lambda: {"claims_total": 3}},
    )
    server.timeout = 5
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
