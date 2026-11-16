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
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.internal import InternalRequest, Message
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


# --------------------------------------------------------------------------- #
# Streaming assemblers (mirror the buffered shapes exactly)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# OpenAI dialect wire (streaming usage chunk + non-streaming render)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Anthropic dialect wire
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Responses dialect wire
# --------------------------------------------------------------------------- #


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
