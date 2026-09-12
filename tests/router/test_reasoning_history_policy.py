"""An explicit tier policy protects clients that do not load request hooks."""
from copy import deepcopy
import json

import pytest

from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import ConfigError, _parse_tier
from anvil_serving.router.dialects.openai import OpenAIDialect
from tests.router.test_streaming_relay import FakeStreamTransport, _openai_sse


def tier(**overrides):
    return _parse_tier(dict(id="primary", model="qualified-model", dialect="openai",
                           base_url="http://127.0.0.1:9001/v1", context_limit=100000,
                           privacy="local", tool_support=True, auth_env="TEST_KEY", **overrides))


def test_history_policy_is_explicit_and_preserves_tool_continuation():
    raw = {"model": "llm.primary", "reasoning_effort": "max", "max_tokens": 4096,
           "tools": [{"type": "function", "function": {"name": "read"}}], "messages": [
               {"role": "user", "content": "Inspect the file."},
               {"role": "assistant", "content": "Reading it.", "reasoning_content": " the =" * 1000,
                "tool_calls": [{"id": "one", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
               {"role": "tool", "tool_call_id": "one", "content": "file evidence"},
               {"role": "assistant", "content": "Visible decision", "reasoning": "old", "reasoning_text": "old"},
               {"role": "assistant", "content": None, "reasoning_content": "unfinished"},
           ]}
    before = deepcopy(raw)
    for stream in (False, True):
        raw["stream"] = stream
        request = OpenAIDialect().parse_request(raw)
        unchanged = RelayBackend(tier(), env={})._build_body(request)
        assert unchanged["messages"] == before["messages"]
        backend = RelayBackend(tier(strip_reasoning_history=True), env={})
        body = backend._build_body(request)
        assert len(body["messages"]) == 4
        assert body["messages"][1]["tool_calls"] == raw["messages"][1]["tool_calls"]
        assert body["messages"][2] == raw["messages"][2]
        assert body["messages"][3] == {"role": "assistant", "content": "Visible decision"}
        assert "reasoning_content" not in body["messages"][1]
        assert body["reasoning_effort"] == "max"
        assert body["max_tokens"] == 4096
        assert body["tools"] == raw["tools"]
        assert raw["messages"] == before["messages"]


@pytest.mark.parametrize("value", ["true", 1, None, {}])
def test_history_policy_rejects_non_boolean(value):
    with pytest.raises(ConfigError, match="must be a boolean"):
        tier(strip_reasoning_history=value)


def test_history_policy_rejects_anthropic_endpoint():
    raw = dict(id="primary", model="qualified-model", dialect="anthropic",
               base_url="http://127.0.0.1:9001", context_limit=100000,
               privacy="local", tool_support=True, auth_env="TEST_KEY", strip_reasoning_history=True)
    with pytest.raises(ConfigError, match="requires an openai endpoint"):
        _parse_tier(raw)


def test_legacy_function_calls_and_opaque_reasoning_are_preserved():
    messages = [
        {"role": "assistant", "content": None, "reasoning_content": "old",
         "function_call": {"name": "read", "arguments": "{}"}},
        {"role": "function", "name": "read", "content": "result"},
        {"role": "assistant", "content": None, "reasoning_content": "old",
         "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}]},
    ]
    request = OpenAIDialect().parse_request({"model": "llm.primary", "messages": messages})
    body = RelayBackend(tier(strip_reasoning_history=True), env={})._build_body(request)
    assert len(body["messages"]) == 3
    assert body["messages"][0]["function_call"] == messages[0]["function_call"]
    assert body["messages"][1] == messages[1]
    assert body["messages"][2]["reasoning_details"] == messages[2]["reasoning_details"]
    assert all("reasoning_content" not in m for m in body["messages"])


@pytest.mark.parametrize("messages", [None, {"custom": "override"}, [None, "invalid"]])
def test_history_policy_preserves_existing_extra_body_override_contract(messages):
    request = OpenAIDialect().parse_request({"model": "llm.primary", "messages": [{"role": "user", "content": "Hi"}]})
    body = RelayBackend(tier(strip_reasoning_history=True, extra_body={"messages": messages}),
                        env={})._build_body(request)
    assert body["messages"] == messages


@pytest.mark.parametrize("stream", [False, True])
def test_chat_only_policy_reaches_wire_and_preserves_new_reasoning(stream):
    messages = [{"role": "user", "content": "Continue."},
                {"role": "assistant", "content": "Prior answer", "reasoning_content": "old"}]
    raw = {"model": "llm.primary", "messages": messages, "stream": stream}
    captured = []

    def buffered(url, *, data, headers, timeout):
        captured.append(json.loads(data))
        return json.dumps({"choices": [{"message": {"content": "Done", "reasoning_content": "new"},
                                       "finish_reason": "stop"}]}).encode()

    streaming = FakeStreamTransport(_openai_sse(
        {"choices": [{"delta": {"content": "Done", "reasoning_content": "new"},
                      "finish_reason": "stop"}]}))
    # The policy runs after operator overrides too; an override cannot reinsert old reasoning.
    backend = RelayBackend(tier(strip_reasoning_history=True, extra_body={"messages": messages}),
                           env={}, transport=buffered, stream_transport=streaming)
    list(backend.generate(OpenAIDialect().parse_request(raw)))
    body = (streaming.bodies if stream else captured)[0]
    assert body["messages"] == [messages[0], {"role": "assistant", "content": "Prior answer"}]
    assert body["stream"] is stream
    assert backend.get_last_structured().reasoning == "new"
    assert messages[1]["reasoning_content"] == "old"
