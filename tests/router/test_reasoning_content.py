"""Structured OpenAI reasoning passthrough without answer-text leakage.

OpenAI-compatible engines such as SGLang emit ``reasoning_content`` separately
from the visible answer. The relay must preserve that distinction in buffered
and true-streaming responses, carry reported reasoning-token usage, and replay
reasoning-bearing assistant history only on the same OpenAI dialect.
"""
from __future__ import annotations

import json

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.dialects.responses import ResponsesDialect
from anvil_serving.router.internal import InternalRequest, Message, ModelDelta, StructuredResult
from tests.router.helpers import make_tier
from tests.router.test_streaming_relay import FakeStreamTransport, _openai_sse

_ENV = {"EXAMPLE_KEY": "sk-test-not-real"}


def _request(*, stream: bool = False, include_usage: bool = False) -> InternalRequest:
    raw = {"stream": stream}
    if include_usage:
        raw["stream_options"] = {"include_usage": True}
    return InternalRequest(
        model="chat",
        messages=[Message("user", "What is two plus two?")],
        max_tokens=64,
        stream=stream,
        dialect="openai",
        raw=raw,
    )


def _parse_openai_sse(raw: bytes) -> list[dict]:
    chunks = []
    for frame in raw.decode().split("\n\n"):
        if not frame.startswith("data: "):
            continue
        payload = frame.removeprefix("data: ")
        if payload != "[DONE]":
            chunks.append(json.loads(payload))
    return chunks


def test_buffered_reasoning_content_and_reported_usage_round_trip() -> None:
    upstream = json.dumps({
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "reasoning_content": "I should add the operands.",
                "content": "Four",
            },
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 12,
            # SGLang's actual wire extension is top-level.
            "reasoning_tokens": 9,
        },
    }).encode()
    backend = RelayBackend(
        make_tier("openai"),
        env=_ENV,
        transport=lambda url, *, data, headers, timeout: upstream,
    )
    request = _request()

    assert list(backend.generate(request)) == ["Four"]
    structured = backend.get_last_structured()
    assert structured == StructuredResult(
        finish_reason="stop",
        usage={
            "input_tokens": 8,
            "output_tokens": 12,
            "reasoning_tokens": 9,
        },
        reasoning="I should add the operands.",
    )

    response = OpenAIDialect().render(
        request, "Four", structured=structured, response_model="chat"
    )
    assert response["choices"][0]["message"] == {
        "role": "assistant",
        "content": "Four",
        "reasoning_content": "I should add the operands.",
    }
    assert response["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 12,
        "total_tokens": 20,
        "completion_tokens_details": {"reasoning_tokens": 9},
    }


def test_true_streaming_reasoning_order_and_usage_round_trip() -> None:
    payload = _openai_sse(
        {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
        {"choices": [{"index": 0, "delta": {"reasoning_content": "First "}}]},
        {"choices": [{"index": 0, "delta": {"reasoning_content": "reason."}}]},
        {"choices": [{"index": 0, "delta": {"content": "Four"}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 12,
            "reasoning_tokens": 9,
        }},
    )
    backend = RelayBackend(
        make_tier("openai"),
        env=_ENV,
        stream_transport=FakeStreamTransport(payload),
    )
    request = _request(stream=True, include_usage=True)

    deltas = list(backend.generate(request))
    assert deltas == [
        ModelDelta(reasoning="First "),
        ModelDelta(reasoning="reason."),
        "Four",
    ]
    assert backend.get_last_structured().reasoning == "First reason."

    chunks = _parse_openai_sse(b"".join(OpenAIDialect().stream(
        request,
        deltas,
        get_structured=backend.get_last_structured,
        response_model="chat",
    )))
    ordered_deltas = [
        chunk["choices"][0]["delta"]
        for chunk in chunks
        if chunk.get("choices")
    ]
    assert ordered_deltas == [
        {"role": "assistant"},
        {"reasoning_content": "First "},
        {"reasoning_content": "reason."},
        {"content": "Four"},
        {},
    ]
    (usage_chunk,) = [chunk for chunk in chunks if chunk.get("usage") is not None]
    assert usage_chunk["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 12,
        "total_tokens": 20,
        "completion_tokens_details": {"reasoning_tokens": 9},
    }


def test_standard_nested_reasoning_usage_is_also_normalized() -> None:
    upstream = json.dumps({
        "choices": [{"message": {"content": "Four"}}],
        "usage": {
            "prompt_tokens": 8,
            "completion_tokens": 12,
            "completion_tokens_details": {"reasoning_tokens": 9},
        },
    }).encode()
    backend = RelayBackend(
        make_tier("openai"),
        env=_ENV,
        transport=lambda url, *, data, headers, timeout: upstream,
    )

    list(backend.generate(_request()))
    assert backend.get_last_structured().usage["reasoning_tokens"] == 9


def test_partial_reasoning_usage_does_not_require_base_counters() -> None:
    response = OpenAIDialect().render(
        _request(),
        "",
        structured=StructuredResult(usage={"reasoning_tokens": 9}),
    )

    assert response["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "completion_tokens_details": {"reasoning_tokens": 9},
    }


def test_reasoning_absent_keeps_openai_wire_shape_unchanged() -> None:
    request = _request()
    response = OpenAIDialect().render(
        request,
        "Four",
        structured=StructuredResult(
            finish_reason="stop",
            usage={"input_tokens": 8, "output_tokens": 1},
        ),
    )

    message = response["choices"][0]["message"]
    assert message == {"role": "assistant", "content": "Four"}
    assert response["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 1,
        "total_tokens": 9,
    }


def test_reasoning_is_not_folded_into_anthropic_or_responses_answer_text() -> None:
    reasoning = "provider-private reasoning"
    delta = ModelDelta(reasoning=reasoning)
    answer = ModelDelta(text="Four")
    structured = StructuredResult(
        finish_reason="stop",
        reasoning=reasoning,
        usage={"input_tokens": 8, "output_tokens": 12, "reasoning_tokens": 9},
    )

    anthropic_request = InternalRequest(
        model="chat",
        messages=[Message("user", "question")],
        dialect="anthropic",
    )
    anthropic_wire = b"".join(AnthropicDialect().stream(
        anthropic_request,
        [delta, answer],
        get_structured=lambda: structured,
    ))
    assert reasoning.encode() not in anthropic_wire
    assert b"Four" in anthropic_wire

    responses = ResponsesDialect().render(
        _request(), "Four", structured=structured
    )
    assert reasoning not in json.dumps(responses)
    assert responses["usage"]["output_tokens_details"] == {
        "reasoning_tokens": 9,
    }


def test_openai_reasoning_history_is_replayed_verbatim_same_dialect() -> None:
    wire_messages = [
        {"role": "user", "content": "What is two plus two?"},
        {
            "role": "assistant",
            "content": "Four",
            "reasoning_content": "Add the operands.",
        },
        {"role": "user", "content": "Are you sure?"},
    ]
    request = OpenAIDialect().parse_request({
        "model": "chat",
        "messages": wire_messages,
    })

    body = RelayBackend(make_tier("openai"), env=_ENV)._build_body(request)
    assert body["messages"] == wire_messages


def test_non_assistant_reasoning_field_does_not_enable_raw_message_replay() -> None:
    request = OpenAIDialect().parse_request({
        "model": "chat",
        "messages": [{
            "role": "user",
            "content": "Question",
            "reasoning_content": "caller-controlled extra field",
        }],
    })

    body = RelayBackend(make_tier("openai"), env=_ENV)._build_body(request)
    assert body["messages"] == [{"role": "user", "content": "Question"}]