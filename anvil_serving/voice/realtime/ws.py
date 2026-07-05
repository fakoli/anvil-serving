"""Pure-stdlib RFC 6455 WebSocket transport (anvil task T011).

No ``websockets`` library, no FastAPI/uvicorn -- an HTTP Upgrade handshake
handled by ``http.server`` (:class:`WebSocketRequestHandler`) and hand-rolled
frame encode/decode (:func:`build_frame` / :func:`parse_frame`). Stdlib only:
``base64``, ``hashlib``, ``socket``, ``struct``, ``threading``,
``http.server``.

Design mirrors ``anvil_serving/router/front_door.py``'s house idiom: a
``ThreadingHTTPServer`` bound to ``127.0.0.1`` by default (CLAUDE.md gotcha
#1 -- never ``localhost``), one background thread per connection so a
long-lived WebSocket session never blocks another. Once the handshake
completes, this module takes over the raw socket for the lifetime of the
connection -- ``http.server``'s request/response machinery is only used to
negotiate the Upgrade.

Frame support: text (0x1) and binary (0x2) data frames, continuation (0x0)
fragmentation, close (0x8), ping (0x9) / pong (0xA) control frames. Per RFC
6455 s5.1, a client-to-server frame MUST be masked and a server-to-client
frame MUST NOT be masked; :func:`build_frame` ENCODES a frame in the
requested direction, and :meth:`WebSocketConnection.recv` ENFORCES the
expected direction on decode (fails the connection -- treats it as a closed
connection -- on a masking-bit violation), per RFC 6455 s5.1's "server MUST
close the connection upon receiving a frame that is not masked" (and the
client-side mirror: it must reject a MASKED frame from the server).
:func:`parse_frame` itself stays a lower-level, direction-agnostic decoder
(its own docstring covers exactly what it does and does not check) --
:meth:`WebSocketConnection.recv` is what plugs in the expected direction. A
minimal stdlib WebSocket *client* (:func:`client_handshake` +
:class:`WebSocketConnection`) is included so tests can round-trip a real
socket pair without any third-party dependency.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple

#: RFC 6455 s1.3 magic GUID appended to the client's Sec-WebSocket-Key before
#: SHA-1 + base64 to compute Sec-WebSocket-Accept.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Opcodes (RFC 6455 s5.2).
OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA
_CONTROL_OPCODES = (OP_CLOSE, OP_PING, OP_PONG)

#: Read chunk cap per ``recv()`` call while assembling a frame -- bounds a
#: single malicious/broken peer's ability to force huge memory allocation via
#: an oversized declared payload length before we've validated anything.
MAX_FRAME_PAYLOAD_BYTES = int(os.environ.get("ANVIL_WS_MAX_FRAME_BYTES", str(16 * 1024 * 1024)))


class WebSocketError(Exception):
    """Raised on a framing violation or a peer that closed mid-frame."""


def compute_accept_key(client_key: str) -> str:
    """RFC 6455 s1.3: base64(SHA1(client_key + magic GUID))."""
    digest = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _header_contains_token(value: Optional[str], token: str) -> bool:
    """True if comma-separated ``value`` contains ``token`` (case-insensitive).

    Both ``Connection`` and ``Upgrade`` are allowed to carry a comma-separated
    list per RFC 7230 s6.1/s6.7 (e.g. ``Connection: keep-alive, Upgrade``).
    """
    if not value:
        return False
    return token.lower() in [part.strip().lower() for part in value.split(",")]


def is_websocket_upgrade(headers) -> bool:
    """True if ``headers`` (an ``email.message.Message`` or a plain dict) is a
    valid RFC 6455 upgrade request: ``Upgrade: websocket``, ``Connection``
    containing ``upgrade``, a present ``Sec-WebSocket-Key``, and
    ``Sec-WebSocket-Version: 13``.
    """
    get = headers.get
    if not _header_contains_token(get("Upgrade"), "websocket"):
        return False
    if not _header_contains_token(get("Connection"), "upgrade"):
        return False
    if not (get("Sec-WebSocket-Key") or "").strip():
        return False
    if (get("Sec-WebSocket-Version") or "").strip() != "13":
        return False
    return True


def handshake_response_bytes(headers) -> Optional[bytes]:
    """Build the raw HTTP/1.1 101 response for a valid upgrade request.

    Returns ``None`` if ``headers`` is not a valid upgrade request (caller
    should answer 400 instead).
    """
    if not is_websocket_upgrade(headers):
        return None
    accept = compute_accept_key(headers.get("Sec-WebSocket-Key").strip())
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Accept: %s" % accept,
        "",
        "",
    ]
    return "\r\n".join(lines).encode("ascii")


# --------------------------------------------------------------------------- #
# Frame encode / decode
# --------------------------------------------------------------------------- #


def build_frame(opcode: int, payload: bytes, *, fin: bool = True, mask: bool = False) -> bytes:
    """Encode one RFC 6455 frame. ``mask=True`` for client->server frames
    (a random 4-byte masking key is generated and applied); ``mask=False``
    (the default) for server->client frames, which MUST NOT be masked.
    """
    length = len(payload)
    first_byte = (0x80 if fin else 0x00) | (opcode & 0x0F)
    out = bytearray([first_byte])

    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        out.append(mask_bit | length)
    elif length < (1 << 16):
        out.append(mask_bit | 126)
        out += struct.pack("!H", length)
    else:
        out.append(mask_bit | 127)
        out += struct.pack("!Q", length)

    if mask:
        masking_key = os.urandom(4)
        out += masking_key
        out += _apply_mask(payload, masking_key)
    else:
        out += payload
    return bytes(out)


def _apply_mask(data: bytes, key: bytes) -> bytes:
    """XOR ``data`` with the repeating 4-byte ``key`` (RFC 6455 s5.3)."""
    if not data:
        return b""
    key_rep = (key * (len(data) // 4 + 1))[: len(data)]
    return bytes(b ^ k for b, k in zip(data, key_rep))


class Frame:
    """One decoded WebSocket frame."""

    __slots__ = ("fin", "opcode", "payload")

    def __init__(self, fin: bool, opcode: int, payload: bytes) -> None:
        self.fin = fin
        self.opcode = opcode
        self.payload = payload

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "Frame(fin=%r, opcode=%#x, payload=%d bytes)" % (self.fin, self.opcode, len(self.payload))


def parse_frame(read_exact: Callable[[int], bytes], *, expect_masked: Optional[bool] = None) -> Frame:
    """Decode one frame, pulling exactly the bytes it needs via ``read_exact``.

    ``read_exact(n)`` must return exactly ``n`` bytes or raise (matches
    :meth:`WebSocketConnection._read_exact`, but is also directly usable
    against an in-memory buffer in tests).

    ``expect_masked`` is OPTIONAL and defaults to ``None`` (no direction
    check -- the same decoder serves both a server reading client frames and
    a test client reading server frames, and low-level round-trip tests feed
    either). Pass ``True``/``False`` to ENFORCE RFC 6455 s5.1's masking
    direction (raises :class:`WebSocketError` on a violation): a server
    reading a client frame passes ``True`` (client frames MUST be masked) and
    a client reading a server frame passes ``False`` (server frames MUST NOT
    be masked). :meth:`WebSocketConnection.recv` always passes this based on
    ``self.is_client``.
    """
    header = read_exact(2)
    first, second = header[0], header[1]
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F

    if expect_masked is not None and masked != expect_masked:
        raise WebSocketError(
            "protocol violation (RFC 6455 s5.1): %s frame has masking bit=%s, expected %s"
            % ("client->server" if expect_masked else "server->client", masked, expect_masked)
        )

    if length == 126:
        (length,) = struct.unpack("!H", read_exact(2))
    elif length == 127:
        (length,) = struct.unpack("!Q", read_exact(8))

    if length > MAX_FRAME_PAYLOAD_BYTES:
        raise WebSocketError(
            "frame payload %d bytes exceeds the %d byte cap" % (length, MAX_FRAME_PAYLOAD_BYTES)
        )

    masking_key = read_exact(4) if masked else b""
    payload = read_exact(length) if length else b""
    if masked:
        payload = _apply_mask(payload, masking_key)
    return Frame(fin, opcode, payload)


# --------------------------------------------------------------------------- #
# Connection: a raw socket + the frame/handshake logic above
# --------------------------------------------------------------------------- #


class WebSocketConnection:
    """One live WebSocket connection over a raw ``socket.socket``.

    ``is_client=True`` means frames THIS side sends are masked (and frames it
    receives are expected UNmasked, per RFC 6455 s5.1) -- the stdlib test
    client uses this. ``is_client=False`` (the default; the server side) is
    the reverse. :meth:`recv` transparently assembles fragmented messages
    (continuation frames), answers a ``PING`` with a ``PONG``, and turns a
    ``CLOSE`` frame into a clean ``None`` return (echoing the close frame back
    per RFC 6455 s5.5.1 before the caller tears the socket down).

    ``rfile``, if given, is a buffered file-like reader (``.read(n)`` blocks
    until exactly ``n`` bytes or EOF -- e.g. ``BaseHTTPRequestHandler``'s own
    ``self.rfile``); reads are routed through it INSTEAD OF a raw
    ``sock.recv()`` loop. This matters on the server side: ``http.server``
    reads the Upgrade request's headers off a BUFFERED ``self.rfile``, which
    may already have pulled subsequent TCP bytes off the wire into its own
    internal buffer while satisfying an earlier ``readline()`` -- including a
    client's first WebSocket frame(s), if it pipelined them right after the
    handshake request without waiting for the ``101`` response. Reading
    straight off the raw socket after the handshake would silently skip
    whatever ``self.rfile`` already buffered. Passing ``self.rfile`` here
    instead means EVERY post-handshake read -- the already-buffered bytes AND
    everything that arrives after -- goes through that one buffered stream,
    so nothing pipelined is lost. See ``_handle_upgrade`` below.
    """

    def __init__(self, sock: socket.socket, *, is_client: bool = False, rfile: Optional[Any] = None) -> None:
        self.sock = sock
        self.is_client = is_client
        self.closed = False
        self._rfile = rfile
        # Every send this connection makes -- whether from this connection's
        # own recv-driving thread or another thread holding a reference to it
        # (e.g. a caller fanning outbound events out on a separate sender
        # thread) -- goes through this one lock, so two threads sending
        # concurrently can never interleave their frame bytes on the wire
        # (see ``_send_frame``).
        self._send_lock = threading.Lock()

    # -- low-level exact-length read, tolerant of short TCP reads -----------
    def _read_exact(self, n: int) -> bytes:
        if n == 0:
            return b""
        if self._rfile is not None:
            data = self._rfile.read(n)
            if data is None or len(data) != n:
                raise WebSocketError("peer closed the connection mid-frame")
            return data
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise WebSocketError("peer closed the connection mid-frame")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    # -- sending --------------------------------------------------------------
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        # Locked so concurrent senders (e.g. a recv-loop thread sending an
        # immediate reply and a separate sender-drain thread forwarding
        # buffered events) can never interleave their frame bytes -- a data
        # race that would corrupt the frame stream for the peer.
        with self._send_lock:
            self.sock.sendall(build_frame(opcode, payload, mask=self.is_client))

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, data: bytes) -> None:
        self._send_frame(OP_BINARY, bytes(data))

    def send_json(self, obj: Any) -> None:
        self.send_text(json.dumps(obj))

    def ping(self, payload: bytes = b"") -> None:
        self._send_frame(OP_PING, payload)

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Send a CLOSE frame (idempotent) and mark this connection closed.

        Does not wait for the peer's echoed CLOSE -- callers that need a
        clean bidirectional shutdown should keep calling :meth:`recv` until
        it returns ``None``.
        """
        if self.closed:
            return
        self.closed = True
        try:
            payload = struct.pack("!H", code) + reason.encode("utf-8")
            self._send_frame(OP_CLOSE, payload)
        except OSError:
            pass  # peer's socket may already be gone; close is best-effort

    # -- receiving --------------------------------------------------------------
    def recv(self) -> Optional[Tuple[int, bytes]]:
        """Return the next complete message as ``(opcode, payload)``, or
        ``None`` once the connection is closed (by either side, OR because a
        peer violated RFC 6455 s5.1's masking direction -- see below).

        Assembles fragmented (continuation-frame) messages transparently;
        transparently answers ``PING`` with ``PONG`` and loops for the next
        frame rather than surfacing control frames to the caller.

        ENFORCES the RFC 6455 s5.1 masking direction on every decoded frame:
        this side is the SERVER (``is_client=False``) and MUST fail the
        connection if it receives an unmasked frame from the client, or this
        side is the CLIENT (``is_client=True``) and MUST fail the connection
        if it receives a masked frame from the server. A violation is treated
        the same as any other framing error -- ``self.closed`` is set and
        this returns ``None`` (see :func:`parse_frame`'s ``expect_masked``).
        """
        if self.closed:
            return None
        parts: list = []
        message_opcode: Optional[int] = None
        while True:
            try:
                frame = parse_frame(self._read_exact, expect_masked=not self.is_client)
            except WebSocketError:
                self.closed = True
                return None

            if frame.opcode == OP_CLOSE:
                # Echo the close frame back (RFC 6455 s5.5.1) then stop.
                # Routed through _send_frame (not a direct sock.sendall) so
                # this echo is serialized against any other thread sending on
                # this same connection, same as every other outbound frame.
                if not self.closed:
                    self.closed = True
                    try:
                        self._send_frame(OP_CLOSE, frame.payload)
                    except OSError:
                        pass
                return None
            if frame.opcode == OP_PING:
                self._send_frame(OP_PONG, frame.payload)
                continue
            if frame.opcode == OP_PONG:
                continue  # unsolicited/solicited pong: nothing to do

            if frame.opcode in (OP_TEXT, OP_BINARY):
                message_opcode = frame.opcode
                parts = [frame.payload]
            elif frame.opcode == OP_CONTINUATION:
                if message_opcode is None:
                    raise WebSocketError("continuation frame with no preceding data frame")
                parts.append(frame.payload)
            else:
                raise WebSocketError("unknown opcode %#x" % frame.opcode)

            if frame.fin:
                assert message_opcode is not None
                return message_opcode, b"".join(parts)

    def recv_text(self) -> Optional[str]:
        got = self.recv()
        if got is None:
            return None
        opcode, payload = got
        if opcode != OP_TEXT:
            raise WebSocketError("expected a TEXT frame, got opcode %#x" % opcode)
        return payload.decode("utf-8")


