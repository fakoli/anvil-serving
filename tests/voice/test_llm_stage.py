"""Tests for anvil_serving.voice.stages.llm -- the OpenAI Chat Completions
LLM stage.

Dependency-light and hermetic: no real sockets are opened. ``stream_chat_completion``
is exercised through an injectable fake ``transport`` (mirroring
``CloudBackend``'s ``stream_transport`` seam in
``anvil_serving/router/backends/cloud.py``/``tests/router/test_streaming_relay.py``)
that returns a canned in-memory SSE response, never a live ``urllib`` connection.
No GPU, no torch, no network.
"""
from __future__ import annotations

import io
import json

import pytest

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import EndOfResponse, GenerateRequest, LLMChunk, LLMToolCall
from anvil_serving.voice.stages.llm import (
    LLMStage,
    LLMStageConfig,
    LLMStreamToolCalls,
    SentenceBatcher,
    build_request_body,
    stream_chat_completion_events,
    stream_chat_completion,
    strip_tts_hostile,
)


# --------------------------------------------------------------------------- #
# fakes: an in-memory transport, mirroring test_streaming_relay.py's DI style
# --------------------------------------------------------------------------- #
class FakeResponse:
    """Line-iterable fake of an open urllib response (no socket)."""

    def __init__(self, payload: bytes):
        self._fp = io.BytesIO(payload)
        self.closed = False

    def __iter__(self):
        return iter(self._fp)

    def close(self) -> None:
        self.closed = True


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
def test_build_request_body_defaults_to_chat_fast_preset():
    body = build_request_body("hi", LLMStageConfig())
    assert body["model"] == "chat-fast"


def test_build_request_body_never_targets_responses_api():
    # There is no field named "responses" anywhere and no code path that could
    # select it -- the URL itself is built by stream_chat_completion (below).
    body = build_request_body("hi", LLMStageConfig())
    assert "response" not in json.dumps(body).lower()


def test_build_request_body_sends_thinking_disable_directive():
    body = build_request_body("hi", LLMStageConfig())
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["reasoning_effort"] == "low"


def test_build_request_body_carries_modality_marker():
    body = build_request_body("hi", LLMStageConfig())
    assert body["modality"] == "voice"


def test_build_request_body_is_streaming():
    body = build_request_body("hi", LLMStageConfig())
    assert body["stream"] is True
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_build_request_body_includes_prior_turn_history_before_current_user():
    body = build_request_body(
        "what is my zip?",
        LLMStageConfig(system_prompt="answer briefly"),
        history=[
            {"role": "user", "content": "my zip is 90210"},
            {"role": "assistant", "content": "Got it."},
        ],
    )
    assert body["messages"] == [
        {"role": "system", "content": "answer briefly"},
        {"role": "user", "content": "my zip is 90210"},
        {"role": "assistant", "content": "Got it."},
        {"role": "user", "content": "what is my zip?"},
    ]


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


def test_build_request_body_supports_system_prompt_and_temperature():
    body = build_request_body(
        "hi",
        LLMStageConfig(system_prompt="answer briefly", temperature=0.0, max_tokens=8),
    )
    assert body["messages"] == [
        {"role": "system", "content": "answer briefly"},
        {"role": "user", "content": "hi"},
    ]
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 8


def test_build_request_body_modality_can_be_disabled():
    body = build_request_body("hi", LLMStageConfig(modality=None))
    assert "modality" not in body


# --------------------------------------------------------------------------- #
# stream_chat_completion: URL construction + SSE assembly (hermetic)
# --------------------------------------------------------------------------- #
def test_stream_chat_completion_posts_to_chat_completions_path():
    transport = FakeTransport(_sse(_chunk("Hello"), _chunk(" world", "stop")))
    config = LLMStageConfig(base_url="http://127.0.0.1:8000/v1")
    deltas = list(stream_chat_completion("hi", config, transport=transport))
    assert deltas == ["Hello", " world"]
    assert transport.calls[0]["url"] == "http://127.0.0.1:8000/v1/chat/completions"
    assert not transport.calls[0]["url"].endswith("/responses")


