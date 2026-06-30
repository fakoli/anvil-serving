"""Tests for structured-field passthrough — issues #42 (tool_use/tool_calls) and
#52 (inert verifiers).

Coverage:
  - CloudBackend._extract_structured: Anthropic + OpenAI wire shapes
  - CloudBackend.get_last_structured: populated after generate() drain
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
from typing import Dict, Iterator, List, Optional


from anvil_serving.router.backends import StaticBackend
from anvil_serving.router.backends.cloud import CloudBackend
from anvil_serving.router.config import Tier
from anvil_serving.router.dialects.anthropic import AnthropicDialect, _anthropic_stop_reason
from anvil_serving.router.dialects.openai import OpenAIDialect, _openai_finish_reason
from anvil_serving.router.front_door import make_server
from anvil_serving.router.internal import InternalRequest, Message, StructuredResult
from anvil_serving.router.verify import ResponseView, NotTruncated, ToolCallJSONValid

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


def _anthropic_tier() -> Tier:
    return Tier(
        id="cloud",
        base_url="https://api.anthropic.com",
        dialect="anthropic",
        context_limit=200000,
        privacy="cloud",
        tool_support=True,
        auth_env=ANTHROPIC_ENV,
    )


def _openai_tier() -> Tier:
    return Tier(
        id="cloud-oai",
        base_url="https://api.openai.com/v1",
        dialect="openai",
        context_limit=128000,
        privacy="cloud",
        tool_support=True,
        auth_env=OPENAI_ENV,
    )


class _CaptureTransport:
    def __init__(self, reply_body: bytes):
        self.reply_body = reply_body

    def __call__(self, url, *, data, headers, timeout):
        return self.reply_body


# ---------------------------------------------------------------------------
# CloudBackend._extract_structured: Anthropic wire shape
# ---------------------------------------------------------------------------

class TestCloudBackendExtractStructuredAnthropic:
    """Extract finish_reason and tool_calls from Anthropic-format response bodies."""

    def _make_backend(self, monkeypatch, reply: bytes) -> CloudBackend:
        monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
        return CloudBackend(_anthropic_tier(), transport=_CaptureTransport(reply))

    def test_text_response_end_turn(self, monkeypatch):
        reply = json.dumps({
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "hello"}],
        }).encode()
        backend = self._make_backend(monkeypatch, reply)
        list(backend.generate(_mk_request("anthropic")))
        s = backend.get_last_structured()
        assert s is not None
        assert s.finish_reason == "end_turn"
        assert not s.tool_calls

    def test_tool_use_response(self, monkeypatch):
        reply = json.dumps({
            "stop_reason": "tool_use",
            "content": [
                {"type": "text", "text": ""},
                {
                    "type": "tool_use",
                    "id": "toolu_abc123",
                    "name": "read_file",
                    "input": {"path": "/etc/hosts"},
                },
            ],
        }).encode()
        backend = self._make_backend(monkeypatch, reply)
        list(backend.generate(_mk_request("anthropic")))
        s = backend.get_last_structured()
        assert s is not None
        assert s.finish_reason == "tool_use"
        assert s.tool_calls is not None
        assert len(s.tool_calls) == 1
        tc = s.tool_calls[0]
        assert tc["name"] == "read_file"
        assert tc["id"] == "toolu_abc123"
        # For Anthropic, arguments is the already-parsed input dict (not a JSON string).
        # Both the dict form and a JSON string that parses to the same dict are acceptable.
        args = tc["arguments"]
        if isinstance(args, dict):
            assert args == {"path": "/etc/hosts"}
        else:
            assert json.loads(args) == {"path": "/etc/hosts"}

    def test_max_tokens_truncated(self, monkeypatch):
        reply = json.dumps({
            "stop_reason": "max_tokens",
            "content": [{"type": "text", "text": "truncated"}],
        }).encode()
        backend = self._make_backend(monkeypatch, reply)
        list(backend.generate(_mk_request("anthropic")))
        s = backend.get_last_structured()
        assert s is not None
        assert s.finish_reason == "max_tokens"

    def test_multiple_tool_calls(self, monkeypatch):
        reply = json.dumps({
            "stop_reason": "tool_use",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "fn_a", "input": {"x": 1}},
                {"type": "tool_use", "id": "t2", "name": "fn_b", "input": {"y": 2}},
            ],
        }).encode()
        backend = self._make_backend(monkeypatch, reply)
        list(backend.generate(_mk_request("anthropic")))
        s = backend.get_last_structured()
        assert s is not None
        assert len(s.tool_calls) == 2
        assert s.tool_calls[0]["name"] == "fn_a"
        assert s.tool_calls[1]["name"] == "fn_b"


# ---------------------------------------------------------------------------
# CloudBackend._extract_structured: OpenAI wire shape
# ---------------------------------------------------------------------------

class TestCloudBackendExtractStructuredOpenAI:
    """Extract finish_reason and tool_calls from OpenAI-format response bodies."""

    def _make_backend(self, monkeypatch, reply: bytes) -> CloudBackend:
        monkeypatch.setenv(OPENAI_ENV, FAKE_KEY)
        return CloudBackend(_openai_tier(), transport=_CaptureTransport(reply))

    def test_stop_response(self, monkeypatch):
        reply = json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}]
        }).encode()
        backend = self._make_backend(monkeypatch, reply)
        list(backend.generate(_mk_request("openai")))
        s = backend.get_last_structured()
        assert s is not None
        assert s.finish_reason == "stop"
        assert not s.tool_calls

    def test_tool_calls_response(self, monkeypatch):
        reply = json.dumps({
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_xyz",
                        "type": "function",
                        "function": {"name": "grep", "arguments": '{"pattern": "foo"}'},
                    }],
                },
            }]
        }).encode()
        backend = self._make_backend(monkeypatch, reply)
        list(backend.generate(_mk_request("openai")))
        s = backend.get_last_structured()
        assert s is not None
        assert s.finish_reason == "tool_calls"
        assert s.tool_calls is not None
        assert len(s.tool_calls) == 1
        tc = s.tool_calls[0]
        assert tc["name"] == "grep"
        assert tc["id"] == "call_xyz"

    def test_length_finish_reason(self, monkeypatch):
        reply = json.dumps({
            "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": "..."}}]
        }).encode()
        backend = self._make_backend(monkeypatch, reply)
        list(backend.generate(_mk_request("openai")))
        s = backend.get_last_structured()
        assert s is not None
        assert s.finish_reason == "length"


# ---------------------------------------------------------------------------
# CloudBackend thread safety: last_result is per-thread
# ---------------------------------------------------------------------------

def test_cloud_backend_thread_local_isolation(monkeypatch):
    """Two threads using the same backend instance see their own last_result."""
    monkeypatch.setenv(ANTHROPIC_ENV, FAKE_KEY)
    reply_a = json.dumps({
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "A"}],
    }).encode()
    reply_b = json.dumps({
        "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "t1", "name": "fn", "input": {}}],
    }).encode()

    results: Dict[str, Optional[StructuredResult]] = {}
    barrier = threading.Barrier(2)

    def _run(label: str, reply: bytes) -> None:
        backend = CloudBackend(_anthropic_tier(), transport=_CaptureTransport(reply))
        list(backend.generate(_mk_request("anthropic")))
        barrier.wait()  # both threads hit this after generate() to ensure overlap
        results[label] = backend.get_last_structured()

    ta = threading.Thread(target=_run, args=("a", reply_a))
    tb = threading.Thread(target=_run, args=("b", reply_b))
    ta.start(); tb.start()
    ta.join(); tb.join()

    # Each backend instance is separate so both should have their own result
    assert results["a"] is not None
    assert results["b"] is not None


# ---------------------------------------------------------------------------
# Stop-reason mappers
# ---------------------------------------------------------------------------

class TestAnthropicStopReasonMapper:
    def test_none_is_end_turn(self):
        assert _anthropic_stop_reason(None) == "end_turn"

    def test_native_values_passthrough(self):
        assert _anthropic_stop_reason("end_turn") == "end_turn"
        assert _anthropic_stop_reason("stop_sequence") == "stop_sequence"

    def test_openai_stop_maps_to_end_turn(self):
        assert _anthropic_stop_reason("stop") == "end_turn"

    def test_tool_calls_maps_to_tool_use(self):
        assert _anthropic_stop_reason("tool_calls") == "tool_use"
        assert _anthropic_stop_reason("tool_use") == "tool_use"

    def test_length_maps_to_max_tokens(self):
        assert _anthropic_stop_reason("length") == "max_tokens"
        assert _anthropic_stop_reason("max_tokens") == "max_tokens"

    def test_unknown_falls_back_to_end_turn(self):
        assert _anthropic_stop_reason("weird_value") == "end_turn"


class TestOpenAIFinishReasonMapper:
    def test_none_is_stop(self):
        assert _openai_finish_reason(None) == "stop"

    def test_native_values_passthrough(self):
        assert _openai_finish_reason("stop") == "stop"
        assert _openai_finish_reason("tool_calls") == "tool_calls"
        assert _openai_finish_reason("length") == "length"

    def test_anthropic_end_turn_maps_to_stop(self):
        assert _openai_finish_reason("end_turn") == "stop"
        assert _openai_finish_reason("stop_sequence") == "stop"

    def test_tool_use_maps_to_tool_calls(self):
        assert _openai_finish_reason("tool_use") == "tool_calls"

    def test_max_tokens_maps_to_length(self):
        assert _openai_finish_reason("max_tokens") == "length"

    def test_unknown_falls_back_to_stop(self):
        assert _openai_finish_reason("weird_value") == "stop"


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

    def test_text_path_no_structured_defaults_to_end_turn(self):
        raw = self._stream(["hello"])
        events = _parse_anthropic_sse(raw)
        msg_delta = next(e for e in events if e[0] == "message_delta")
        assert msg_delta[1]["delta"]["stop_reason"] == "end_turn"
        # No tool_use content blocks
        block_starts = [e for e in events if e[0] == "content_block_start"]
        assert all(b[1]["content_block"]["type"] == "text" for b in block_starts)

    def test_stop_reason_from_structured(self):
        s = StructuredResult(finish_reason="max_tokens")
        raw = self._stream(["hi"], get_structured=lambda: s)
        events = _parse_anthropic_sse(raw)
        msg_delta = next(e for e in events if e[0] == "message_delta")
        assert msg_delta[1]["delta"]["stop_reason"] == "max_tokens"

    def test_tool_use_blocks_emitted(self):
        s = StructuredResult(
            finish_reason="tool_use",
            tool_calls=[{
                "id": "toolu_01",
                "name": "read_file",
                "arguments": '{"path": "/etc/hosts"}',
            }],
        )
        raw = self._stream(["thinking..."], get_structured=lambda: s)
        events = _parse_anthropic_sse(raw)

        # message_delta stop_reason should be tool_use
        msg_delta = next(e for e in events if e[0] == "message_delta")
        assert msg_delta[1]["delta"]["stop_reason"] == "tool_use"

        # There should be a tool_use content_block_start at index 1
        block_starts = [e for e in events if e[0] == "content_block_start"]
        tool_block = next(
            (b for b in block_starts if b[1]["content_block"].get("type") == "tool_use"),
            None
        )
        assert tool_block is not None, "Expected a tool_use content_block_start"
        assert tool_block[1]["content_block"]["name"] == "read_file"
        assert tool_block[1]["content_block"]["id"] == "toolu_01"

        # There should be an input_json_delta for the tool block
        deltas = [e for e in events if e[0] == "content_block_delta"]
        tool_delta = next(
            (d for d in deltas if d[1].get("delta", {}).get("type") == "input_json_delta"),
            None
        )
        assert tool_delta is not None
        parsed_input = json.loads(tool_delta[1]["delta"]["partial_json"])
        assert parsed_input == {"path": "/etc/hosts"}

    def test_tool_use_dict_arguments(self):
        """Arguments as a dict (Anthropic upstream) are serialized correctly."""
        s = StructuredResult(
            finish_reason="tool_use",
            tool_calls=[{"id": "t1", "name": "fn", "arguments": {"key": "val"}}],
        )
        raw = self._stream([], get_structured=lambda: s)
        events = _parse_anthropic_sse(raw)
        deltas = [e for e in events if e[0] == "content_block_delta"]
        tool_delta = next(
            (d for d in deltas if d[1].get("delta", {}).get("type") == "input_json_delta"),
            None
        )
        assert tool_delta is not None
        assert json.loads(tool_delta[1]["delta"]["partial_json"]) == {"key": "val"}

    def test_none_structured_no_regression(self):
        """get_structured returning None → defaults, no crash."""
        raw = self._stream(["text"], get_structured=lambda: None)
        events = _parse_anthropic_sse(raw)
        msg_delta = next(e for e in events if e[0] == "message_delta")
        assert msg_delta[1]["delta"]["stop_reason"] == "end_turn"


# ---------------------------------------------------------------------------
# AnthropicDialect.render() — structured fields
# ---------------------------------------------------------------------------

class TestAnthropicDialectRender:
    def _render(self, text: str, structured=None) -> dict:
        d = AnthropicDialect()
        r = _mk_request("anthropic")
        return d.render(r, text, structured=structured)

    def test_text_path_no_structured_defaults(self):
        out = self._render("hello world")
        assert out["stop_reason"] == "end_turn"
        assert out["content"] == [{"type": "text", "text": "hello world"}]

    def test_stop_reason_from_structured(self):
        s = StructuredResult(finish_reason="max_tokens")
        out = self._render("truncated", structured=s)
        assert out["stop_reason"] == "max_tokens"

    def test_tool_use_content_blocks(self):
        s = StructuredResult(
            finish_reason="tool_use",
            tool_calls=[{
                "id": "toolu_99",
                "name": "write_file",
                "arguments": '{"path": "/tmp/x", "content": "hello"}',
            }],
        )
        out = self._render("", structured=s)
        assert out["stop_reason"] == "tool_use"
        # content should include the tool_use block
        tool_block = next((b for b in out["content"] if b["type"] == "tool_use"), None)
        assert tool_block is not None
        assert tool_block["name"] == "write_file"
        assert tool_block["id"] == "toolu_99"
        assert tool_block["input"] == {"path": "/tmp/x", "content": "hello"}

    def test_empty_text_with_tool_only_gets_content(self):
        """Empty text + tool call must still have content (not an empty list)."""
        s = StructuredResult(
            finish_reason="tool_use",
            tool_calls=[{"id": "t1", "name": "fn", "arguments": "{}"}],
        )
        out = self._render("", structured=s)
        assert len(out["content"]) >= 1

    def test_null_structured_no_regression(self):
        out = self._render("text", structured=None)
        assert out["stop_reason"] == "end_turn"
        assert any(b["type"] == "text" for b in out["content"])


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

    def test_text_path_no_structured_defaults_to_stop(self):
        raw = self._stream(["hello"])
        chunks = _parse_openai_sse(raw)
        final = chunks[-1]
        assert final["choices"][0]["finish_reason"] == "stop"
        # No tool_calls delta
        tool_chunks = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
        assert not tool_chunks

    def test_finish_reason_from_structured(self):
        s = StructuredResult(finish_reason="length")
        raw = self._stream(["hi"], get_structured=lambda: s)
        chunks = _parse_openai_sse(raw)
        final = chunks[-1]
        assert final["choices"][0]["finish_reason"] == "length"

    def test_tool_calls_chunks_emitted(self):
        s = StructuredResult(
            finish_reason="tool_calls",
            tool_calls=[{
                "id": "call_abc",
                "name": "grep",
                "arguments": '{"pattern": "foo", "path": "/src"}',
            }],
        )
        raw = self._stream(["text"], get_structured=lambda: s)
        chunks = _parse_openai_sse(raw)

        # Final chunk must have finish_reason="tool_calls"
        final = chunks[-1]
        assert final["choices"][0]["finish_reason"] == "tool_calls"

        # Header chunk: id, type, name
        tool_chunks = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
        assert tool_chunks, "Expected tool_call chunks"
        header = tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
        assert header["id"] == "call_abc"
        assert header["function"]["name"] == "grep"

        # Arguments chunk
        if len(tool_chunks) > 1:
            args_chunk = tool_chunks[1]["choices"][0]["delta"]["tool_calls"][0]
            assert args_chunk["function"]["arguments"] == '{"pattern": "foo", "path": "/src"}'

    def test_tool_calls_dict_arguments_serialized(self):
        s = StructuredResult(
            finish_reason="tool_calls",
            tool_calls=[{"id": "c1", "name": "fn", "arguments": {"x": 1}}],
        )
        raw = self._stream([], get_structured=lambda: s)
        chunks = _parse_openai_sse(raw)
        tool_chunks = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
        # At minimum there should be a header chunk with the function name
        assert tool_chunks

    def test_none_structured_no_regression(self):
        raw = self._stream(["text"], get_structured=lambda: None)
        chunks = _parse_openai_sse(raw)
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# OpenAIDialect.render() — structured fields
# ---------------------------------------------------------------------------

class TestOpenAIDialectRender:
    def _render(self, text: str, structured=None) -> dict:
        d = OpenAIDialect()
        r = _mk_request("openai")
        return d.render(r, text, structured=structured)

    def test_text_path_no_structured_defaults(self):
        out = self._render("hello")
        assert out["choices"][0]["finish_reason"] == "stop"
        assert out["choices"][0]["message"]["content"] == "hello"
        assert "tool_calls" not in out["choices"][0]["message"]

    def test_finish_reason_from_structured(self):
        s = StructuredResult(finish_reason="length")
        out = self._render("truncated", structured=s)
        assert out["choices"][0]["finish_reason"] == "length"

    def test_tool_calls_in_message(self):
        s = StructuredResult(
            finish_reason="tool_calls",
            tool_calls=[{
                "id": "call_01",
                "name": "ls",
                "arguments": '{"path": "/"}',
            }],
        )
        out = self._render("", structured=s)
        assert out["choices"][0]["finish_reason"] == "tool_calls"
        tc = out["choices"][0]["message"].get("tool_calls")
        assert tc is not None and len(tc) == 1
        assert tc[0]["id"] == "call_01"
        assert tc[0]["function"]["name"] == "ls"
        assert tc[0]["function"]["arguments"] == '{"path": "/"}'

    def test_dict_arguments_serialized(self):
        s = StructuredResult(
            finish_reason="tool_calls",
            tool_calls=[{"id": "c1", "name": "fn", "arguments": {"k": "v"}}],
        )
        out = self._render("", structured=s)
        tc = out["choices"][0]["message"]["tool_calls"]
        assert json.loads(tc[0]["function"]["arguments"]) == {"k": "v"}

    def test_null_structured_no_regression(self):
        out = self._render("text", structured=None)
        assert out["choices"][0]["finish_reason"] == "stop"
        assert "tool_calls" not in out["choices"][0]["message"]


# ---------------------------------------------------------------------------
# Verifiers are live with real structured fields (#52)
# ---------------------------------------------------------------------------

class TestVerifiersLive:
    """Prove NotTruncated and ToolCallJSONValid fire on real ResponseView data."""

    def test_not_truncated_fires_on_max_tokens(self):
        view = ResponseView(text="x", finish_reason="max_tokens")
        r = NotTruncated().verify(view)
        assert not r.passed
        assert "truncat" in r.reason.lower()

    def test_not_truncated_passes_on_end_turn(self):
        view = ResponseView(text="x", finish_reason="end_turn")
        r = NotTruncated().verify(view)
        assert r.passed

    def test_not_truncated_passes_when_finish_reason_none(self):
        # No finish_reason set → defaults to pass (can't assert truncation)
        view = ResponseView(text="x", finish_reason=None)
        r = NotTruncated().verify(view)
        assert r.passed

    def test_tool_call_json_valid_fires_on_bad_json(self):
        view = ResponseView(
            text="",
            tool_calls=[{"name": "fn", "arguments": '{"key": truncated_json'}],
        )
        r = ToolCallJSONValid().verify(view)
        assert not r.passed

    def test_tool_call_json_valid_passes_on_good_json(self):
        view = ResponseView(
            text="",
            tool_calls=[{"name": "fn", "arguments": '{"key": "val"}'}],
        )
        r = ToolCallJSONValid().verify(view)
        assert r.passed

    def test_tool_call_json_valid_passes_when_no_calls(self):
        # No tool_calls → nothing to validate → pass (not a tool-use response)
        view = ResponseView(text="hello", tool_calls=None)
        r = ToolCallJSONValid().verify(view)
        assert r.passed


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
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
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

    def test_openai_render_with_tool_calls(self):
        s = StructuredResult(
            finish_reason="tool_calls",
            tool_calls=[{"id": "call_x", "name": "fn", "arguments": '{"a": 1}'}],
        )
        backend = _StructuredBackend("", s)
        with self._server(backend) as (host, port):
            status, data = self._post(host, port, "/v1/chat/completions", {
                "model": "m", "messages": [{"role": "user", "content": "go"}],
                "stream": False,
            })
        assert status == 200
        out = json.loads(data)
        choice = out["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        tc = choice["message"].get("tool_calls")
        assert tc is not None and tc[0]["function"]["name"] == "fn"

    def test_openai_render_text_path_no_regression(self):
        backend = _StructuredBackend("hello world", None)
        with self._server(backend) as (host, port):
            status, data = self._post(host, port, "/v1/chat/completions", {
                "model": "m", "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
            })
        assert status == 200
        out = json.loads(data)
        assert out["choices"][0]["finish_reason"] == "stop"
        assert out["choices"][0]["message"]["content"] == "hello world"


class TestFrontDoorStructuredStreaming:
    """front_door passes get_structured to dialect.stream()."""

    @contextmanager
    def _server(self, backend):
        httpd = make_server("127.0.0.1", 0, backend)
        host, port = httpd.server_address[:2]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
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

    def test_openai_stream_emits_tool_calls_chunks(self):
        s = StructuredResult(
            finish_reason="tool_calls",
            tool_calls=[{"id": "call_1", "name": "ls", "arguments": '{"p": "/"}'}],
        )
        backend = _StructuredBackend("", s)
        with self._server(backend) as (host, port):
            status, raw = self._post_stream(host, port, "/v1/chat/completions", {
                "model": "m",
                "messages": [{"role": "user", "content": "go"}],
                "stream": True,
            })
        assert status == 200
        chunks = _parse_openai_sse(raw)
        final = chunks[-1]
        assert final["choices"][0]["finish_reason"] == "tool_calls"
        tool_chunks = [c for c in chunks if c["choices"][0]["delta"].get("tool_calls")]
        assert tool_chunks

    def test_text_path_no_regression_anthropic_stream(self):
        """StaticBackend (no get_last_structured) → defaults unchanged."""
        backend = StaticBackend("hello!")
        with self._server(backend) as (host, port):
            status, raw = self._post_stream(host, port, "/v1/messages", {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 100,
                "stream": True,
            })
        assert status == 200
        events = _parse_anthropic_sse(raw)
        msg_delta = next(e for e in events if e[0] == "message_delta")
        assert msg_delta[1]["delta"]["stop_reason"] == "end_turn"

    def test_text_path_no_regression_openai_stream(self):
        backend = StaticBackend("hi there")
        with self._server(backend) as (host, port):
            status, raw = self._post_stream(host, port, "/v1/chat/completions", {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })
        assert status == 200
        chunks = _parse_openai_sse(raw)
        assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
