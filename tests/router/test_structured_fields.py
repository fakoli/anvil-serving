"""Tests for structured-field passthrough — issues #42 (tool_use/tool_calls) and
#52 (inert verifiers).

Coverage:
  - RelayBackend._extract_structured: Anthropic + OpenAI wire shapes
  - RelayBackend.get_last_structured: populated after generate() drain
  - Dialect stop-reason mappers (_anthropic_stop_reason / _openai_finish_reason)
  - AnthropicDialect.stream() with get_structured → tool_use blocks + stop_reason
  - AnthropicDialect.render() with structured → tool_use blocks + stop_reason
  - OpenAIDialect.stream() with get_structured → tool_calls chunks + finish_reason
  - OpenAIDialect.render() with structured → tool_calls + finish_reason
  - Text-path regression: stream/render with get_structured=None / structured=None
    produces byte-identical output to pre-change defaults
  - NotTruncated and ToolCallJSONValid are live on the serve path
    (via RoutingBackend's _structured_view_factory injection into route_with_fallback)
  - front_door.py wires get_structured correctly for SSE + non-streaming paths
  - RoutingBackend.get_last_structured() is populated on the allow path
"""

from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager
from typing import Iterator, List, Optional


from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import Tier
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import InternalRequest, Message, StructuredResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_KEY = "sk-test-DEADBEEF-not-real"
ANTHROPIC_ENV = "ANVIL_TEST_STRUCT_ANTHROPIC"
OPENAI_ENV = "ANVIL_TEST_STRUCT_OPENAI"


def _mk_request(dialect: str = "anthropic") -> InternalRequest:
    return InternalRequest(
        model="test-model",
        messages=[Message("user", "call the tool")],
        max_tokens=100,
        dialect=dialect,
    )


def _mk_stream_req(include_usage: bool = True) -> InternalRequest:
    """Request that carries ``stream_options.include_usage`` on the wire."""
    return InternalRequest(
        model="test-model",
        messages=[Message("user", "call the tool")],
        max_tokens=100,
        dialect="openai",
        raw={"stream": True, "stream_options": {"include_usage": include_usage}},
    )


def _anthropic_tier() -> Tier:
    return Tier(
        id="cloud",
        base_url="https://api.anthropic.com",
        dialect="anthropic",
        context_limit=200000,
        privacy="local",
        tool_support=True,
        auth_env=ANTHROPIC_ENV,
    )


def _openai_tier() -> Tier:
    return Tier(
        id="cloud-oai",
        base_url="https://api.openai.com/v1",
        dialect="openai",
        context_limit=128000,
        privacy="local",
        tool_support=True,
        auth_env=OPENAI_ENV,
    )


class _CaptureTransport:
    def __init__(self, reply_body: bytes):
        self.reply_body = reply_body

    def __call__(self, url, *, data, headers, timeout):
        return self.reply_body


# ---------------------------------------------------------------------------
# RelayBackend._extract_structured: Anthropic wire shape
# ---------------------------------------------------------------------------

class TestRelayBackendExtractStructuredAnthropic:
    """Extract finish_reason and tool_calls from Anthropic-format response bodies."""

    def _make_backend(self, monkeypatch, reply: bytes) -> RelayBackend:
        monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
        return RelayBackend(_anthropic_tier(), transport=_CaptureTransport(reply))


# ---------------------------------------------------------------------------
# RelayBackend._extract_structured: OpenAI wire shape
# ---------------------------------------------------------------------------

class TestRelayBackendExtractStructuredOpenAI:
    """Extract finish_reason and tool_calls from OpenAI-format response bodies."""

    def _make_backend(self, monkeypatch, reply: bytes) -> RelayBackend:
        monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
        return RelayBackend(_openai_tier(), transport=_CaptureTransport(reply))


# ---------------------------------------------------------------------------
# RelayBackend thread safety: last_result is per-thread
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stop-reason mappers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AnthropicDialect.stream() — structured fields
# ---------------------------------------------------------------------------

def _parse_anthropic_sse(raw: bytes) -> List[tuple]:
    """Return ordered (event_type, data_dict) tuples."""
    events = []
    for block in raw.decode().split("\n\n"):
        lines = [ln for ln in block.split("\n") if ln]
        if not lines:
            continue
        etype = None
        data = None
        for ln in lines:
            if ln.startswith("event: "):
                etype = ln[len("event: "):]
            elif ln.startswith("data: "):
                data = json.loads(ln[len("data: "):])
        if etype and data is not None:
            events.append((etype, data))
    return events


