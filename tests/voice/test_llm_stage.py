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

from anvil_serving.voice.cancel_scope import CancelScope
from anvil_serving.voice.messages import EndOfResponse, GenerateRequest, LLMChunk
from anvil_serving.voice.stages.llm import (
    LLMStage,
    LLMStageConfig,
    SentenceBatcher,
    build_request_body,
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
    out = stage.process(_gen_request())

    chunks = [m for m in out if isinstance(m, LLMChunk)]
    ends = [m for m in out if isinstance(m, EndOfResponse)]
    assert [c.text for c in chunks] == ["Hello there.", "How can I help you today?"]
    assert chunks[-1].is_final is True
    assert len(ends) == 1
    assert ends[0].turn_id == "t1"


def test_llm_stage_skips_already_stale_request():
    scope = CancelScope()
    scope.cancel()  # generation is now 1; a request tagged generation=0 is stale

    def fake_stream(text, config):
        raise AssertionError("must not stream for an already-stale request")

    stage = LLMStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    out = stage.process(_gen_request(generation=0))
    assert out is None


def test_llm_stage_stops_emitting_after_mid_stream_barge_in():
    scope = CancelScope()

    def fake_stream(text, config):
        yield "First sentence. "
        scope.cancel()  # simulate a barge-in landing mid-stream
        yield "Second sentence that must not be emitted. "
        yield "Third."

    stage = LLMStage(in_queue=None, cancel_scope=scope, stream_fn=fake_stream)
    out = stage.process(_gen_request(generation=0))

    texts = [m.text for m in (out or []) if isinstance(m, LLMChunk)]
    assert texts == ["First sentence."]
    # No EndOfResponse either -- the turn was superseded, not completed.
    assert not any(isinstance(m, EndOfResponse) for m in (out or []))


def test_llm_stage_ignores_non_generate_request_items():
    stage = LLMStage(in_queue=None, stream_fn=lambda t, c: iter(()))
    assert stage.process("not a GenerateRequest") is None