def test_stream_chat_completion_sends_history_messages():
    transport = FakeTransport(_sse(_chunk("ok", "stop")))
    config = LLMStageConfig(base_url="http://127.0.0.1:8000/v1")
    list(stream_chat_completion(
        "what did I say?",
        config,
        history=[
            {"role": "user", "content": "my zip is 90210"},
            {"role": "assistant", "content": "Got it."},
        ],
        transport=transport,
    ))
    assert transport.calls[0]["body"]["messages"] == [
        {"role": "user", "content": "my zip is 90210"},
        {"role": "assistant", "content": "Got it."},
        {"role": "user", "content": "what did I say?"},
    ]


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


def test_stream_chat_completion_never_responses_api():
    # Even with a trailing slash on base_url, the built path is chat/completions.
    transport = FakeTransport(_sse(_chunk("hi", "stop")))
    config = LLMStageConfig(base_url="http://127.0.0.1:8000/v1/")
    list(stream_chat_completion("hi", config, transport=transport))
    assert transport.calls[0]["url"] == "http://127.0.0.1:8000/v1/chat/completions"


def test_stream_chat_completion_closes_response():
    transport = FakeTransport(_sse(_chunk("hi", "stop")))
    list(stream_chat_completion("hi", LLMStageConfig(), transport=transport))
    assert transport.response.closed


def test_stream_chat_completion_sends_bearer_token_from_env_var(monkeypatch):
    monkeypatch.setenv("ANVIL_TEST_VOICE_TOKEN", "secret-token")
    transport = FakeTransport(_sse(_chunk("hi", "stop")))
    config = LLMStageConfig(api_key_env="ANVIL_TEST_VOICE_TOKEN")
    list(stream_chat_completion("hi", config, transport=transport))
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-token"


def test_stream_chat_completion_strips_bearer_token_whitespace(monkeypatch):
    monkeypatch.setenv("ANVIL_TEST_VOICE_TOKEN", " secret-token\r\n")
    transport = FakeTransport(_sse(_chunk("hi", "stop")))
    config = LLMStageConfig(api_key_env="ANVIL_TEST_VOICE_TOKEN")
    list(stream_chat_completion("hi", config, transport=transport))
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer secret-token"


def test_stream_chat_completion_no_token_when_env_unset(monkeypatch):
    monkeypatch.delenv("ANVIL_TEST_VOICE_TOKEN_UNSET", raising=False)
    transport = FakeTransport(_sse(_chunk("hi", "stop")))
    config = LLMStageConfig(api_key_env="ANVIL_TEST_VOICE_TOKEN_UNSET")
    list(stream_chat_completion("hi", config, transport=transport))
    assert "Authorization" not in transport.calls[0]["headers"]


# --------------------------------------------------------------------------- #
# strip_tts_hostile / SentenceBatcher
# --------------------------------------------------------------------------- #
def test_strip_tts_hostile_removes_markdown_markers():
    assert strip_tts_hostile("**bold** and `code` and # heading") == "bold and code and  heading"


def test_strip_tts_hostile_removes_bullets():
    assert strip_tts_hostile("- one\n- two").strip() == "one\ntwo"


def test_sentence_batcher_splits_on_sentence_end():
    b = SentenceBatcher()
    assert b.feed("Hello there. How are") == ["Hello there."]
    assert b.feed(" you? Fine!") == ["How are you?"]
    assert b.flush() == "Fine!"


def test_sentence_batcher_flush_none_when_buffer_fully_consumed():
    b = SentenceBatcher()
    b.feed("One sentence. ")  # trailing space after the terminator: buffer empties
    assert b.flush() is None


def test_sentence_batcher_flush_returns_trailing_partial_sentence():
    b = SentenceBatcher()
    assert b.feed("no terminator yet") == []
    assert b.flush() == "no terminator yet"


def test_sentence_batcher_strips_tts_hostile_chars_from_output():
    b = SentenceBatcher()
    out = b.feed("**Hello** there. ")
    assert out == ["Hello there."]


