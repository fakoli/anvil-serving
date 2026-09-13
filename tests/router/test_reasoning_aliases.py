"""OpenAI-compatible reasoning aliases share one response and history contract."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.internal import ModelDelta
from tests.router.helpers import make_tier
from tests.router.test_reasoning_content import _parse_openai_sse, _request
from tests.router.test_streaming_relay import FakeStreamTransport, _openai_sse


@pytest.mark.parametrize("mode", ["buffered", "sse", "json-stream-fallback"])
@pytest.mark.parametrize(("fields", "expected"), [
    ({"reasoning_content": "Canonical"}, "Canonical"),
    ({"reasoning": "Alternate"}, "Alternate"),
    ({"reasoning_text": "Text alias"}, "Text alias"),
    ({"reasoning_text": "Third", "reasoning": "Second", "reasoning_content": "First"},
     "First"),
    ({"reasoning_content": "", "reasoning": "Second", "reasoning_text": "Third"},
     "Second"),
    ({"reasoning_content": {"opaque": True}, "reasoning": [], "reasoning_text": "Third"},
     "Third"),
    ({"reasoning_content": None, "reasoning": False, "reasoning_text": 42}, None),
    ({"reasoning_content": " ", "reasoning": "Do not trim stream whitespace"}, " "),
])
def test_response_aliases_emit_one_canonical_field(mode, fields, expected):
    message = {"content": "Visible answer", **fields}
    upstream = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}).encode()
    backend_kwargs = {}
    if mode == "buffered":
        backend_kwargs["transport"] = lambda url, **kwargs: upstream
    else:
        payload = (_openai_sse({"choices": [{"delta": message, "finish_reason": "stop"}]})
                   if mode == "sse" else upstream)
        backend_kwargs["stream_transport"] = FakeStreamTransport(
            payload, "text/event-stream" if mode == "sse" else "application/json")
    backend = RelayBackend(make_tier("openai"), env={"EXAMPLE_KEY": "test"}, **backend_kwargs)
    request = _request(stream=mode != "buffered")
    deltas = list(backend.generate(request))
    structured = backend.get_last_structured()
    assert structured.reasoning == expected
    if mode == "buffered":
        assert "".join(deltas) == "Visible answer"
        outputs = [OpenAIDialect().render(request, "Visible answer", structured=structured)
                   ["choices"][0]["message"]]
    else:
        assert "".join(d.text or "" if isinstance(d, ModelDelta) else d for d in deltas) == (
            "Visible answer")
        chunks = _parse_openai_sse(b"".join(OpenAIDialect().stream(
            request, deltas, get_structured=backend.get_last_structured)))
        outputs = [c["choices"][0]["delta"] for c in chunks if c.get("choices")]
    assert [o["reasoning_content"] for o in outputs if "reasoning_content" in o] == (
        [] if expected is None else [expected])
    assert all("reasoning" not in o and "reasoning_text" not in o for o in outputs)


def test_stream_alias_changes_preserve_order_tool_fragments_and_usage():
    payload = _openai_sse(
        {"choices": [{"delta": {"reasoning": "First "}}]},
        {"choices": [{"delta": {"reasoning_text": "then ", "tool_calls": [{
            "index": 0, "id": "call-one", "function": {"name": "read", "arguments": '{"x":'}}]}}]},
        {"choices": [{"delta": {"reasoning_content": "last", "reasoning": "duplicate",
                                  "content": "Done", "tool_calls": [{
            "index": 0, "function": {"arguments": "1}"}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 12,
                                     "reasoning_tokens": 9}},
    )
    backend = RelayBackend(make_tier("openai"), env={"EXAMPLE_KEY": "test"},
                           stream_transport=FakeStreamTransport(payload))
    assert list(backend.generate(_request(stream=True))) == [
        ModelDelta(reasoning="First "), ModelDelta(reasoning="then "),
        ModelDelta(text="Done", reasoning="last"),
    ]
    result = backend.get_last_structured()
    assert result.reasoning == "First then last"
    assert result.tool_calls == [{"name": "read", "id": "call-one", "arguments": '{"x":1}'}]
    assert result.finish_reason == "tool_calls"
    assert result.usage == {"input_tokens": 8, "output_tokens": 12, "reasoning_tokens": 9}


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "reasoning_text"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("strip", [False, True])
def test_alias_only_continuation_reaches_wire_without_mutating_input(field, stream, strip):
    raw = {"model": "chat", "stream": stream, "messages": [
        {"role": "user", "content": "Continue."},
        {"role": "assistant", "content": None, field: "Prior reasoning"},
    ]}
    before = deepcopy(raw)
    captured = []

    def buffered(url, *, data, **kwargs):
        captured.append(json.loads(data))
        return json.dumps({"choices": [{"message": {"content": "Done", field: "New reasoning"}}]}).encode()

    streaming = FakeStreamTransport(_openai_sse(
        {"choices": [{"delta": {"content": "Done", field: "New reasoning"}}]}))
    backend = RelayBackend(replace(make_tier("openai"), strip_reasoning_history=strip),
                           env={"EXAMPLE_KEY": "test"}, transport=buffered,
                           stream_transport=streaming)
    list(backend.generate(OpenAIDialect().parse_request(raw)))
    body = (streaming.bodies if stream else captured)[0]
    assert body["messages"] == (before["messages"][:1] if strip else before["messages"])
    assert body["stream"] is stream
    assert backend.get_last_structured().reasoning == "New reasoning"
    assert raw == before


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "reasoning_text"])
def test_empty_history_alias_keeps_same_dialect_wire_fidelity(field):
    raw = {"model": "chat", "messages": [
        {"role": "assistant", "content": None, field: ""},
        {"role": "user", "content": "Continue."},
    ]}
    body = RelayBackend(make_tier("openai"), env={})._build_body(OpenAIDialect().parse_request(raw))
    assert body["messages"] == raw["messages"]


def test_alias_guard_after_overrides_removes_all_aliases_and_keeps_tool_history():
    messages = [
        {"role": "assistant", "content": None, "reasoning": "Second", "reasoning_text": "Third",
         "reasoning_content": "First", "tool_calls": [{"id": "one", "type": "function",
         "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "one", "content": "Result"},
        {"role": "assistant", "content": None, "reasoning": {"invalid": True},
         "reasoning_text": False, "reasoning_content": None},
    ]
    before = deepcopy(messages)
    tier = replace(make_tier("openai"), strip_reasoning_history=True,
                   extra_body={"messages": messages})
    body = RelayBackend(tier, env={})._build_body(_request())
    assert body["messages"] == [
        {"role": "assistant", "content": None, "tool_calls": messages[0]["tool_calls"]},
        messages[1],
    ]
    assert messages == before


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "reasoning_text"])
@pytest.mark.parametrize(("role", "value"), [("user", "Private"), ("assistant", {"invalid": True})])
def test_non_assistant_or_invalid_alias_does_not_enable_raw_history_replay(field, role, value):
    raw = {"model": "chat", "messages": [{"role": role, "content": "Visible", field: value}]}
    body = RelayBackend(make_tier("openai"), env={})._build_body(OpenAIDialect().parse_request(raw))
    assert body["messages"] == [{"role": role, "content": "Visible"}]


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "reasoning_text"])
def test_reasoning_alias_is_not_replayed_to_another_dialect(field):
    raw = {"model": "chat", "messages": [{"role": "assistant", "content": "Visible", field: "Private"}]}
    body = RelayBackend(make_tier("anthropic"), env={})._build_body(OpenAIDialect().parse_request(raw))
    assert body["messages"] == [{"role": "assistant", "content": "Visible"}]