# --------------------------------------------------------------------------- #
# Client-side handshake (stdlib test client -- no third-party WS lib)
# --------------------------------------------------------------------------- #


def client_handshake(sock: socket.socket, *, host: str, port: int, path: str) -> WebSocketConnection:
    """Perform the CLIENT side of the RFC 6455 handshake over an already-
    connected ``sock``; return a :class:`WebSocketConnection` on success.

    Raises :class:`WebSocketError` if the server's response is not a valid
    ``101 Switching Protocols`` with a matching ``Sec-WebSocket-Accept``.
    """
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s:%d\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n" % (path, host, port, key)
    ).encode("ascii")
    sock.sendall(request)

    conn = WebSocketConnection(sock, is_client=True)
    response = _read_http_response_headers(conn._read_exact)
    status_line = response[0]
    if " 101 " not in (" " + status_line):
        raise WebSocketError("handshake failed: %s" % status_line)
    headers = _parse_header_lines(response[1:])
    expected = compute_accept_key(key)
    if headers.get("sec-websocket-accept") != expected:
        raise WebSocketError("Sec-WebSocket-Accept mismatch (got %r, want %r)"
                              % (headers.get("sec-websocket-accept"), expected))
    return conn


def _read_http_response_headers(read_exact: Callable[[int], bytes]) -> list:
    """Read an HTTP response byte-at-a-time up to the blank line terminator.

    Byte-at-a-time is deliberately simple (this is a test-only client, not a
    hot path) and avoids needing a buffered socket file object.
    """
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        buf += read_exact(1)
    text = buf.decode("iso-8859-1")
    return text.split("\r\n")[:-2]  # drop the trailing blank-line pair


