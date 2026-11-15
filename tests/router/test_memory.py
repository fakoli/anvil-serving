"""Hermetic contract tests for the principal-bound Hindsight memory router."""

from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from typing import Any

import pytest

from anvil_serving.router.config import MemoryRoute
from anvil_serving.router.memory import MemoryError, MemoryRouter


ROUTE = MemoryRoute(
    alias="memory.personal", principal="pi", backend="hindsight", bank="pi-bank",
    base_url="http://127.0.0.1:31100", auth_env="HINDSIGHT_TOKEN", timeout=9.0,
)
HERMES = MemoryRoute(
    alias="memory.hermes", principal="pi", backend="hermes", bank="ignored",
    base_url="http://127.0.0.1:31101", auth_env="HINDSIGHT_TOKEN", timeout=9.0,
)


class CaptureTransport:
    def __init__(self, reply: bytes = b'{"ok":true}', error: Exception | None = None):
        self.reply, self.error = reply, error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, *, data, headers, timeout, max_bytes=None):
        self.calls.append({"url": url, "data": data, "headers": dict(headers), "timeout": timeout, "max_bytes": max_bytes})
        if self.error:
            raise self.error
        return self.reply


def router(transport: CaptureTransport | None = None, *, env=None, routes=(ROUTE,)):
    return MemoryRouter(routes, env={"HINDSIGHT_TOKEN": "router-secret"} if env is None else env,
                        transport=transport or CaptureTransport())


def request(operation: str, arguments: dict) -> dict:
    return {"alias": " MEMORY.PERSONAL ", "operation": operation, "arguments": arguments}


def test_retain_is_sync_and_principal_bound_with_literal_pinned_payload():
    transport = CaptureTransport()
    result = router(transport).dispatch(request("retain", {"content": "remember this", "context": "session", "tags": ["pi"]}), principal="pi")
    assert result == {"alias": "memory.personal", "backend": "hindsight", "operation": "retain", "result": {"ok": True}}
    assert transport.calls == [{
        "url": "http://127.0.0.1:31100/v1/default/banks/pi-bank/memories",
        "data": b'{"items":[{"content":"remember this","context":"session","tags":["pi"]}],"async":false}',
        "headers": {"Content-Type": "application/json", "Authorization": "Bearer router-secret"},
        "timeout": 9.0, "max_bytes": 2 * 1024 * 1024,
    }]


def test_selected_hermes_or_missing_token_never_falls_through():
    transport = CaptureTransport()
    with pytest.raises(MemoryError) as hermes:
        router(transport, routes=(HERMES,)).dispatch(
            {"alias": "memory.hermes", "operation": "recall", "arguments": {"query": "x"}}, principal="pi")
    assert hermes.value.status == 501
    with pytest.raises(MemoryError) as unavailable:
        router(transport, env={}).dispatch(request("recall", {"query": "x"}), principal="pi")
    assert unavailable.value.status == 503
    assert transport.calls == []


def test_malformed_operations_and_credentials_never_reach_transport():
    transport = CaptureTransport()
    for operation in ([], {}, None, True):
        with pytest.raises(MemoryError) as error:
            router(transport).dispatch(request(operation, {}), principal="pi")
        assert error.value.status == 422
    for token in ("", "bad\r\nheader", "bad token", "nonascii-\u00e9", 123):
        with pytest.raises(MemoryError) as error:
            router(transport, env={"HINDSIGHT_TOKEN": token}).dispatch(request("recall", {"query": "x"}), principal="pi")
        assert error.value.status == 503
    assert transport.calls == []


@pytest.mark.parametrize("drip_headers", [False, True])
def test_total_deadline_interrupts_byte_drip_and_releases_permit(drip_headers):
    class Drip(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = b'{"ok":true}'
            header = b'HTTP/1.0 200 OK\r\nContent-Length: 11\r\n\r\n'
            try:
                if not drip_headers:
                    self.connection.sendall(header)
                for byte in header if drip_headers else body:
                    self.connection.sendall(bytes([byte]))
                    time.sleep(0.15)
            except OSError:
                pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Drip)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    memory = MemoryRouter((replace(ROUTE, base_url=f"http://127.0.0.1:{httpd.server_port}", timeout=0.4),),
                          env={"HINDSIGHT_TOKEN": "synthetic-token"})
    started = time.monotonic()
    try:
        with pytest.raises(MemoryError) as error:
            memory.dispatch(request("recall", {"query": "x"}), principal="pi")
        assert error.value.status == 504
        assert time.monotonic() - started < 1.3
        assert all(memory._limit.acquire(blocking=False) for _ in range(4))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
