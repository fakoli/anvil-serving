"""Prompt-cache accounting relay — ``cache_read_input_tokens`` end to end.

vLLM serves launched with ``--enable-prompt-tokens-details`` report prefix-cache
hits as ``usage.prompt_tokens_details.cached_tokens``; Anthropic upstreams
report ``usage.cache_read_input_tokens``. The relay normalizes both into the
optional ``cache_read_input_tokens`` key on ``StructuredResult.usage`` and each
dialect re-renders its own wire vocabulary:

* OpenAI Chat Completions: ``usage.prompt_tokens_details.cached_tokens``
* Anthropic Messages: ``usage.cache_read_input_tokens``
* Responses: ``usage.input_tokens_details.cached_tokens``

Mirrors the existing usage-relay tests (test_streaming_relay.py /
test_structured_fields.py). When the upstream omits the field, every wire
shape is identical to before — no zero-filled details blocks, no estimates.
"""
from __future__ import annotations

import json

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.backends.sse import (
    AnthropicStreamAssembler,
    OpenAIStreamAssembler,
)
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.dialects.responses import ResponsesDialect
from anvil_serving.router.internal import InternalRequest, Message, StructuredResult
from tests.router.helpers import make_tier as _tier
from tests.router.test_streaming_relay import (
    FakeStreamTransport,
    _openai_sse,
    _request,
)

ENV = {"EXAMPLE_KEY": "sk-test-not-real"}


def _mk_request(dialect: str) -> InternalRequest:
    return InternalRequest(
        model="test-model",
        messages=[Message("user", "hi")],
        max_tokens=100,
        dialect=dialect,
    )


def _mk_usage_stream_req() -> InternalRequest:
    """OpenAI request that asks for the trailing usage chunk."""
    return InternalRequest(
        model="test-model",
        messages=[Message("user", "hi")],
        max_tokens=100,
        dialect="openai",
        raw={"stream": True, "stream_options": {"include_usage": True}},
    )


def _parse_openai_sse(raw: bytes) -> list:
    parsed = []
    for line in raw.decode().split("data: "):
        line = line.strip()
        if not line or line == "[DONE]":
            continue
        parsed.append(json.loads(line))
    return parsed


def _parse_anthropic_events(raw: bytes) -> list:
    events = []
    for frame in raw.decode().split("\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        name, _, data = frame.partition("\ndata: ")
        events.append((name.removeprefix("event: "), json.loads(data)))
    return events


# --------------------------------------------------------------------------- #
# Buffered extraction (RelayBackend._extract_structured)
# --------------------------------------------------------------------------- #
class TestBufferedExtraction:
    def _openai_backend(self, reply: dict) -> RelayBackend:
        body = json.dumps(reply).encode()
        return RelayBackend(_tier("openai"), env=ENV,
                            transport=lambda url, *, data, headers, timeout: body)

    def test_openai_prompt_tokens_details_normalized(self):
        backend = self._openai_backend({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 2,
                      "total_tokens": 14,
                      "prompt_tokens_details": {"cached_tokens": 9}},
        })
        list(backend.generate(_mk_request("openai")))
        s = backend.get_last_structured()
        assert s.usage == {"input_tokens": 12, "output_tokens": 2,
                           "cache_read_input_tokens": 9}

    def test_openai_without_details_unchanged(self):
        backend = self._openai_backend({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 2},
        })
        list(backend.generate(_mk_request("openai")))
        assert backend.get_last_structured().usage == {
            "input_tokens": 12, "output_tokens": 2}

    def test_openai_invalid_cached_values_dropped(self):
        for bad in (-1, True, "9", None, 2.5):
            backend = self._openai_backend({
                "choices": [{"message": {"content": "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 2,
                          "prompt_tokens_details": {"cached_tokens": bad}},
            })
            list(backend.generate(_mk_request("openai")))
            assert backend.get_last_structured().usage == {
                "input_tokens": 12, "output_tokens": 2}, bad

    def test_anthropic_cache_read_normalized(self):
        body = json.dumps({
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "ok"}],
            "usage": {"input_tokens": 12, "output_tokens": 2,
                      "cache_read_input_tokens": 9},
        }).encode()
        backend = RelayBackend(_tier("anthropic"), env=ENV,
                               transport=lambda url, *, data, headers, timeout: body)
        list(backend.generate(_mk_request("anthropic")))
        assert backend.get_last_structured().usage == {
            "input_tokens": 12, "output_tokens": 2,
            "cache_read_input_tokens": 9}


