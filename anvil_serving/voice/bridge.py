"""Small stdlib TCP bridge for voice STT/TTS endpoints.

This module intentionally lives under ``anvil_serving.voice`` so split-host
audio exposure is an Anvil Serving operation, not an operator-owned ad hoc
script. It is transport-only: it forwards TCP bytes and does not inspect,
authenticate, or transform OpenAI-compatible HTTP traffic.
"""
from __future__ import annotations

from dataclasses import dataclass
import socket
import threading
import time
from collections import deque
from typing import Callable, Iterable, Optional


MINI_OWNER = "mini"
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0
DEFAULT_LOG_BYTES = 64 * 1024


@dataclass(frozen=True)
class TCPBridgeRoute:
    name: str
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int


@dataclass(frozen=True)
class BridgeState:
    """Typed, side-effect-free snapshot of a Mini-owned forwarding bridge."""

    owner: str
    running: bool
    routes: tuple[TCPBridgeRoute, ...]
    started_at: Optional[float]
    stopping: bool = False


@dataclass(frozen=True)
class BridgeLogs:
    """A bounded log snapshot; output is never streamed by this API."""

    lines: tuple[str, ...]
    max_bytes: int
    truncated: bool


def describe_route(route: TCPBridgeRoute) -> str:
    return "%s %s:%d -> %s:%d" % (
        route.name,
        route.listen_host,
        route.listen_port,
        route.target_host,
        route.target_port,
    )


def serve_forever(
    routes: Iterable[TCPBridgeRoute],
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    """Bind all routes and forward connections until interrupted."""
    serve_until_stopped(routes, threading.Event(), log=log)


def serve_until_stopped(
    routes: Iterable[TCPBridgeRoute],
    stop_event: threading.Event,
    *,
    log: Callable[[str], None] | None = None,
) -> None:
    """Bind all routes and forward connections until `stop_event` is set."""
    route_list = list(routes)
    if not route_list:
        raise ValueError("at least one bridge route is required")

    servers: list[tuple[socket.socket, TCPBridgeRoute]] = []
    try:
        for route in route_list:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((route.listen_host, route.listen_port))
            server.settimeout(0.2)
            server.listen(128)
            servers.append((server, route))
            if log:
                log("listening %s" % describe_route(route))

        for server, route in servers:
            thread = threading.Thread(
                target=_accept_loop,
                args=(server, route, stop_event, log),
                daemon=True,
            )
            thread.start()

        while not stop_event.wait(0.2):
            pass
    finally:
        for server, _route in servers:
            try:
                server.close()
            except OSError:
                pass


def _accept_loop(
    server: socket.socket,
    route: TCPBridgeRoute,
    stop_event: threading.Event,
    log: Callable[[str], None] | None,
) -> None:
    while not stop_event.is_set():
        try:
            client, _addr = server.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        thread = threading.Thread(
            target=_handle_client,
            args=(client, route, log),
            daemon=True,
        )
        thread.start()


def _handle_client(
    client: socket.socket,
    route: TCPBridgeRoute,
    log: Callable[[str], None] | None,
) -> None:
    upstream = None
    try:
        upstream = socket.create_connection((route.target_host, route.target_port), timeout=10.0)
        left = threading.Thread(target=_pipe, args=(client, upstream), daemon=True)
        right = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
        left.start()
        right.start()
        left.join()
        right.join()
    except OSError as exc:
        if log:
            log("%s connection failed: %s" % (route.name, exc))
    finally:
        _close_socket(client)
        if upstream is not None:
            _close_socket(upstream)


def _pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _close_socket(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


class ForwardingBridgeService:
    """Own the lifecycle of transport-only Mini forwarding listeners.

    This is deliberately an in-process service wrapper around
    :func:`serve_until_stopped`: it only opens the declared TCP listeners and
    forwards bytes to their upstreams.  It neither imports nor starts audio
    model serves.  The bounded retained log is an operator snapshot, not a
    follow stream.
    """

    def __init__(
        self,
        routes: Iterable[TCPBridgeRoute],
        *,
        owner: str = MINI_OWNER,
        max_log_bytes: int = DEFAULT_LOG_BYTES,
        serve: Callable[..., None] = serve_until_stopped,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if owner != MINI_OWNER:
            raise ValueError("forwarding bridge owner must be %r" % MINI_OWNER)
        if max_log_bytes < 1:
            raise ValueError("max_log_bytes must be positive")
        self._routes = tuple(routes)
        if not self._routes:
            raise ValueError("at least one bridge route is required")
        self._owner = owner
        self._max_log_bytes = max_log_bytes
        self._serve = serve
        self._clock = clock
        self._lock = threading.Lock()
        self._stop_event: Optional[threading.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._started_at: Optional[float] = None
        self._logs: deque[str] = deque()
        self._log_bytes = 0
        self._truncated = False

    def _append_log(self, message: str) -> None:
        line = str(message)
        size = len(line.encode("utf-8", errors="replace"))
        with self._lock:
            while self._logs and self._log_bytes + size > self._max_log_bytes:
                removed = self._logs.popleft()
                self._log_bytes -= len(removed.encode("utf-8", errors="replace"))
                self._truncated = True
            if size > self._max_log_bytes:
                encoded = line.encode("utf-8", errors="replace")[-self._max_log_bytes :]
                line = encoded.decode("utf-8", errors="replace")
                size = len(encoded)
                self._logs.clear()
                self._log_bytes = 0
                self._truncated = True
            self._logs.append(line)
            self._log_bytes += size

    def _state_locked(self) -> BridgeState:
        alive = self._thread is not None and self._thread.is_alive()
        stopping = alive and self._stop_event is not None and self._stop_event.is_set()
        return BridgeState(self._owner, alive, self._routes, self._started_at, stopping)

    def run(self) -> BridgeState:
        """Run in the calling thread until :meth:`stop` is requested."""
        with self._lock:
            if self._stop_event is not None and not self._stop_event.is_set():
                raise RuntimeError("forwarding bridge is already running")
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._started_at = self._clock()
        try:
            self._serve(self._routes, stop_event, log=self._append_log)
        finally:
            with self._lock:
                self._started_at = None
        return self.status()

    def start(self) -> BridgeState:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._state_locked()
            thread = threading.Thread(target=self.run, name="anvil-voice-bridge", daemon=True)
            self._thread = thread
            thread.start()
            return self._state_locked()

    def stop(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> BridgeState:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._lock:
            event, thread = self._stop_event, self._thread
            if event is not None:
                event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        return self.status()

    def restart(self, *, timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS) -> BridgeState:
        self.stop(timeout=timeout)
        return self.start()

    def status(self) -> BridgeState:
        with self._lock:
            return self._state_locked()

    def logs(self) -> BridgeLogs:
        with self._lock:
            return BridgeLogs(tuple(self._logs), self._max_log_bytes, self._truncated)