def test_sentence_batcher_splits_long_prefix_without_sentence_end():
    b = SentenceBatcher(max_chars=20)
    assert b.feed("This answer is long enough") == ["This answer is long"]
    assert b.flush() == "enough"


def test_sentence_batcher_preserves_separator_after_long_prefix_boundary():
    b = SentenceBatcher(max_chars=10)
    assert b.feed("one two three and ") == ["one two"]
    assert b.feed("then done") == ["three and"]
    assert b.flush() == "then done"


def test_sentence_batcher_hard_splits_long_tokens_at_bound():
    b = SentenceBatcher(max_chars=10)
    assert b.feed("abcdefghijklmno rest") == ["abcdefghij"]
    assert b.flush() == "klmno rest"


def test_sentence_batcher_avoids_tiny_trailing_split_tail():
    b = SentenceBatcher(max_chars=56)
    text = "aaaaaaaaaa bbbbbbbbbb cccccccccc dddddddddd eeeeeeeeee fffff"

    assert b.feed(text) == ["aaaaaaaaaa bbbbbbbbbb cccccccccc dddddddddd"]
    assert b.flush() == "eeeeeeeeee fffff"


# --------------------------------------------------------------------------- #
# LLMStage: sentence-batched output + cancel_scope integration
# --------------------------------------------------------------------------- #
def _gen_request(turn_id="t1", generation=0, text="hi"):
    return GenerateRequest(turn_id=turn_id, turn_revision=0, generation=generation, text=text)


def test_llm_stage_emits_sentence_batched_chunks_then_end_of_response():
    scope = CancelScope()

    def fake_stream(text, config):
        yield "Hello there. "
        yield "How can I help "
        yield "you today?"

    stage = LLMStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)  # queues unused directly
    # process() is a generator now (PUNCH-LIST #1: incremental emission) --
    # materialize it ONCE so both list comprehensions below see every item.
    out = list(stage.process(_gen_request()))

    chunks = [m for m in out if isinstance(m, LLMChunk)]
    ends = [m for m in out if isinstance(m, EndOfResponse)]
    assert [c.text for c in chunks] == ["Hello there.", "How can I help you today?"]
    assert chunks[-1].is_final is True
    assert len(ends) == 1
    assert ends[0].turn_id == "t1"


def test_llm_stage_splits_long_speech_chunks_on_word_boundaries():
    def fake_stream(text, config):
        yield "This answer is long enough"

    stage = LLMStage(
        in_queue=None,
        config=LLMStageConfig(speech_chunk_max_chars=20),
        stream_fn=fake_stream,
    )

    out = list(stage.process(_gen_request()))

    chunks = [m for m in out if isinstance(m, LLMChunk)]
    assert [c.text for c in chunks] == ["This answer is long", "enough"]
    assert chunks[0].is_final is False
    assert chunks[1].is_final is True
    assert any(isinstance(m, EndOfResponse) for m in out)


def test_llm_stage_remembers_completed_turns_for_next_request():
    calls = []

    def fake_stream(text, config, history=()):
        calls.append({"text": text, "history": [dict(m) for m in history]})
        if text == "my zip is 90210":
            yield "Got it."
        else:
            yield "Your zip is 90210."

    stage = LLMStage(in_queue=None, stream_fn=fake_stream)

    list(stage.process(_gen_request(turn_id="t1", text="my zip is 90210")))
    list(stage.process(_gen_request(turn_id="t2", text="what is my zip?")))

    assert calls[0]["history"] == []
    assert calls[1]["history"] == [
        {"role": "user", "content": "my zip is 90210"},
        {"role": "assistant", "content": "Got it."},
    ]


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


def test_llm_stage_history_can_be_disabled():
    calls = []

    def fake_stream(text, config, history=()):
        calls.append([dict(m) for m in history])
        yield "ok."

    stage = LLMStage(
        in_queue=None,
        config=LLMStageConfig(history_max_turns=0),
        stream_fn=fake_stream,
    )

    list(stage.process(_gen_request(turn_id="t1", text="remember this")))
    list(stage.process(_gen_request(turn_id="t2", text="what did I say?")))

    assert calls == [[], []]


