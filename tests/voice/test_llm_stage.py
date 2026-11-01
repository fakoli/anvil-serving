"""Tests for anvil_serving.voice.stages.llm -- the OpenAI Chat Completions
LLM stage.

Dependency-light and hermetic: no real sockets are opened. ``stream_chat_completion``
is exercised through an injectable fake ``transport`` (mirroring
``RelayBackend``'s ``stream_transport`` seam in
``anvil_serving/router/backends/relay.py``/``tests/router/test_streaming_relay.py``)
that returns a canned in-memory SSE response, never a live ``urllib`` connection.
No GPU, no torch, no network.
"""
from __future__ import annotations

import json


from anvil_serving.voice.messages import GenerateRequest, LLMToolCall
from anvil_serving.voice.stages.llm import (
    LLMStage,
    LLMClientError,
    LLMStageConfig,
    LLMStreamToolCalls,
    SentenceBatcher,
    build_request_body,
    stream_chat_completion_events,
)
from tests.voice.conftest import FakeLineResponse as FakeResponse


# --------------------------------------------------------------------------- #
# fakes: an in-memory transport, mirroring test_streaming_relay.py's DI style
# --------------------------------------------------------------------------- #
class FakeTransport:
    """Records every call and returns a canned SSE payload -- proves the LLM
    stage never has to touch a real socket, and lets tests assert exactly
    which URL/body were sent."""

    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = []
        self.response = None

    def __call__(self, url, *, data, headers, timeout):
        self.calls.append({"url": url, "body": json.loads(data), "headers": dict(headers), "timeout": timeout})
        self.response = FakeResponse(self.payload)
        return self.response


def _sse(*chunks: dict, done: bool = True) -> bytes:
    out = b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks)
    if done:
        out += b"data: [DONE]\n\n"
    return out


def _chunk(text: str, finish: str | None = None) -> dict:
    delta = {"content": text} if text else {}
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _tool_call_chunk(index: int, *, call_id: str | None = None, name: str | None = None, args: str = "") -> dict:
    call = {"index": index, "function": {"arguments": args}}
    if call_id:
        call["id"] = call_id
        call["type"] = "function"
    if name:
        call["function"]["name"] = name
    return {"choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": None}]}


# --------------------------------------------------------------------------- #
# build_request_body: wire contract
# --------------------------------------------------------------------------- #


def test_build_request_body_normalizes_realtime_tools_to_chat_completions_shape():
    body = build_request_body(
        "what is the weather?",
        LLMStageConfig(
            tools=[
                {
                    "type": "function",
                    "name": "openclaw_agent_consult",
                    "description": "Ask OpenClaw.",
                    "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
                }
            ],
            tool_choice="auto",
        ),
    )
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "openclaw_agent_consult",
                "description": "Ask OpenClaw.",
                "parameters": {"type": "object", "properties": {"question": {"type": "string"}}},
            },
        }
    ]
    assert body["tool_choice"] == "auto"


# --------------------------------------------------------------------------- #
# stream_chat_completion: URL construction + SSE assembly (hermetic)
# --------------------------------------------------------------------------- #


def test_stream_chat_completion_events_surfaces_final_tool_calls():
    transport = FakeTransport(_sse(
        _tool_call_chunk(0, call_id="call_1", name="openclaw_agent_consult", args='{"question":'),
        _tool_call_chunk(0, args='"weather?"}'),
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ))
    events = list(stream_chat_completion_events(
        [{"role": "user", "content": "weather"}],
        LLMStageConfig(base_url="http://127.0.0.1:8000/v1"),
        transport=transport,
    ))
    assert events == [
        LLMStreamToolCalls([
            {
                "id": "call_1",
                "name": "openclaw_agent_consult",
                "arguments": '{"question":"weather?"}',
            }
        ])
    ]


def test_stream_chat_completion_retries_mistral_unsupported_thinking_knobs():
    payload = _sse(_chunk("Hello.", "stop"))
    calls = []

    def transport(url, *, data, headers, timeout):
        calls.append(json.loads(data))
        if len(calls) == 1:
            raise LLMClientError(
                "HTTP Error 400: chat_template is not supported for Mistral tokenizers."
            )
        if len(calls) == 2:
            raise LLMClientError(
                "HTTP Error 400: reasoning_effort=low is not supported by Mistral models."
            )
        return FakeResponse(payload)

    events = list(stream_chat_completion_events(
        [{"role": "user", "content": "hi"}],
        LLMStageConfig(base_url="http://127.0.0.1:8000/v1"),
        transport=transport,
    ))

    assert [event.text for event in events] == ["Hello."]
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in calls[1]
    assert calls[1]["reasoning_effort"] == "low"
    assert "chat_template_kwargs" not in calls[2]
    assert calls[2]["reasoning_effort"] == "none"


# --------------------------------------------------------------------------- #
# strip_tts_hostile / SentenceBatcher
# --------------------------------------------------------------------------- #


def test_sentence_batcher_preserves_separator_after_long_prefix_boundary():
    b = SentenceBatcher(max_chars=10)
    assert b.feed("one two three and ") == ["one two"]
    assert b.feed("then done") == ["three and"]
    assert b.flush() == "then done"


# --------------------------------------------------------------------------- #
# LLMStage: sentence-batched output + cancel_scope integration
# --------------------------------------------------------------------------- #
def _gen_request(turn_id="t1", generation=0, text="hi"):
    return GenerateRequest(turn_id=turn_id, turn_revision=0, generation=generation, text=text)


def test_llm_stage_history_is_bounded_by_turn_count_and_message_chars():
    calls = []

    def fake_stream(text, config, history=()):
        calls.append([dict(m) for m in history])
        yield "assistant reply for %s." % text

    stage = LLMStage(
        in_queue=None,
        config=LLMStageConfig(history_max_turns=1, history_max_message_chars=18),
        stream_fn=fake_stream,
    )

    list(stage.process(_gen_request(turn_id="t1", text="first turn with a very long body")))
    list(stage.process(_gen_request(turn_id="t2", text="second turn with a very long body")))
    list(stage.process(_gen_request(turn_id="t3", text="third turn")))

    assert calls[2][0]["role"] == "user"
    assert "second" in calls[2][0]["content"]
    assert "first" not in calls[2][0]["content"]
    assert len(calls[2]) == 2
    assert all(len(m["content"]) <= 18 for m in calls[2])


def test_llm_stage_trims_large_tool_outputs_without_changing_small_json():
    calls = []

    def fake_stream(text, config, history=()):
        calls.append([dict(m) for m in history])
        if len(calls) == 1:
            yield LLMStreamToolCalls([
                {"id": "call_1", "name": "openclaw_agent_consult", "arguments": "{}"}
            ])
        else:
            yield "Done."

    stage = LLMStage(
        in_queue=None,
        config=LLMStageConfig(tool_result_timeout=1.0, tool_result_max_chars=40),
        stream_fn=fake_stream,
    )
    gen = stage.process(_gen_request())
    assert isinstance(next(gen), LLMToolCall)
    assert stage.submit_tool_result("call_1", "a" * 80) is True
    list(gen)

    tool_message = calls[1][-1]
    assert tool_message["role"] == "tool"
    assert len(tool_message["content"]) == 40
    assert "truncated" in tool_message["content"]


# --------------------------------------------------------------------------- #
# Q1 (Opus gate, quality) -- a mid-turn exception must still terminate the
# turn on the wire (exactly one EndOfResponse), while a barge-in mid-turn
# (the test above) must stay silent -- no terminal from this stage at all.
# --------------------------------------------------------------------------- #