# --------------------------------------------------------------------------- #
# Streaming assemblers (mirror the buffered shapes exactly)
# --------------------------------------------------------------------------- #
def test_openai_stream_assembler_captures_cached_tokens():
    a = OpenAIStreamAssembler()
    a.feed(None, json.dumps({
        "choices": [],
        "usage": {"prompt_tokens": 12, "completion_tokens": 2,
                  "prompt_tokens_details": {"cached_tokens": 9}},
    }))
    assert a.result().usage == {"input_tokens": 12, "output_tokens": 2,
                                "cache_read_input_tokens": 9}


def test_openai_stream_assembler_without_details_unchanged():
    a = OpenAIStreamAssembler()
    a.feed(None, json.dumps({
        "choices": [],
        "usage": {"prompt_tokens": 12, "completion_tokens": 2},
    }))
    assert a.result().usage == {"input_tokens": 12, "output_tokens": 2}


def test_anthropic_stream_assembler_cache_read_from_message_start():
    a = AnthropicStreamAssembler()
    a.feed("message_start", json.dumps({
        "type": "message_start",
        "message": {"usage": {"input_tokens": 7, "output_tokens": 0,
                              "cache_read_input_tokens": 5}},
    }))
    a.feed("message_delta", json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 9},
    }))
    assert a.result().usage == {"input_tokens": 7, "output_tokens": 9,
                                "cache_read_input_tokens": 5}


def test_anthropic_stream_assembler_cache_read_from_message_delta():
    # Some upstreams report cumulative usage (incl. cache reads) on
    # message_delta; the later value wins.
    a = AnthropicStreamAssembler()
    a.feed("message_start", json.dumps({
        "type": "message_start",
        "message": {"usage": {"input_tokens": 7, "output_tokens": 0}},
    }))
    a.feed("message_delta", json.dumps({
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 9, "cache_read_input_tokens": 5},
    }))
    assert a.result().usage == {"input_tokens": 7, "output_tokens": 9,
                                "cache_read_input_tokens": 5}


# --------------------------------------------------------------------------- #
# OpenAI dialect wire (streaming usage chunk + non-streaming render)
# --------------------------------------------------------------------------- #
class TestOpenAIDialectWire:
    def test_usage_chunk_carries_prompt_tokens_details(self):
        r = _mk_usage_stream_req()
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7,
                                    "cache_read_input_tokens": 9})
        d = OpenAIDialect()
        chunks = _parse_openai_sse(
            b"".join(d.stream(r, ["ok"], get_structured=lambda: s)))
        (u,) = [c for c in chunks if c.get("usage") is not None]
        assert u["usage"] == {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
            "prompt_tokens_details": {"cached_tokens": 9},
        }

    def test_usage_chunk_without_cached_has_no_details_block(self):
        r = _mk_usage_stream_req()
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7})
        d = OpenAIDialect()
        chunks = _parse_openai_sse(
            b"".join(d.stream(r, ["ok"], get_structured=lambda: s)))
        (u,) = [c for c in chunks if c.get("usage") is not None]
        assert u["usage"] == {"prompt_tokens": 12, "completion_tokens": 7,
                              "total_tokens": 19}

    def test_render_carries_prompt_tokens_details(self):
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7,
                                    "cache_read_input_tokens": 9})
        out = OpenAIDialect().render(_mk_request("openai"), "ok", structured=s)
        assert out["usage"] == {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
            "prompt_tokens_details": {"cached_tokens": 9},
        }

    def test_render_without_cached_has_no_details_block(self):
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7})
        out = OpenAIDialect().render(_mk_request("openai"), "ok", structured=s)
        assert out["usage"] == {"prompt_tokens": 12, "completion_tokens": 7,
                                "total_tokens": 19}

    def test_render_estimate_path_has_no_details_block(self):
        out = OpenAIDialect().render(_mk_request("openai"), "ok", structured=None)
        assert "prompt_tokens_details" not in out["usage"]


