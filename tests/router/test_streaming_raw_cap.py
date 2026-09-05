"""Raw upstream response caps for streaming relays."""
from __future__ import annotations

import http.client
import io
import json
import threading
from contextlib import contextmanager

import pytest

from anvil_serving.router.backends.relay import RelayBackend, RelayBackendError
from anvil_serving.router.config import Tier
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import InternalRequest, Message


class StreamResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_type: str = "text/event-stream"):
        super().__init__(payload)
        self.headers = {"Content-Type": content_type}
        self.readline_sizes: list[int] = []
        self.read_sizes: list[int] = []

    def readline(self, size=-1):
        self.readline_sizes.append(size)
        return super().readline(size)

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class StreamTransport:
    def __init__(self, payload: bytes, content_type: str = "text/event-stream"):
        self.response = StreamResponse(payload, content_type)

    def __call__(self, url, *, data, headers, timeout):
        return self.response


def _tier(dialect: str) -> Tier:
    return Tier(
        id=dialect + "-local",
        base_url="http://127.0.0.1:30000",
        dialect=dialect,
        context_limit=4096,
        privacy="local",
        tool_support=True,
        auth_env="TEST_UPSTREAM_KEY",
        model="served-model",
    )


def _request(dialect: str) -> InternalRequest:
    return InternalRequest(
        model="llm.primary",
        messages=[Message("user", "hi")],
        max_tokens=8,
        stream=True,
        dialect=dialect,
    )


def _tool_only_payload(dialect: str) -> bytes:
    arguments = "x" * 100_000
    if dialect == "anthropic":
        event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": arguments},
        }
        return (
            b"event: content_block_delta\n"
            + b"data: "
            + json.dumps(event).encode()
            + b"\n\n"
        )
    event = {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": arguments},
                        }
                    ]
                },
            }
        ]
    }
    return b"data: " + json.dumps(event).encode() + b"\n\n"


@pytest.mark.parametrize("dialect", ["openai", "anthropic"])
def test_tool_only_sse_is_capped_before_argument_accumulation(dialect):
    transport = StreamTransport(_tool_only_payload(dialect))
    backend = RelayBackend(
        _tier(dialect),
        env={},
        stream_transport=transport,
        max_response_bytes=128,
    )

    with pytest.raises(RelayBackendError, match="max_response_bytes=128"):
        list(backend.generate(_request(dialect)))

    assert transport.response.closed
    assert backend.get_last_structured() is None
    assert max(transport.response.readline_sizes) <= 129


def test_cap_counts_comments_malformed_events_usage_and_framing_cumulatively():
    payload = (
        b": keepalive\n\n"
        b"data: not-json\n\n"
        b'data: {"choices":[],"usage":{"prompt_tokens":1}}\n\n'
    )
    transport = StreamTransport(payload)
    backend = RelayBackend(
        _tier("openai"),
        env={},
        stream_transport=transport,
        max_response_bytes=32,
    )

    with pytest.raises(RelayBackendError, match="max_response_bytes=32"):
        list(backend.generate(_request("openai")))

    assert transport.response.closed
    assert len(transport.response.readline_sizes) >= 3
    assert transport.response.readline_sizes[0] == 33
    assert all(size <= 33 for size in transport.response.readline_sizes)


def test_streaming_transport_caps_plain_json_read_before_parsing():
    transport = StreamTransport(b"x" * 10_000, "application/json")
    backend = RelayBackend(
        _tier("openai"),
        env={},
        stream_transport=transport,
        max_response_bytes=64,
    )

    with pytest.raises(RelayBackendError, match="max_response_bytes=64"):
        list(backend.generate(_request("openai")))

    assert transport.response.read_sizes == [65]
    assert transport.response.closed


@contextmanager
def _server(backend):
    server = make_server(
        "127.0.0.1", 0, backend, model_routes=("llm.primary",)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[:2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_front_door_emits_one_terminal_failure_and_closes_capped_upstream():
    transport = StreamTransport(_tool_only_payload("openai"))
    backend = RelayBackend(
        _tier("openai"),
        env={},
        stream_transport=transport,
        max_response_bytes=128,
    )
    with _server(backend) as (host, port):
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request(
                "POST",
                "/v1/chat/completions",
                json.dumps(
                    {
                        "model": "llm.primary",
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                    }
                ),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read().decode()
        finally:
            connection.close()

    assert response.status == 200
    assert body.count('"type":"upstream_error"') == 1
    assert "[DONE]" not in body
    assert transport.response.closed


@pytest.mark.parametrize("dialect", ["openai", "anthropic"])
def test_provider_error_after_partial_content_becomes_one_sanitized_terminal_failure(
    dialect, capsys
):
    if dialect == "anthropic":
        payload = (
            b'event: content_block_delta\ndata: {"type":"content_block_delta",'
            b'"index":0,"delta":{"type":"text_delta","text":"partial"}}\n\n'
            b'event: error\ndata: {"type":"error","error":{"message":'
            b'"private provider failure"}}\n\n'
        )
        path = "/v1/messages"
        request_body = {
            "model": "llm.primary",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        terminal_marker = "event: error"
        completion_marker = "message_stop"
    else:
        payload = (
            b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
            b'data: {"error":{"message":"private provider failure"}}\n\n'
        )
        path = "/v1/chat/completions"
        request_body = {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        terminal_marker = '"type":"upstream_error"'
        completion_marker = "[DONE]"

    transport = StreamTransport(payload)
    backend = RelayBackend(
        _tier(dialect), env={}, stream_transport=transport, max_response_bytes=4096
    )
    with _server(backend) as (host, port):
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request(
                "POST",
                path,
                json.dumps(request_body),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read().decode()
        finally:
            connection.close()

    logged = capsys.readouterr().err
    assert response.status == 200
    assert "partial" in body
    assert body.count(terminal_marker) == 1
    assert completion_marker not in body
    assert "private provider failure" not in body
    assert "private provider failure" not in logged
    assert transport.response.closed


@pytest.mark.parametrize("dialect", ["openai", "anthropic"])
def test_clean_eof_after_partial_content_is_one_terminal_failure(dialect, capsys):
    if dialect == "anthropic":
        payload = (
            b'event: content_block_delta\ndata: {"type":"content_block_delta",'
            b'"index":0,"delta":{"type":"text_delta","text":"partial"}}\n\n'
        )
        path = "/v1/messages"
        request_body = {
            "model": "llm.primary",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        terminal_marker = "event: error"
        completion_marker = "message_stop"
    else:
        payload = (
            b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
        )
        path = "/v1/chat/completions"
        request_body = {
            "model": "llm.primary",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }
        terminal_marker = '"type":"upstream_error"'
        completion_marker = "[DONE]"

    transport = StreamTransport(payload)
    backend = RelayBackend(
        _tier(dialect), env={}, stream_transport=transport, max_response_bytes=4096
    )
    with _server(backend) as (host, port):
        connection = http.client.HTTPConnection(host, port, timeout=5)
        try:
            connection.request(
                "POST",
                path,
                json.dumps(request_body),
                {"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = response.read().decode()
        finally:
            connection.close()

    logged = capsys.readouterr().err
    assert response.status == 200
    assert "partial" in body
    assert body.count(terminal_marker) == 1
    assert completion_marker not in body
    assert "RelayBackendError" in logged
    assert transport.response.closed