def _parse_header_lines(lines: list) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return headers


# --------------------------------------------------------------------------- #
# Server: http.server-based upgrade handling
# --------------------------------------------------------------------------- #

OnConnect = Callable[[WebSocketConnection, str], None]
IntrospectionRoute = Callable[[], dict]


def _make_ws_handler(
    on_connect: OnConnect,
    ws_path: str,
    extra_routes: Optional[Dict[str, IntrospectionRoute]] = None,
):
    routes = dict(extra_routes or {})

    class WebSocketRequestHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "anvil-realtime"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            ws_path_norm = ws_path.rstrip("/") or "/"

            if path == ws_path_norm:
                self._handle_upgrade()
                return

            route_fn = routes.get(path)
            if route_fn is not None:
                self._handle_introspection(route_fn)
                return

            self.send_error(404, "no route %s" % self.path)

        def _handle_introspection(self, route_fn: IntrospectionRoute) -> None:
            payload = json.dumps(route_fn()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _handle_upgrade(self) -> None:
            resp = handshake_response_bytes(self.headers)
            if resp is None:
                self.send_error(400, "expected a WebSocket upgrade request")
                return
            self.wfile.write(resp)
            self.wfile.flush()
            # From here on, this module owns the raw socket for the rest of the
            # connection's lifetime; tell BaseHTTPRequestHandler's bookkeeping
            # not to try to read another HTTP request off it afterwards.
            self.close_connection = True
            # Read via self.rfile (NOT a raw sock.recv() loop): BaseHTTPRequestHandler
            # parsed the Upgrade request's headers off this SAME buffered
            # stream, which may have already pulled a client's pipelined
            # first frame(s) into its internal buffer -- reading raw off the
            # socket from here on would silently skip those bytes (see
            # WebSocketConnection's own docstring, ``rfile`` param).
            conn = WebSocketConnection(self.connection, is_client=False, rfile=self.rfile)
            try:
                on_connect(conn, self.path)
            except Exception:  # noqa: BLE001 - one connection's crash must not kill the server
                pass
            finally:
                conn.close()

        def log_message(self, *args) -> None:  # keep the server quiet
            pass

    return WebSocketRequestHandler


def make_ws_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    on_connect: Optional[OnConnect] = None,
    *,
    ws_path: str = "/v1/realtime",
    extra_routes: Optional[Dict[str, IntrospectionRoute]] = None,
) -> ThreadingHTTPServer:
    """Build (but do not start) the Realtime WebSocket server.

    ``on_connect(conn, path)`` is called on the connection's OWN background
    thread (``ThreadingHTTPServer`` gives one thread per accepted TCP
    connection, same as ``router/front_door.py``) once the handshake
    completes; it should run until the session ends (typically driving a
    :class:`~anvil_serving.voice.realtime.service.RealtimeService` loop).
    ``extra_routes`` maps a plain HTTP GET path (e.g. ``/pool``, ``/usage``)
    to a zero-arg callable returning a JSON-serializable dict -- the
    introspection endpoints a bounded session pool exposes (see ``pool.py``).
    Pass ``port=0`` to bind an ephemeral port (read back via
    ``server.server_address[1]``). Binds ``127.0.0.1`` by default -- never
    ``localhost`` (CLAUDE.md gotcha #1: the Windows IPv6 stall).
    """
    if on_connect is None:
        def on_connect(conn: WebSocketConnection, path: str) -> None:  # pragma: no cover - trivial default
            conn.close()

    httpd = ThreadingHTTPServer((host, port), _make_ws_handler(on_connect, ws_path, extra_routes))
    httpd.daemon_threads = True
    return httpd


def serve_forever_in_background(server: ThreadingHTTPServer) -> threading.Thread:
    """Run ``server.serve_forever()`` on a daemon thread; return that thread."""
    thread = threading.Thread(target=server.serve_forever, name="anvil-realtime-ws", daemon=True)
    thread.start()
    return thread