def test_llm_stage_pauses_for_tool_result_then_resumes_final_answer():
    calls = []

    def fake_stream(text, config, history=()):
        calls.append([dict(m) for m in history])
        if len(calls) == 1:
            yield LLMStreamToolCalls([
                {
                    "id": "call_1",
                    "name": "openclaw_agent_consult",
                    "arguments": '{"question":"weather"}',
                }
            ])
        else:
            yield "It is sunny."

    stage = LLMStage(
        in_queue=None,
        config=LLMStageConfig(tool_result_timeout=1.0),
        stream_fn=fake_stream,
    )
    gen = stage.process(_gen_request(text="what is the weather?"))

    first = next(gen)
    assert isinstance(first, LLMToolCall)
    assert first.call_id == "call_1"
    assert first.name == "openclaw_agent_consult"

    assert stage.submit_tool_result("call_1", '{"text":"Sunny and 72."}') is True
    rest = list(gen)

    chunks = [m for m in rest if isinstance(m, LLMChunk)]
    assert [c.text for c in chunks] == ["It is sunny."]
    assert any(isinstance(m, EndOfResponse) for m in rest)
    assert calls[1] == [
        {"role": "user", "content": "what is the weather?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "openclaw_agent_consult",
                        "arguments": '{"question":"weather"}',
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"text":"Sunny and 72."}'},
    ]


def test_llm_stage_suppresses_tools_for_openclaw_forced_consult_speech_turn():
    calls = []
    forced_speech_prompt = "\n".join([
        "OpenClaw finished checking. Speak this result naturally and concisely.",
        "Do not mention tool calls, JSON, or internal routing.",
        "",
        "The weather is sunny and 72.",
    ])

    def fake_stream(text, config, history=()):
        calls.append({"text": text, "tools": config.tools, "tool_choice": config.tool_choice})
        if config.tools:
            yield LLMStreamToolCalls([
                {"id": "call_1", "name": "openclaw_agent_consult", "arguments": "{}"}
            ])
        else:
            yield "The weather is sunny and 72."

    stage = LLMStage(
        in_queue=None,
        config=LLMStageConfig(
            tools=[{"type": "function", "name": "openclaw_agent_consult"}],
            tool_choice="auto",
        ),
        stream_fn=fake_stream,
    )

    out = list(stage.process(_gen_request(text=forced_speech_prompt)))

    assert calls == [{"text": forced_speech_prompt, "tools": None, "tool_choice": None}]
    assert not any(isinstance(m, LLMToolCall) for m in out)
    assert [m.text for m in out if isinstance(m, LLMChunk)] == ["The weather is sunny and 72."]
    assert any(isinstance(m, EndOfResponse) for m in out)


def test_llm_stage_ignores_will_continue_tool_result_until_final_result():
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
        config=LLMStageConfig(tool_result_timeout=1.0),
        stream_fn=fake_stream,
    )
    gen = stage.process(_gen_request())
    assert isinstance(next(gen), LLMToolCall)
    assert stage.submit_tool_result("call_1", '{"status":"working"}', will_continue=True) is True
    assert stage.submit_tool_result("call_1", '{"text":"final"}') is True
    out = list(gen)

    assert [m.text for m in out if isinstance(m, LLMChunk)] == ["Done."]
    assert calls[-1][-1] == {"role": "tool", "tool_call_id": "call_1", "content": '{"text":"final"}'}


def test_llm_stage_rejects_tool_results_without_matching_pending_call():
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
        config=LLMStageConfig(tool_result_timeout=1.0),
        stream_fn=fake_stream,
    )

    assert stage.submit_tool_result("call_1", '{"too":"early"}') is False
    gen = stage.process(_gen_request())
    assert isinstance(next(gen), LLMToolCall)
    assert stage.submit_tool_result("stale_call", '{"wrong":"turn"}') is False
    assert stage.submit_tool_result("call_1", '{"text":"final"}') is True
    out = list(gen)

    assert [m.text for m in out if isinstance(m, LLMChunk)] == ["Done."]


