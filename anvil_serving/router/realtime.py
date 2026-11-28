"""Realtime WebSocket relay behind ``/v1/realtime`` (ADR-0050).

The router authenticates the caller and resolves one configured
``[[router.realtime_routes]]`` entry. It then opens the upstream upgrade itself,
carrying only the route's own bearer token: the caller's ``Authorization``,
``Origin``, cookies and any credential-bearing subprotocol never leave the
router. After a validated ``101`` it copies bytes both ways without parsing
frames. Stdlib only, like the rest of the router.
"""
from __future__ import annotations

import re
import selectors
import socket
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..websocket_handshake import compute_accept_key, is_websocket_upgrade
from .config import RealtimeRoute

__all__ = ["REALTIME_PATH", "RealtimeError", "RealtimeGateway", "UpstreamSession", "is_websocket_upgrade", "probe_realtime_health", "realtime_health_url"]

REALTIME_PATH = "/v1/realtime"
HANDSHAKE_TIMEOUT_SECONDS = 10.0
# ponytail: fixed idle cap so an abandoned session cannot pin a slot forever;
# make it a route field if long silent sessions turn out to be legitimate.
IDLE_TIMEOUT_SECONDS = 300.0
HEALTH_CACHE_SECONDS = 5.0
HEALTH_TIMEOUT_SECONDS = 2.0
_MAX_RESPONSE_HEAD_BYTES = 16 * 1024
_RELAY_CHUNK = 64 * 1024
_RELAY_BUFFER_LIMIT = 256 * 1024
# Browser SDKs smuggle API keys through subprotocols; never forward those.
_CREDENTIAL_PROTOCOL_PREFIXES = ("openai-insecure-api-key.",)
_CLIENT_KEY = re.compile(r"[A-Za-z0-9+/]{22}==")
_TOKEN_CHARS = frozenset("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _valid_header_value(value: str) -> bool:
    """True if ``value`` is non-empty Latin-1 without CR, LF, NUL, or control chars."""
    return bool(value) and all(0x20 <= ord(ch) <= 0xFF and ord(ch) != 0x7F for ch in value)


def _valid_request_target(value: str) -> bool:
    """True if ``value`` is an HTTP request-target without whitespace or control chars."""
    return bool(value) and all(0x21 <= ord(ch) <= 0x7E for ch in value)


def realtime_health_url(route: RealtimeRoute) -> str:
    """Return the route's token-free configured health path on its own origin."""
    parsed = urllib.parse.urlsplit(route.base_url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, route.health_path, "", ""))


