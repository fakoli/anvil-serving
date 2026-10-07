"""Synthetic token observations; no inference, network or protected state."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace

import pytest

from anvil_serving.router.backends.relay import RelayBackend, RelayBackendError
from anvil_serving.router.backends.sse import AnthropicStreamAssembler, OpenAIStreamAssembler
from anvil_serving.router.decision_log import TokenDirection, TokenUsage, normalize_usage
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.dialects.responses import ResponsesDialect
from anvil_serving.router.internal import InternalRequest, Message, StructuredResult, estimate_tokens
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


def test_unknown_measured_zero_and_non_token_are_distinct():
    absent = normalize_usage(None, "openai")
    zero = normalize_usage({"prompt_tokens": 0, "completion_tokens": 0}, "openai")
    assert absent.input == TokenDirection() and absent.output == TokenDirection()
    assert zero.input == zero.output == TokenDirection(0, "measured")
    assert set(absent.to_dict()) == {"input", "output", "limitations"}
    non_token = normalize_usage(OPENAI, "openai", ("not_applicable", "not_applicable"))
    assert non_token.input == non_token.output == TokenDirection(applicability="not_applicable")
    assert non_token.cache_read_input_tokens is None and non_token.reasoning_output_tokens is None
    assert normalize_usage({"prompt_tokens": 5}, "openai", ("applicable", "not_applicable")).input.count == 5


@pytest.mark.parametrize("bad", [True, -1, 1.5, "8", 10**15 + 1, None, [], {}])
def test_invalid_counts_never_become_reported_zero(bad):
    raw = {"prompt_tokens": bad, "completion_tokens": bad,
           "prompt_tokens_details": {"cached_tokens": bad},
           "completion_tokens_details": {"reasoning_tokens": bad}}
    usage = normalize_usage(raw, "openai")
    assert usage.input.count is usage.output.count is None
    assert usage.cache_read_input_tokens is usage.reasoning_output_tokens is None
    assert set(usage.limitations) == {"input_count_invalid", "output_count_invalid",
                                     "cache_read_invalid", "reasoning_invalid"}
    if bad is not None:
        with pytest.raises(ValueError):
            TokenDirection(bad, "measured")


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


@pytest.mark.parametrize("dialect,raw", [
    ("openai", OPENAI),
    ("responses", {"input_tokens": 20, "output_tokens": 10,
                   "input_tokens_details": {"cached_tokens": 7},
                   "output_tokens_details": {"reasoning_tokens": 4}}),
])
def test_openai_inclusive_totals_do_not_add_subsets(dialect, raw):
    usage = normalize_usage(raw, dialect)
    assert (usage.input.count, usage.output.count) == (20, 10)
    assert (usage.cache_read_input_tokens, usage.reasoning_output_tokens) == (7, 4)
    assert usage.uncached_input_tokens is usage.cache_creation_input_tokens is None


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


@pytest.mark.parametrize("visible", ["", "visible synthetic answer"])
def test_missing_tool_or_reasoning_counts_use_only_partial_visible_estimates(visible):
    estimate = estimate_tokens([visible])
    usage = normalize_usage(None, "openai", estimates=(estimate_tokens(["synthetic input"]), estimate))
    assert usage.input.source == "estimated" and usage.input.partial
    if visible:
        assert usage.output == TokenDirection(estimate, "estimated", partial=True)
    else:
        assert usage.output == TokenDirection()
    assert "synthetic" not in json.dumps(usage.to_dict())


def test_cumulative_sse_replaces_partial_counts_and_preserves_missing_components():
    a = AnthropicStreamAssembler()
    a.feed("message_start", json.dumps({"message": {"usage": {"input_tokens": 7, "output_tokens": 0}}}))
    assert a.get_normalized_usage().output == TokenDirection(0, "measured", partial=True)
    assert a.get_normalized_usage().uncached_input_tokens == 7
    a.feed("message_delta", json.dumps({"usage": {"output_tokens": 3, "cache_read_input_tokens": 5}}))
    a.feed("message_delta", json.dumps({"usage": {"output_tokens": 10, "cache_creation_input_tokens": 8}}))
    assert a.get_normalized_usage().input == TokenDirection(20, "measured")
    assert a.get_normalized_usage().output == TokenDirection(10, "measured", partial=True)
    a.feed("message_stop", "{}")
    assert a.get_normalized_usage().output == TokenDirection(10, "measured")
    assert a.result().normalized_usage == a.get_normalized_usage()
    b = OpenAIStreamAssembler()
    b.feed(None, json.dumps({"usage": OPENAI}))
    b.feed(None, json.dumps({"usage": {"completion_tokens": 11}}))
    assert b.get_normalized_usage().input.count == 20
    assert b.get_normalized_usage().output.count == 11
    b.feed(None, json.dumps({"usage": {"completion_tokens": False}}))
    assert b.get_normalized_usage().output.count is None
    # Legacy parser's earlier valid value is intentionally unchanged.
    assert b.result().usage["output_tokens"] == 11


@pytest.mark.parametrize("upstream", ["openai", "anthropic"])
@pytest.mark.parametrize("caller", ["openai", "anthropic", "responses"])
@pytest.mark.parametrize("stream", [False, True])
def test_caller_dialect_never_selects_upstream_accounting(upstream, caller, stream):
    dialect = {"openai": OpenAIDialect, "anthropic": AnthropicDialect,
               "responses": ResponsesDialect}[caller]()
    body = {"model": "chat", "stream": stream, "max_tokens": 64,
            "messages": [{"role": "user", "content": "hi"}]}
    if caller == "responses":
        body = {"model": "chat", "stream": stream, "input": "hi"}
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


@pytest.mark.parametrize("upstream", ["openai", "anthropic"])
def test_close_retains_observed_metadata_without_publishing_partial_wire(upstream):
    if upstream == "openai":
        payload = _openai_sse({"usage": OPENAI, "choices": [{"delta": {"content": "visible"}}]}, done=False)
    else:
        payload = anthropic_sse(("message_start", {"message": {"usage": {"input_tokens": 7, "output_tokens": 0}}}),
                                ("content_block_delta", {"delta": {"type": "text_delta", "text": "visible"}}))
    transport = FakeStreamTransport(payload)
    backend = RelayBackend(make_tier(upstream), env={}, stream_transport=transport)
    iterator = backend.generate(request(True))
    assert next(iterator) == "visible"
    before = backend.get_last_normalized_usage()
    iterator.close()
    assert backend.get_last_normalized_usage() == before
    assert before.output.source == "measured" and before.output.partial
    if upstream == "anthropic":
        assert before.input.count is None and before.uncached_input_tokens == 7
    assert backend.get_last_structured() is None and transport.response.closed


@pytest.mark.parametrize("failure", ["missing_terminator", "provider_error", "raw_cap"])
def test_stream_failure_retains_measured_counts(failure):
    first = _openai_sse({"usage": OPENAI, "choices": []}, done=False)
    if failure == "provider_error":
        payload = first + _openai_sse({"error": {"message": "synthetic-content-canary"}}, done=False)
    elif failure == "raw_cap":
        payload = first + _openai_sse({"choices": [{"delta": {"content": "x" * 1000}}]}, done=False)
    else:
        payload = first
    transport = FakeStreamTransport(payload)
    backend = RelayBackend(make_tier("openai"), env={}, stream_transport=transport,
                           max_response_bytes=len(first) + 10 if failure == "raw_cap" else None)
    with pytest.raises(RelayBackendError):
        list(backend.generate(request(True)))
    usage = backend.get_last_normalized_usage()
    assert usage.input.count == 20 and usage.output == TokenDirection(10, "measured", partial=True)
    assert "canary" not in json.dumps(usage.to_dict())
    assert backend.get_last_structured() is None and transport.response.closed


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


def test_observations_are_thread_local_and_do_not_retain_content():
    def transport(*args, data, **kwargs):
        count = int(json.loads(data)["messages"][0]["content"])
        return json.dumps({"choices": [{"message": {"content": "synthetic-response-canary",
                            "reasoning_content": "synthetic-reasoning-canary",
                            "tool_calls": [{"id": "call_synthetic", "function": {"name": "example",
                             "arguments": "synthetic-tool-canary"}}]}}],
                           "usage": {"prompt_tokens": count, "completion_tokens": 2,
                                     "content": "synthetic-usage-canary"}}).encode()
    backend = RelayBackend(make_tier("openai"), env={}, transport=transport)
    def run(count):
        list(backend.generate(replace(request(), messages=[Message("user", str(count))])))
        usage = backend.get_last_normalized_usage()
        assert "canary" not in json.dumps(usage.to_dict())
        return usage.input.count
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, (5, 9))) == [5, 9]
    assert backend.get_last_normalized_usage() is None