# --------------------------------------------------------------------------- #
# Anthropic dialect wire
# --------------------------------------------------------------------------- #
class TestAnthropicDialectWire:
    def test_render_passes_cache_read_through(self):
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7,
                                    "cache_read_input_tokens": 9})
        out = AnthropicDialect().render(_mk_request("anthropic"), "ok",
                                        structured=s)
        assert out["usage"] == {"input_tokens": 12, "output_tokens": 7,
                                "cache_read_input_tokens": 9}

    def test_stream_message_delta_carries_cache_read(self):
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7,
                                    "cache_read_input_tokens": 9})
        events = _parse_anthropic_events(b"".join(
            AnthropicDialect().stream(_mk_request("anthropic"), ["ok"],
                                      get_structured=lambda: s)))
        (delta,) = [d for name, d in events if name == "message_delta"]
        assert delta["usage"] == {"output_tokens": 7,
                                  "cache_read_input_tokens": 9}

    def test_stream_message_delta_without_cached_unchanged(self):
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7})
        events = _parse_anthropic_events(b"".join(
            AnthropicDialect().stream(_mk_request("anthropic"), ["ok"],
                                      get_structured=lambda: s)))
        (delta,) = [d for name, d in events if name == "message_delta"]
        assert delta["usage"] == {"output_tokens": 7}


# --------------------------------------------------------------------------- #
# Responses dialect wire
# --------------------------------------------------------------------------- #
class TestResponsesDialectWire:
    def test_render_carries_input_tokens_details(self):
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7,
                                    "cache_read_input_tokens": 9})
        out = ResponsesDialect().render(_mk_request("openai"), "ok",
                                        structured=s)
        assert out["usage"] == {
            "input_tokens": 12,
            "output_tokens": 7,
            "total_tokens": 19,
            "input_tokens_details": {"cached_tokens": 9},
        }

    def test_render_without_cached_has_no_details_block(self):
        s = StructuredResult(usage={"input_tokens": 12, "output_tokens": 7})
        out = ResponsesDialect().render(_mk_request("openai"), "ok",
                                        structured=s)
        assert out["usage"] == {"input_tokens": 12, "output_tokens": 7,
                                "total_tokens": 19}


# --------------------------------------------------------------------------- #
# End-to-end: streaming relay -> assembler -> OpenAI dialect usage chunk
# --------------------------------------------------------------------------- #
def test_vllm_cached_tokens_flow_to_openai_usage_chunk():
    """Full path of the fix: a vLLM-style trailing usage chunk with
    ``prompt_tokens_details`` reaches the caller's trailing usage chunk as
    ``prompt_tokens_details.cached_tokens`` (mirrors
    test_openai_streaming_usage_flows_to_dialect_usage_chunk)."""
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"content": "ok"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 2,
                                  "total_tokens": 14,
                                  "prompt_tokens_details": {"cached_tokens": 9}}},
    )
    backend = RelayBackend(_tier("openai"), env=ENV,
                           stream_transport=FakeStreamTransport(payload))
    request = _request(stream=True)
    request.raw = {"stream": True, "stream_options": {"include_usage": True}}
    deltas = list(backend.generate(request))
    assert deltas == ["ok"]

    chunks = _parse_openai_sse(b"".join(OpenAIDialect().stream(
        request, deltas, get_structured=backend.get_last_structured)))
    (u,) = [c for c in chunks if c.get("usage") is not None]
    assert u["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 2,
        "total_tokens": 14,
        "prompt_tokens_details": {"cached_tokens": 9},
    }