def test_llm_stage_preserves_pre_tool_text_in_continuation_prompt():
    calls = []

    def fake_stream(text, config, history=()):
        calls.append([dict(m) for m in history])
        if len(calls) == 1:
            yield "Let me check"
            yield LLMStreamToolCalls([
                {"id": "call_1", "name": "openclaw_agent_consult", "arguments": "{}"}
            ])
        else:
            yield "It is sunny."

    stage = LLMStage(
        in_queue=None,
        config=LLMStageConfig(tool_result_timeout=1.0),
        stream_fn=fake_stream,
    )
    gen = stage.process(_gen_request(text="weather?"))

    first = next(gen)
    assert isinstance(first, LLMChunk)
    assert first.text == "Let me check"
    second = next(gen)
    assert isinstance(second, LLMToolCall)
    assert stage.submit_tool_result("call_1", '{"text":"Sunny"}') is True
    list(gen)

    assert calls[1][1]["content"] == "Let me check"


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


def test_configure_realtime_session_ignores_client_model_override():
    stage = LLMStage(
        in_queue=None,
        config=LLMStageConfig(model="fast-local", system_prompt="Manifest prompt."),
        stream_fn=lambda text, config: iter(()),
    )

    stage.configure_realtime_session({
        "model": "gpt-realtime",
        "instructions": "Session prompt.",
        "tools": [{"type": "function", "name": "openclaw_agent_consult"}],
        "tool_choice": "auto",
    })

    assert stage.config.model == "fast-local"
    assert stage.config.system_prompt == "Manifest prompt.\n\nSession prompt."
    assert stage.config.tool_choice == "auto"


def test_llm_stage_skips_already_stale_request():
    scope = CancelScope()
    scope.cancel()  # generation is now 1; a request tagged generation=0 is stale

    def fake_stream(text, config):
        raise AssertionError("must not stream for an already-stale request")

    stage = LLMStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    out = list(stage.process(_gen_request(generation=0)))
    assert out == []


def test_llm_stage_stops_emitting_after_mid_stream_barge_in():
    scope = CancelScope()

    def fake_stream(text, config):
        yield "First sentence. "
        scope.cancel()  # simulate a barge-in landing mid-stream
        yield "Second sentence that must not be emitted. "
        yield "Third."

    stage = LLMStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    out = list(stage.process(_gen_request(generation=0)))

    texts = [m.text for m in out if isinstance(m, LLMChunk)]
    assert texts == ["First sentence."]
    # No EndOfResponse either -- the turn was superseded, not completed.
    assert not any(isinstance(m, EndOfResponse) for m in out)


def test_llm_stage_ignores_non_generate_request_items():
    stage = LLMStage(in_queue=None, stream_fn=lambda t, c: iter(()))
    assert list(stage.process("not a GenerateRequest")) == []


# --------------------------------------------------------------------------- #
# Q1 (Opus gate, quality) -- a mid-turn exception must still terminate the
# turn on the wire (exactly one EndOfResponse), while a barge-in mid-turn
# (the test above) must stay silent -- no terminal from this stage at all.
# --------------------------------------------------------------------------- #
def test_llm_stage_emits_exactly_one_terminal_on_mid_stream_exception():
    """Before the Q1 fix, a mid-turn exception (e.g. the streaming HTTP call
    dropping partway through) propagated straight out of `process()` with NO
    terminal `EndOfResponse` ever yielded -- a client downstream would hang on
    an unterminated turn. The fix must emit exactly one terminal on the way
    out, then still let the exception propagate (so `BaseStage._run`'s
    per-item exception isolation keeps logging real failures)."""
    scope = CancelScope()

    def fake_stream(text, config):
        yield "First sentence. "
        raise RuntimeError("simulated upstream failure")

    stage = LLMStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    gen = stage.process(_gen_request())

    out = []
    with pytest.raises(RuntimeError, match="simulated upstream failure"):
        for item in gen:
            out.append(item)

    chunks = [m for m in out if isinstance(m, LLMChunk)]
    ends = [m for m in out if isinstance(m, EndOfResponse)]
    assert [c.text for c in chunks] == ["First sentence."]
    assert len(ends) == 1
    assert ends[0].turn_id == "t1"