class RealtimeError(Exception):
    """A sanitized refusal sent to the caller before any ``101``."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class UpstreamSession:
    """An upgraded upstream socket plus the ``101`` to hand the caller."""

    sock: socket.socket
    leftover: bytes  # upstream bytes that arrived in the same reads as the 101
    response: bytes

    def close(self) -> None:
        _shutdown(self.sock)
        try:
            self.sock.close()
        except OSError:
            pass


def _shutdown(sock: socket.socket) -> None:
    # socket-level, never SSLSocket.shutdown: that discards TLS state while the
    # other pump may still be reading, which would surface raw ciphertext.
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass


def _offered_protocols(headers) -> Tuple[str, ...]:
    offered = []
    for value in headers.get_all("Sec-WebSocket-Protocol") or []:
        for item in value.split(","):
            item = item.strip()
            if not item or not set(item) <= _TOKEN_CHARS:
                raise RealtimeError(400, "invalid_request", "invalid Sec-WebSocket-Protocol")
            if not item.lower().startswith(_CREDENTIAL_PROTOCOL_PREFIXES):
                offered.append(item)
    return tuple(dict.fromkeys(offered))


class RealtimeGateway:
    """Resolve realtime routes, bound sessions, probe health and relay sockets."""

    def __init__(
        self,
        routes: Sequence[RealtimeRoute],
        *,
        max_concurrency: int,
        env: Optional[Mapping[str, str]] = None,
        connect: Callable[..., socket.socket] = socket.create_connection,
        probe: Optional[Callable[[str, float], int]] = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        import os
        environ = os.environ if env is None else env
        self._connect = connect
        self._probe = probe or probe_realtime_health
        self._monotonic = monotonic
        self._limit = threading.BoundedSemaphore(max_concurrency)
        self._routes: Dict[str, RealtimeRoute] = {}
        self._tokens: Dict[str, Optional[str]] = {}
        for route in routes:
            token = None
            if route.auth_env:
                raw_token = environ.get(route.auth_env) or ""
                token = raw_token.strip() or None
                if token is None:
                    # An unresolved credential leaves the route unavailable; never fall back.
                    print(f"[anvil-serving] realtime route {route.id!r} not bound: auth_env "
                          f"{route.auth_env!r} is unset/empty in the environment", file=sys.stderr, flush=True)
                    continue
                if any(ch in raw_token for ch in ("\r", "\n", "\x00")) or not _valid_header_value(token):
                    print(f"[anvil-serving] realtime route {route.id!r} not bound: auth_env "
                          f"{route.auth_env!r} contains invalid header characters", file=sys.stderr, flush=True)
                    continue
            self._routes[route.id] = route
            self._tokens[route.id] = token
        declared = list(routes)
        chosen = declared[0] if len(declared) == 1 else next((r for r in declared if r.default), None)
        self._default = chosen if chosen is not None and chosen.id in self._routes else None
        self._health: Dict[str, Tuple[float, bool]] = {}
        self._health_lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._routes)

    def resolve(self, route_id: Optional[str]) -> RealtimeRoute:
        route = self._routes.get(route_id) if route_id is not None else self._default
        if route is None:
            raise RealtimeError(404, "route_not_found", "realtime route is not configured")
        return route

    def acquire(self) -> bool:
        return self._limit.acquire(blocking=False)

    def release(self) -> None:
        self._limit.release()

    def healthy(self, route: RealtimeRoute) -> bool:
        """Cached token-free readiness probe of the route's ``health_path``."""
        now = self._monotonic()
        with self._health_lock:
            cached = self._health.get(route.id)
            if cached is not None and now - cached[0] < HEALTH_CACHE_SECONDS:
                return cached[1]
        try:
            ok = self._probe(realtime_health_url(route), HEALTH_TIMEOUT_SECONDS) == 200
        except Exception:  # noqa: BLE001 - any probe failure means not ready
            ok = False
        with self._health_lock:
            self._health[route.id] = (now, ok)
        return ok

    def route_healthy(self, route_id: str) -> bool:
        """Return the bound route's cached token-free readiness."""
        route = self._routes.get(route_id)
        return route is not None and self.healthy(route)

    def open_upstream(self, route: RealtimeRoute, headers, query: str,
                      request_id: Optional[str] = None) -> UpstreamSession:
        """Perform the upstream upgrade with only the route credential."""
        if not is_websocket_upgrade(headers):
            raise RealtimeError(426, "upgrade_required", "use a WebSocket upgrade for /v1/realtime")
        client_key = headers.get("Sec-WebSocket-Key").strip()
        if _CLIENT_KEY.fullmatch(client_key) is None:  # RFC 6455: base64 of a 16-byte nonce
            raise RealtimeError(400, "invalid_request", "invalid Sec-WebSocket-Key")
        protocols = _offered_protocols(headers)
        parsed = urllib.parse.urlsplit(route.base_url)
        pairs = [(k, v) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True) if k != "route"]
        target = (parsed.path.rstrip("/") + "/realtime") + ("?" + urllib.parse.urlencode(pairs) if pairs else "")
        if not _valid_request_target(target) or not _valid_header_value(parsed.netloc):
            raise RealtimeError(502, "upstream_unavailable", "realtime upstream request is invalid")
        lines = [
            f"GET {target} HTTP/1.1", f"Host: {parsed.netloc}", "Upgrade: websocket",
            "Connection: Upgrade", f"Sec-WebSocket-Key: {client_key}", "Sec-WebSocket-Version: 13",
        ]
        if protocols:
            lines.append("Sec-WebSocket-Protocol: " + ", ".join(protocols))
        token = self._tokens.get(route.id)
        if token is not None:
            if not _valid_header_value(token):
                raise RealtimeError(502, "upstream_unavailable", "realtime upstream request is invalid")
            lines.append("Authorization: Bearer " + token)
        if request_id:
            if not _valid_header_value(request_id):
                raise RealtimeError(502, "upstream_unavailable", "realtime upstream request is invalid")
            lines.append("X-Request-Id: " + request_id)
        try:
            request = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        except UnicodeEncodeError:
            raise RealtimeError(502, "upstream_unavailable", "realtime upstream request is invalid") from None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        deadline = self._monotonic() + HANDSHAKE_TIMEOUT_SECONDS

        def remaining() -> float:
            left = deadline - self._monotonic()
            if left <= 0:
                raise RealtimeError(504, "upstream_timeout", "realtime upstream handshake timed out")
            return left

        try:
            sock = self._connect((parsed.hostname, port), timeout=remaining())
        except RealtimeError:
            raise
        except OSError:
            raise RealtimeError(502, "upstream_unavailable", "realtime upstream is unreachable") from None
        try:
            if parsed.scheme == "https":
                # ponytail: the TLS handshake is bounded per socket read, not by the total deadline.
                sock.settimeout(remaining())
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
            sock.settimeout(remaining())
            sock.sendall(request)
            head = bytearray()
            while b"\r\n\r\n" not in head:
                sock.settimeout(remaining())
                chunk = sock.recv(4096)
                if not chunk or len(head) + len(chunk) > _MAX_RESPONSE_HEAD_BYTES + 4096:
                    raise RealtimeError(502, "upstream_unavailable", "realtime upstream response head is invalid")
                head += chunk
            raw_head, _, leftover = bytes(head).partition(b"\r\n\r\n")
            status, response_headers = _parse_head(raw_head)
        except RealtimeError:
            sock.close()
            raise
        except socket.timeout:
            sock.close()
            raise RealtimeError(504, "upstream_timeout", "realtime upstream handshake timed out") from None
        except (OSError, ValueError):
            sock.close()
            raise RealtimeError(502, "upstream_unavailable", "realtime upstream handshake failed") from None
        accept = compute_accept_key(client_key)
        problem = None
        upgrade_tokens = [
            part.strip().lower()
            for value in response_headers.get("upgrade", [])
            for part in value.split(",")
            if part.strip()
        ]
        connection_tokens = [
            part.strip().lower()
            for value in response_headers.get("connection", [])
            for part in value.split(",")
            if part.strip()
        ]
        accept_values = response_headers.get("sec-websocket-accept", [])
        extension_values = response_headers.get("sec-websocket-extensions", [])
        protocol_values = response_headers.get("sec-websocket-protocol", [])
        chosen: Optional[str] = None
        if status != 101:
            problem = f"realtime upstream refused the upgrade (HTTP {status})"
        elif upgrade_tokens != ["websocket"] or "upgrade" not in connection_tokens:
            problem = "realtime upstream sent an invalid upgrade response"
        elif len(accept_values) != 1 or accept_values[0] != accept:
            problem = "realtime upstream sent a mismatched Sec-WebSocket-Accept"
        elif any(value.strip() for value in extension_values):
            problem = "realtime upstream negotiated an extension that was not offered"
        elif len(protocol_values) > 1:
            problem = "realtime upstream sent duplicate Sec-WebSocket-Protocol headers"
        elif protocol_values:
            chosen = protocol_values[0]
            if chosen not in protocols:
                problem = "realtime upstream selected a subprotocol that was not offered"
        if problem is not None:
            sock.close()
            raise RealtimeError(502, "upstream_rejected", problem)
        response = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept}\r\n"
                    + (f"Sec-WebSocket-Protocol: {chosen}\r\n" if chosen else "") + "\r\n").encode("latin-1")
        return UpstreamSession(sock=sock, leftover=leftover, response=response)

    def relay(self, client_sock: socket.socket, client_leftover: bytes, upstream: UpstreamSession,
              idle_timeout: Optional[float] = None) -> Tuple[int, int]:
        """Copy bytes both ways on one thread until EOF, error, or ``idle_timeout``."""
        idle = IDLE_TIMEOUT_SECONDS if idle_timeout is None else idle_timeout
        socks = (client_sock, upstream.sock)
        for sock in socks:
            sock.setblocking(False)
        # buf[0] holds bytes read from client_sock destined for upstream.sock;
        # buf[1] holds bytes read from upstream.sock destined for client_sock.
        buf = (bytearray(client_leftover), bytearray(upstream.leftover))
        counts = [0, 0]
        eof = [False, False]
        read_want = [0, 0]
        write_want = [0, 0]
        registered = [0, 0]
        last = self._monotonic()
        broken = False

        def do_read(index: int) -> None:
            nonlocal last, broken
            sock = socks[index]
            out = buf[index]
            while len(out) < _RELAY_BUFFER_LIMIT:
                space = min(_RELAY_CHUNK, _RELAY_BUFFER_LIMIT - len(out))
                try:
                    data = sock.recv(space)
                except ssl.SSLWantReadError:
                    read_want[index] = selectors.EVENT_READ
                    return
                except ssl.SSLWantWriteError:
                    read_want[index] = selectors.EVENT_WRITE
                    return
                except BlockingIOError:
                    read_want[index] = selectors.EVENT_READ
                    return
                except (OSError, ValueError):
                    broken = True
                    return
                if not data:
                    eof[index] = True
                    read_want[index] = 0
                    return
                out.extend(data)
                last = self._monotonic()
                read_want[index] = selectors.EVENT_READ
                if not (isinstance(sock, ssl.SSLSocket) and sock.pending() > 0):
                    return

        def do_write(index: int) -> None:
            nonlocal last, broken
            other = 1 - index
            pending_out = buf[other]
            if not pending_out:
                write_want[index] = 0
                return
            sock = socks[index]
            try:
                sent = sock.send(pending_out[:_RELAY_CHUNK])
            except ssl.SSLWantWriteError:
                write_want[index] = selectors.EVENT_WRITE
                return
            except ssl.SSLWantReadError:
                write_want[index] = selectors.EVENT_READ
                return
            except BlockingIOError:
                write_want[index] = selectors.EVENT_WRITE
                return
            except (OSError, ValueError):
                broken = True
                return
            if sent <= 0:
                broken = True
                return
            del pending_out[:sent]
            counts[other] += sent
            last = self._monotonic()
            write_want[index] = selectors.EVENT_WRITE if pending_out else 0

        try:
            with selectors.DefaultSelector() as selector:
                while True:
                    for index in (0, 1):
                        if eof[index] or len(buf[index]) >= _RELAY_BUFFER_LIMIT:
                            continue
                        sock = socks[index]
                        if isinstance(sock, ssl.SSLSocket) and sock.pending() > 0:
                            do_read(index)
                            if broken:
                                break
                    if broken or (eof[0] and not buf[0]) or (eof[1] and not buf[1]):
                        break

                    any_registered = False
                    for index in (0, 1):
                        other = 1 - index
                        can_read = (not eof[index]) and (len(buf[index]) < _RELAY_BUFFER_LIMIT)
                        can_write = bool(buf[other])
                        if not can_read:
                            read_want[index] = 0
                        elif read_want[index] == 0:
                            read_want[index] = selectors.EVENT_READ
                        if not can_write:
                            write_want[index] = 0
                        elif write_want[index] == 0:
                            write_want[index] = selectors.EVENT_WRITE
                        mask = read_want[index] | write_want[index]
                        if mask != registered[index]:
                            if registered[index]:
                                selector.unregister(socks[index])
                            if mask:
                                selector.register(socks[index], mask, index)
                            registered[index] = mask
                        if mask:
                            any_registered = True

                    if not any_registered:
                        break
                    remaining = idle - (self._monotonic() - last)
                    if remaining <= 0:
                        break
                    events = selector.select(timeout=remaining)
                    if not events:
                        if self._monotonic() - last >= idle:
                            break
                        continue
                    for key, mask in events:
                        index = key.data
                        other = 1 - index
                        if buf[other] and (mask & write_want[index]):
                            do_write(index)
                            if broken:
                                break
                        if (not eof[index]) and len(buf[index]) < _RELAY_BUFFER_LIMIT and (mask & read_want[index]):
                            do_read(index)
                            if broken:
                                break
                    if broken:
                        break
        except (OSError, ValueError):
            pass
        finally:
            _shutdown(client_sock)
            _shutdown(upstream.sock)
        return counts[0], counts[1]


def _parse_head(raw: bytes) -> Tuple[int, Dict[str, List[str]]]:
    lines = raw.decode("latin-1").split("\r\n")
    parts = lines[0].split(None, 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/1.") or not parts[1].isdigit():
        raise RealtimeError(502, "upstream_unavailable", "realtime upstream sent an invalid response")
    headers: Dict[str, List[str]] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers.setdefault(name.strip().lower(), []).append(value.strip())
    return int(parts[1]), headers


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # a readiness probe never follows redirects
        return None


def probe_realtime_health(url: str, timeout: float) -> int:
    # No proxy handler: a probe must reach the configured origin directly.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(urllib.request.Request(url, method="GET"), timeout=timeout) as response:
        return response.status


_probe_status = probe_realtime_health
