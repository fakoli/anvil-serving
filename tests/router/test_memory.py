"""Hermetic contract tests for the principal-bound Hindsight memory router."""

from __future__ import annotations

import json
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from typing import Any

import pytest

from anvil_serving.router.config import MemoryRoute
from anvil_serving.router.internal import BackendClientError
from anvil_serving.router.memory import MemoryError, MemoryRouter, tool_schemas


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


def test_recall_and_reflect_defaults_use_fixed_paths_and_bounds():
    transport = CaptureTransport()
    memory = router(transport)
    memory.dispatch(request("recall", {"query": "where?"}), principal="pi")
    memory.dispatch(request("reflect", {"query": "why?", "max_tokens": 2}), principal="pi")
    assert [call["url"] for call in transport.calls] == [
        "http://127.0.0.1:31100/v1/default/banks/pi-bank/memories/recall",
        "http://127.0.0.1:31100/v1/default/banks/pi-bank/reflect",
    ]
    assert [json.loads(call["data"]) for call in transport.calls] == [
        {"query": "where?", "budget": "mid", "max_tokens": 4096},
        {"query": "why?", "budget": "low", "max_tokens": 2},
    ]


@pytest.mark.parametrize("body,principal,status", [
    ({"alias": "memory.personal", "operation": "recall", "arguments": {"query": "x"}}, "", 401),
    ({"alias": "missing", "operation": "recall", "arguments": {"query": "x"}}, "pi", 404),
    ({"alias": "memory.personal", "operation": "recall", "arguments": {"query": "x"}}, "other", 403),
    ({"alias": "memory.personal", "operation": "retain", "arguments": {"content": "x", "bank": "other"}}, "pi", 422),
    ({"alias": "memory.personal", "operation": "retain", "arguments": {"content": "x", "async": True}}, "pi", 422),
    ({"alias": "memory.personal", "operation": "recall", "arguments": {"query": "x", "max_tokens": True}}, "pi", 422),
])
def test_rejects_isolation_and_override_escape_paths_without_transport(body, principal, status):
    transport = CaptureTransport()
    with pytest.raises(MemoryError) as error:
        router(transport).dispatch(body, principal=principal)
    assert error.value.status == status
    assert transport.calls == []


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


def test_upstream_failure_is_safe_and_concurrency_is_bounded():
    transport = CaptureTransport(error=RuntimeError("private upstream router-secret remember this"))
    memory = router(transport)
    with pytest.raises(MemoryError) as failure:
        memory.dispatch(request("recall", {"query": "remember this"}), principal="pi")
    assert failure.value.status == 503
    assert "private" not in failure.value.message
    assert "secret" not in failure.value.message
    assert memory._limit.acquire(blocking=False)
    assert memory._limit.acquire(blocking=False)
    assert memory._limit.acquire(blocking=False)
    assert memory._limit.acquire(blocking=False)
    try:
        with pytest.raises(MemoryError) as busy:
            memory.dispatch(request("recall", {"query": "x"}), principal="pi")
        assert busy.value.status == 503
        assert transport.calls == [transport.calls[0]]
    finally:
        for _ in range(4):
            memory._limit.release()


def test_aliases_and_tool_schemas_expose_only_safe_operations():
    assert router().aliases("pi") == ("memory.personal",)
    assert router().aliases("other") == ()
    schemas = tool_schemas(("memory.personal",))
    assert [schema["function"]["name"] for schema in schemas] == ["memory_retain", "memory_recall", "memory_reflect"]
    assert all(schema["function"]["parameters"]["additionalProperties"] is False for schema in schemas)
    assert schemas[0]["function"]["parameters"]["properties"]["alias"]["enum"] == ["memory.personal"]


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


def test_upstream_content_rejection_stays_rejected_without_echoing_content():
    transport = CaptureTransport(error=BackendClientError(422, "invalid_request", "sensitive upstream content"))
    with pytest.raises(MemoryError) as error:
        router(transport).dispatch(request("retain", {"content": "synthetic fixture"}), principal="pi")
    assert error.value.status == 422 and error.value.code == "memory_rejected"
    assert "sensitive" not in error.value.message


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
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
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
