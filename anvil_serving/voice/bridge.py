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
from typing import Callable, Iterable


@dataclass(frozen=True)
class TCPBridgeRoute:
    name: str
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int


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
