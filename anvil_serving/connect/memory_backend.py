"""Private Hindsight origin and bounded direct transport for account creation."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import threading
from urllib.parse import urlsplit


def origin(value):
    if type(value) is not str or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("memory origin is invalid")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
            or parsed.port is not None and not 1 <= parsed.port <= 65535):
        raise ValueError("memory origin must be a credential-free HTTP origin")
    if parsed.hostname != "host.docker.internal":
        if parsed.hostname == "localhost":
            raise ValueError("memory origin must use 127.0.0.1 or host.docker.internal, never localhost")
        address = ipaddress.ip_address(parsed.hostname)
        networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
        if str(address) != "127.0.0.1" and not any(address in ipaddress.ip_network(net) for net in networks):
            raise ValueError("memory origin must be private")
    return value


def request(url, *, data, headers, timeout, max_bytes, method):
    parsed = urlsplit(url)
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    held_socket = [None]
    expired = threading.Event()
    def cancel():
        expired.set()
        active = connection.sock or held_socket[0]
        if active is not None:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
    timer = threading.Timer(timeout, cancel)
    timer.daemon = True
    timer.start()
    response = None
    try:
        connection.connect()
        held_socket[0] = connection.sock
        if expired.is_set():
            raise TimeoutError("memory request deadline")
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection.request(method, target, body=data, headers=headers)
        response = connection.getresponse()
        raw = response.read(max_bytes + 1)
        if expired.is_set():
            raise TimeoutError("memory request deadline")
        if not 200 <= response.status < 300 or len(raw) > max_bytes:
            raise ValueError("memory response is unavailable")
        return raw
    finally:
        timer.cancel()
        if response is not None:
            response.close()
        connection.close()
