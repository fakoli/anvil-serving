"""True upstream streaming in CloudBackend/RelayBackend.

Hermetic: a fake stream_transport returns canned SSE bytes; no sockets. Pins:

* real per-chunk deltas come out AS THEY ARRIVE (not one buffered blob);
* the assembled StructuredResult (finish_reason / tool_calls / usage) matches
  the buffered path's shapes for both dialects;
* non-SSE responses (upstream ignored stream:true) fall back to the buffered
  parse; buffered custom transports never engage streaming;
* the request body carries stream:true (+ include_usage on OpenAI), with
  extra_body precedence preserved.
"""
from __future__ import annotations

import io
import json

import pytest

from anvil_serving.router.backends.cloud import CloudBackend, CloudBackendError
from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.backends.sse import (
    AnthropicStreamAssembler,
    OpenAIStreamAssembler,
    iter_sse_events,
)
from anvil_serving.router.config import Tier
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.verify import ResponseView, ToolCallJSONValid


def _tier(dialect: str, privacy: str = "cloud", extra_body=None) -> Tier:
    return Tier(
        id=f"{dialect}-tier", base_url="https://api.example.test",
        dialect=dialect, context_limit=200_000, privacy=privacy,
        tool_support=True, auth_env="EXAMPLE_KEY", model="m",
        extra_body=extra_body,
    )


def _request(stream: bool = True) -> InternalRequest:
    return InternalRequest(
        model="chat", messages=[Message("user", "hi")], max_tokens=64,
        stream=stream, dialect="openai",
    )


class FakeStreamResponse:
    """Line-iterable fake of an open urllib response."""

    def __init__(self, payload: bytes, ctype: str = "text/event-stream"):
        self._fp = io.BytesIO(payload)
        self.headers = {"Content-Type": ctype}
        self.closed = False

    def __iter__(self):
        return iter(self._fp)

    def read(self, n: int = -1) -> bytes:
        return self._fp.read(n)

    def close(self) -> None:
        self.closed = True


class FakeStreamTransport:
    def __init__(self, payload: bytes, ctype: str = "text/event-stream"):
        self.payload = payload
        self.ctype = ctype
        self.bodies = []
        self.response = None

    def __call__(self, url, *, data, headers, timeout):
        self.bodies.append(json.loads(data))
        self.response = FakeStreamResponse(self.payload, self.ctype)
        return self.response


def _openai_sse(*chunks: dict, done: bool = True) -> bytes:
    out = b"".join(
        b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks
    )
    if done:
        out += b"data: [DONE]\n\n"
    return out


# --------------------------------------------------------------------------- #
# SSE parser
# --------------------------------------------------------------------------- #
def test_iter_sse_events_named_and_plain():
    raw = (b"event: message_start\ndata: {\"a\":1}\n\n"
           b": keep-alive comment\n\n"
           b"data: {\"b\":2}\n\n"
           b"data: [DONE]\n\n")
    events = list(iter_sse_events(io.BytesIO(raw)))
    assert events == [("message_start", '{"a":1}'), (None, '{"b":2}'),
                      (None, "[DONE]")]


def test_iter_sse_events_multiline_data_and_no_trailing_blank():
    raw = b"data: {\"x\":\ndata: 1}\n\ndata: tail"
    events = list(iter_sse_events(io.BytesIO(raw)))
    assert events == [(None, '{"x":\n1}'), (None, "tail")]


# --------------------------------------------------------------------------- #
# OpenAI streaming
# --------------------------------------------------------------------------- #
def test_openai_streaming_deltas_and_structured():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        {"choices": [{"index": 0, "delta": {"content": "Hel"}}]},
        {"choices": [{"index": 0, "delta": {"content": "lo"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2}},
    )
    transport = FakeStreamTransport(payload)
    backend = CloudBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=transport)
    deltas = list(backend.generate(_request()))
    assert deltas == ["Hel", "lo"]  # REAL model chunks, not word-split fakes
    s = backend.get_last_structured()
    assert s is not None
    assert s.finish_reason == "stop"
    assert s.usage == {"input_tokens": 12, "output_tokens": 2}
    assert transport.response.closed
    # The upstream body asked to stream, with usage in the final chunk.
    body = transport.bodies[0]
    assert body["stream"] is True
    assert body["stream_options"] == {"include_usage": True}


def test_openai_streaming_tool_calls_accumulate():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"city\": "}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "\"Oakland\"}"}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    )
    backend = CloudBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=FakeStreamTransport(payload))
    assert list(backend.generate(_request())) == []
    s = backend.get_last_structured()
    (tc,) = s.tool_calls
    assert tc["id"] == "call_1" and tc["name"] == "get_weather"
    assert json.loads(tc["arguments"]) == {"city": "Oakland"}  # str, like buffered
    assert s.finish_reason == "tool_calls"


