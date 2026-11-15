"""Synthetic token observations; no inference, network or protected state."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from anvil_serving.router.backends.relay import RelayBackend, RelayBackendError
from anvil_serving.router.decision_log import TokenDirection, TokenUsage, normalize_usage
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.dialects.responses import ResponsesDialect
from anvil_serving.router.internal import InternalRequest, Message, StructuredResult
from tests.router.helpers import make_tier
from tests.router.test_streaming_relay import FakeStreamTransport, _openai_sse


OPENAI = {"prompt_tokens": 20, "completion_tokens": 10,
          "prompt_tokens_details": {"cached_tokens": 7},
          "completion_tokens_details": {"reasoning_tokens": 4}}
ANTHROPIC = {"input_tokens": 7, "output_tokens": 10,
             "cache_read_input_tokens": 5, "cache_creation_input_tokens": 8,
             "cache_creation": {"ephemeral_5m_input_tokens": 3, "ephemeral_1h_input_tokens": 5}}


def anthropic_sse(*events):
    return b"".join(f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()
                    for event, payload in events)


def request(stream=False):
    return InternalRequest("chat", [Message("user", "hi")], stream=stream, dialect="openai")


def test_closed_frozen_serializer_and_independent_directions():
    usage = normalize_usage({"prompt_tokens": 0}, "openai", estimates=(99, 3))
    assert usage.input == TokenDirection(0, "measured")
    assert usage.output == TokenDirection(3, "estimated", partial=True)
    assert TokenUsage.from_dict(usage.to_dict()) == usage
    assert StructuredResult(normalized_usage=usage) == StructuredResult()
    with pytest.raises(FrozenInstanceError):
        usage.input = TokenDirection()
    with pytest.raises(ValueError):
        TokenUsage.from_dict({**usage.to_dict(), "content": "synthetic-content-canary"})
    with pytest.raises(ValueError):
        TokenDirection.from_dict({**usage.input.to_dict(), "reasoning": "synthetic-content-canary"})
    class ExtraUsage(TokenUsage):
        pass
    with pytest.raises(ValueError):
        ExtraUsage().to_dict()
    with pytest.raises(ValueError):
        TokenDirection(None, "measured")
    with pytest.raises(ValueError):
        TokenDirection(partial=True, applicability="not_applicable")
    with pytest.raises(ValueError):
        normalize_usage(None, "guessed")
    with pytest.raises(ValueError):
        normalize_usage(None, "openai", partial=(True, "yes"))
    assert normalize_usage(["synthetic-content-canary"], "openai").limitations == ("usage_invalid",)


def test_anthropic_cache_aggregate_and_native_components():
    usage = normalize_usage(ANTHROPIC, "anthropic")
    assert usage.input == TokenDirection(20, "measured")
    assert (usage.uncached_input_tokens, usage.cache_read_input_tokens, usage.cache_creation_input_tokens) == (7, 5, 8)
    nested = {k: v for k, v in ANTHROPIC.items() if k != "cache_creation_input_tokens"}
    assert normalize_usage(nested, "anthropic") == usage
    aggregate_wins = {**ANTHROPIC, "cache_creation": {"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 100}}
    assert normalize_usage(aggregate_wins, "anthropic").input.count == 20
    for raw in ({"input_tokens": 7}, {"input_tokens": 7, "cache_read_input_tokens": 0},
                {"input_tokens": 7, "cache_creation": {"ephemeral_5m_input_tokens": 3}}):
        incomplete = normalize_usage(raw, "anthropic")
        assert incomplete.input == TokenDirection(partial=True)
        assert incomplete.uncached_input_tokens == 7
        assert TokenUsage.from_dict(incomplete.to_dict()).uncached_input_tokens == 7
        assert "input_incomplete" in incomplete.limitations
    invalid_aggregate = normalize_usage({**ANTHROPIC, "cache_creation_input_tokens": -1}, "anthropic")
    assert invalid_aggregate.input.count is invalid_aggregate.cache_creation_input_tokens is None
    assert "cache_creation_invalid" in invalid_aggregate.limitations
    overflow = normalize_usage({"input_tokens": 10**15, "cache_read_input_tokens": 1,
                                "cache_creation_input_tokens": 0}, "anthropic")
    assert overflow.input.count is None and "input_count_overflow" in overflow.limitations
    assert normalize_usage({"input_tokens": 0, "cache_read_input_tokens": 0,
                            "cache_creation_input_tokens": 0}, "anthropic").input == TokenDirection(0, "measured")


@pytest.mark.parametrize("upstream", ["openai", "anthropic"])
@pytest.mark.parametrize("caller", ["openai", "anthropic", "responses"])
def test_caller_dialect_never_selects_upstream_accounting(upstream, caller):
    dialect = {"openai": OpenAIDialect, "anthropic": AnthropicDialect,
               "responses": ResponsesDialect}[caller]()
    body = {"model": "chat", "stream": False, "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}]}
    if caller == "responses":
        body = {"model": "chat", "stream": False, "input": "hi"}
    req = dialect.parse_request(body)
    if upstream == "openai":
        reply = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": OPENAI}
        payload = _openai_sse({"choices": [{"delta": {"content": "ok"}}]}, {"usage": OPENAI, "choices": []})
    else:
        reply = {"content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "usage": ANTHROPIC}
        payload = anthropic_sse(("message_start", {"message": {"usage": ANTHROPIC}}),
                                ("content_block_delta", {"delta": {"type": "text_delta", "text": "ok"}}),
                                ("message_delta", {"usage": {"output_tokens": 10}}), ("message_stop", {}))
    backend = RelayBackend(make_tier(upstream), env={},
                           transport=lambda *args, **kwargs: json.dumps(reply).encode(),
                           stream_transport=FakeStreamTransport(payload))
    assert list(backend.generate(req)) == ["ok"]
    structured = backend.get_last_structured()
    normalized = backend.get_last_normalized_usage()
    assert structured.normalized_usage == normalized
    assert (normalized.input.count, normalized.output.count) == (20, 10)
    # Accounting's native Anthropic sum does not alter legacy wire counts.
    assert structured.usage["input_tokens"] == (20 if upstream == "openai" else 7)
    assert "cache_creation_input_tokens" not in structured.usage
    rendered = dialect.render(req, "ok", structured=structured)
    in_key = "prompt_tokens" if caller == "openai" else "input_tokens"
    assert rendered["usage"][in_key] == structured.usage["input_tokens"]
    assert "normalized_usage" not in json.dumps(rendered)


def test_buffered_fault_json_stream_fallback_and_reset():
    reply = {"usage": OPENAI}  # Usage observed before the text-shape error.
    backend = RelayBackend(make_tier("openai"), env={}, transport=lambda *a, **k: json.dumps(reply).encode())
    with pytest.raises(RelayBackendError):
        list(backend.generate(request()))
    assert backend.get_last_normalized_usage().input.count == 20
    transport = FakeStreamTransport(json.dumps({"choices": [], "usage": OPENAI}).encode(), "application/json")
    backend = RelayBackend(make_tier("openai"), env={}, stream_transport=transport)
    assert list(backend.generate(request(True))) == []
    assert backend.get_last_normalized_usage().output.count == 10
    iterator = backend.generate(request(True))
    assert backend.get_last_normalized_usage() == TokenUsage()
    iterator.close()
    assert backend.get_last_normalized_usage() == TokenUsage()
    def fail(*args, **kwargs):
        raise RelayBackendError("synthetic transport failure")
    backend._stream_transport = fail
    with pytest.raises(RelayBackendError):
        backend.generate(request(True))
    assert backend.get_last_normalized_usage() == TokenUsage()