class TestAnthropicDialectStream:
    def _stream(self, deltas, get_structured=None) -> bytes:
        d = AnthropicDialect()
        r = _mk_request("anthropic")
        return b"".join(d.stream(r, deltas, get_structured=get_structured))


# ---------------------------------------------------------------------------
# AnthropicDialect.render() — structured fields
# ---------------------------------------------------------------------------

class TestAnthropicDialectRender:
    def _render(self, text: str, structured=None) -> dict:
        d = AnthropicDialect()
        r = _mk_request("anthropic")
        return d.render(r, text, structured=structured)


# ---------------------------------------------------------------------------
# OpenAIDialect.stream() — structured fields
# ---------------------------------------------------------------------------

def _parse_openai_sse(raw: bytes) -> list:
    """Return list of chunk dicts (excluding [DONE])."""
    chunks = []
    for block in raw.decode().split("\n\n"):
        block = block.strip()
        if not block or not block.startswith("data: "):
            continue
        payload = block[len("data: "):]
        if payload == "[DONE]":
            continue
        chunks.append(json.loads(payload))
    return chunks


class TestOpenAIDialectStream:
    def _stream(self, deltas, get_structured=None) -> bytes:
        d = OpenAIDialect()
        r = _mk_request("openai")
        return b"".join(d.stream(r, deltas, get_structured=get_structured))


# ---------------------------------------------------------------------------
# OpenAIDialect.render() — structured fields
# ---------------------------------------------------------------------------

class TestOpenAIDialectRender:
    def _render(self, text: str, structured=None) -> dict:
        d = OpenAIDialect()
        r = _mk_request("openai")
        return d.render(r, text, structured=structured)


# ---------------------------------------------------------------------------
# StructuredResult propagation via a simple backend stub
# ---------------------------------------------------------------------------

class _StructuredBackend:
    """Stub backend that exposes get_last_structured() with a preset result."""

    def __init__(self, text: str, structured: Optional[StructuredResult]):
        self._text = text
        self._structured = structured

    def generate(self, request: InternalRequest) -> Iterator[str]:
        yield self._text

    def get_last_structured(self) -> Optional[StructuredResult]:
        return self._structured


class TestFrontDoorStructuredNonStreaming:
    """front_door reads get_last_structured() and passes structured= to render()."""

    @contextmanager
    def _server(self, backend):
        httpd = make_server("127.0.0.1", 0, backend)
        host, port = httpd.server_address[:2]
        t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        t.start()
        try:
            yield host, port
        finally:
            httpd.shutdown(); httpd.server_close(); t.join(timeout=5)

    def _post(self, host, port, path, body):
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            payload = json.dumps(body)
            conn.request("POST", path, payload, {"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        finally:
            conn.close()


class TestFrontDoorStructuredStreaming:
    """front_door passes get_structured to dialect.stream()."""

    @contextmanager
    def _server(self, backend):
        httpd = make_server("127.0.0.1", 0, backend)
        host, port = httpd.server_address[:2]
        t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        t.start()
        try:
            yield host, port
        finally:
            httpd.shutdown(); httpd.server_close(); t.join(timeout=5)

    def _post_stream(self, host, port, path, body):
        conn = http.client.HTTPConnection(host, port, timeout=10)
        try:
            payload = json.dumps(body)
            conn.request("POST", path, payload, {"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data
        finally:
            conn.close()

    def test_anthropic_stream_emits_tool_use_block(self):
        s = StructuredResult(
            finish_reason="tool_use",
            tool_calls=[{"id": "t1", "name": "read", "arguments": '{"f": "a.py"}'}],
        )
        backend = _StructuredBackend("", s)
        with self._server(backend) as (host, port):
            status, raw = self._post_stream(host, port, "/v1/messages", {
                "model": "m",
                "messages": [{"role": "user", "content": "go"}],
                "max_tokens": 100,
                "stream": True,
            })
        assert status == 200
        events = _parse_anthropic_sse(raw)
        block_starts = [e for e in events if e[0] == "content_block_start"]
        tool_block = next(
            (b for b in block_starts if b[1]["content_block"].get("type") == "tool_use"),
            None
        )
        assert tool_block is not None
        assert tool_block[1]["content_block"]["name"] == "read"