# --------------------------------------------------------------------------- #
# Anthropic streaming
# --------------------------------------------------------------------------- #
def test_anthropic_streaming_deltas_tools_and_usage():
    events = [
        ("message_start", {"type": "message_start", "message": {
            "usage": {"input_tokens": 7, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "Hi"}}),
        ("content_block_start", {"type": "content_block_start", "index": 1,
                                 "content_block": {"type": "tool_use",
                                                   "id": "toolu_1",
                                                   "name": "get_weather",
                                                   "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": "{\"city\": \"Oa"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "input_json_delta",
                                           "partial_json": "kland\"}"}}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "tool_use"},
                           "usage": {"output_tokens": 9}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    payload = b"".join(
        b"event: " + name.encode() + b"\ndata: " + json.dumps(data).encode() + b"\n\n"
        for name, data in events
    )
    request = InternalRequest(
        model="chat", messages=[Message("user", "hi")], max_tokens=64,
        stream=True, dialect="anthropic",
    )
    backend = CloudBackend(_tier("anthropic"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=FakeStreamTransport(payload))
    assert list(backend.generate(request)) == ["Hi"]
    s = backend.get_last_structured()
    assert s.finish_reason == "tool_use"
    (tc,) = s.tool_calls
    assert tc["arguments"] == {"city": "Oakland"}  # parsed dict, like buffered
    assert s.usage == {"input_tokens": 7, "output_tokens": 9}


def test_anthropic_streaming_preserves_malformed_tool_arguments_for_verification():
    assembler = AnthropicStreamAssembler()
    assembler.feed("content_block_start", json.dumps({
        "type": "content_block_start",
        "index": 0,
        "content_block": {
            "type": "tool_use",
            "id": "toolu_bad",
            "name": "get_weather",
            "input": {},
        },
    }))
    assembler.feed("content_block_delta", json.dumps({
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": "{not json"},
    }))

    (tool_call,) = assembler.result().tool_calls
    assert tool_call["arguments"] == "{not json"
    verdict = ToolCallJSONValid().verify(ResponseView(tool_calls=[tool_call]))
    assert verdict.passed is False
    assert "not valid JSON" in verdict.reason


# --------------------------------------------------------------------------- #
# gating / fallbacks / caps
# --------------------------------------------------------------------------- #
def test_non_sse_response_falls_back_to_buffered_parse():
    body = json.dumps({
        "choices": [{"message": {"content": "one two"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }).encode()
    backend = CloudBackend(
        _tier("openai"), env={"EXAMPLE_KEY": "k"},
        stream_transport=FakeStreamTransport(body, ctype="application/json"))
    assert "".join(backend.generate(_request())) == "one two"
    s = backend.get_last_structured()
    assert s.finish_reason == "stop"
    assert s.usage == {"input_tokens": 3, "output_tokens": 2}


def test_custom_buffered_transport_never_streams():
    """A hermetic buffered transport (no stream companion) keeps the old path —
    it must not attempt a network streaming call."""
    calls = []

    def buffered(url, *, data, headers, timeout):
        calls.append(json.loads(data))
        return json.dumps({
            "choices": [{"message": {"content": "buffered"},
                         "finish_reason": "stop"}],
        }).encode()

    backend = CloudBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           transport=buffered)
    assert "".join(backend.generate(_request(stream=True))) == "buffered"
    assert calls[0]["stream"] is False  # buffered path body unchanged


def test_extra_body_stream_override_wins():
    tier = _tier("openai", extra_body={"stream": False})
    transport = FakeStreamTransport(_openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "x"}}]}))
    backend = CloudBackend(tier, env={"EXAMPLE_KEY": "k"},
                           stream_transport=transport)
    list(backend.generate(_request()))
    body = transport.bodies[0]
    assert body["stream"] is False           # operator override respected
    assert "stream_options" not in body


def test_streaming_response_cap_enforced():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "x" * 64}}]},
        {"choices": [{"index": 0, "delta": {"content": "y" * 64}}]},
    )
    backend = CloudBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"},
                           stream_transport=FakeStreamTransport(payload),
                           max_response_bytes=100)
    with pytest.raises(CloudBackendError):
        list(backend.generate(_request()))


def test_relay_backend_streams_too():
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "local "}}]},
        {"choices": [{"index": 0, "delta": {"content": "stream"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    )
    tier = _tier("openai", privacy="local")
    backend = RelayBackend(tier, env={},
                           stream_transport=FakeStreamTransport(payload))
    assert list(backend.generate(_request())) == ["local ", "stream"]


def test_assemblers_skip_malformed_events():
    oa = OpenAIStreamAssembler()
    assert oa.feed(None, "not json") is None
    assert oa.feed(None, '{"choices": "nope"}') is None
    an = AnthropicStreamAssembler()
    assert an.feed("content_block_delta", "not json") is None
    assert an.feed(None, '{"type": "content_block_delta", "delta": 5}') is None
