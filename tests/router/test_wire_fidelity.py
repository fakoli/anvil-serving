"""Wire fidelity for tool-carrying requests through the relay backends.

The relay rebuilds the upstream body from the flattened InternalRequest; before
this fix that dropped ``tools`` / ``tool_choice`` and the tool_use/tool_result
history an agent loop rides on. These tests pin:

* same-dialect passthrough (raw messages + tools forwarded verbatim);
* cross-dialect translation (Anthropic <-> OpenAI tool shapes);
* regression safety: a tool-free request builds the exact same body as before.
"""
from __future__ import annotations

import json

from anvil_serving.router.backends.cloud import CloudBackend
from anvil_serving.router.config import Tier
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.dialects.translate import (
    anthropic_messages_to_openai,
    anthropic_tool_choice_to_openai,
    anthropic_tools_to_openai,
    has_tool_artifacts,
    openai_messages_to_anthropic,
    openai_tool_choice_to_anthropic,
    openai_tools_to_anthropic,
)


def _tier(dialect: str, privacy: str = "cloud") -> Tier:
    return Tier(
        id=f"{dialect}-tier",
        base_url="https://api.example.test",
        dialect=dialect,
        context_limit=200_000,
        privacy=privacy,
        tool_support=True,
        auth_env="EXAMPLE_KEY",
        model="concrete-model",
    )


def _backend(dialect: str) -> CloudBackend:
    return CloudBackend(_tier(dialect), env={"EXAMPLE_KEY": "k"})


ANTHROPIC_TOOLS = [{
    "name": "get_weather",
    "description": "Get current weather",
    "input_schema": {"type": "object",
                     "properties": {"city": {"type": "string"}},
                     "required": ["city"]},
}]

OPENAI_TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string"}},
                       "required": ["city"]},
    },
}]


# --------------------------------------------------------------------------- #
# detector
# --------------------------------------------------------------------------- #
def test_has_tool_artifacts_detects_each_shape():
    assert has_tool_artifacts({"tools": ANTHROPIC_TOOLS})
    assert has_tool_artifacts({"tool_choice": "auto"})
    assert has_tool_artifacts({"messages": [{"role": "tool", "content": "42"}]})
    assert has_tool_artifacts({"messages": [
        {"role": "assistant", "tool_calls": [{"id": "c1"}]}]})
    assert has_tool_artifacts({"messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]})
    assert not has_tool_artifacts({"messages": [{"role": "user", "content": "hi"}]})
    assert not has_tool_artifacts({})


# --------------------------------------------------------------------------- #
# tool-definition translation
# --------------------------------------------------------------------------- #
def test_anthropic_tools_to_openai_shape():
    out = anthropic_tools_to_openai(ANTHROPIC_TOOLS)
    assert out == OPENAI_TOOLS


def test_openai_tools_to_anthropic_shape():
    out = openai_tools_to_anthropic(OPENAI_TOOLS)
    assert out == ANTHROPIC_TOOLS


def test_server_tools_and_malformed_entries_are_skipped():
    tools = [
        {"type": "web_search_20250305", "name": "web_search"},  # server tool
        {"no": "name"},
        "garbage",
    ] + ANTHROPIC_TOOLS
    out = anthropic_tools_to_openai(tools)
    assert out == OPENAI_TOOLS
    assert openai_tools_to_anthropic(["garbage", {"type": "function"}]) == []


def test_tool_choice_translation_both_ways():
    assert anthropic_tool_choice_to_openai({"type": "auto"}) == "auto"
    assert anthropic_tool_choice_to_openai({"type": "any"}) == "required"
    assert anthropic_tool_choice_to_openai({"type": "none"}) == "none"
    assert anthropic_tool_choice_to_openai(
        {"type": "tool", "name": "get_weather"}
    ) == {"type": "function", "function": {"name": "get_weather"}}
    assert anthropic_tool_choice_to_openai("auto") is None  # wrong shape

    assert openai_tool_choice_to_anthropic("auto") == {"type": "auto"}
    assert openai_tool_choice_to_anthropic("required") == {"type": "any"}
    assert openai_tool_choice_to_anthropic("none") == {"type": "none"}
    assert openai_tool_choice_to_anthropic(
        {"type": "function", "function": {"name": "get_weather"}}
    ) == {"type": "tool", "name": "get_weather"}
    assert openai_tool_choice_to_anthropic(123) is None


# --------------------------------------------------------------------------- #
# message-history translation
# --------------------------------------------------------------------------- #
def test_anthropic_history_to_openai_preserves_tool_traffic():
    messages = [
        {"role": "user", "content": "What's the weather in Oakland?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather",
             "input": {"city": "Oakland"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": [{"type": "text", "text": "68F sunny"}]},
        ]},
    ]
    out = anthropic_messages_to_openai(messages)
    assert out[0] == {"role": "user", "content": "What's the weather in Oakland?"}
    assistant = out[1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "Let me check."
    (tc,) = assistant["tool_calls"]
    assert tc["id"] == "toolu_1"
    assert tc["function"]["name"] == "get_weather"
    assert json.loads(tc["function"]["arguments"]) == {"city": "Oakland"}
    tool_msg = out[2]
    assert tool_msg == {"role": "tool", "tool_call_id": "toolu_1",
                        "content": "68F sunny"}


def test_openai_history_to_anthropic_preserves_tool_traffic():
    messages = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "weather in Oakland?"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "get_weather",
                          "arguments": '{"city": "Oakland"}'}},
        ]},
        {"role": "tool", "tool_call_id": "call_1", "content": "68F sunny"},
        {"role": "tool", "tool_call_id": "call_2", "content": "n/a"},
    ]
    out = openai_messages_to_anthropic(messages)
    # system dropped (carried on the top-level `system` field by the caller)
    assert out[0] == {"role": "user", "content": "weather in Oakland?"}
    assistant = out[1]
    assert assistant["role"] == "assistant"
    (block,) = assistant["content"]
    assert block["type"] == "tool_use"
    assert block["id"] == "call_1"
    assert block["input"] == {"city": "Oakland"}
    # consecutive tool messages merge into ONE user turn (strict alternation)
    results = out[2]
    assert results["role"] == "user"
    assert [b["tool_use_id"] for b in results["content"]] == ["call_1", "call_2"]
    assert all(b["type"] == "tool_result" for b in results["content"])


# --------------------------------------------------------------------------- #
# _build_body integration
# --------------------------------------------------------------------------- #
def test_same_dialect_openai_passthrough():
    body_in = {
        "model": "quick-edit",
        "messages": [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "68F"},
        ],
        "tools": OPENAI_TOOLS,
        "tool_choice": "auto",
    }
    request = OpenAIDialect().parse_request(body_in)
    body = _backend("openai")._build_body(request)
    assert body["tools"] == OPENAI_TOOLS
    assert body["tool_choice"] == "auto"
    assert body["messages"] == body_in["messages"]  # verbatim, tool traffic intact
    assert body["model"] == "concrete-model"


def test_same_dialect_anthropic_passthrough():
    body_in = {
        "model": "quick-edit",
        "max_tokens": 512,
        "system": "be brief",
        "messages": [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1", "content": "68F"},
                {"type": "text", "text": "and SF?"},
            ]},
        ],
        "tools": ANTHROPIC_TOOLS,
        "tool_choice": {"type": "auto"},
    }
    request = AnthropicDialect().parse_request(body_in)
    body = _backend("anthropic")._build_body(request)
    assert body["tools"] == ANTHROPIC_TOOLS
    assert body["tool_choice"] == {"type": "auto"}
    assert body["messages"] == body_in["messages"]
    assert body["system"] == "be brief"


def test_cross_dialect_anthropic_request_to_openai_tier():
    """The money path: Claude Code (Anthropic) -> local vLLM (OpenAI dialect)."""
    body_in = {
        "model": "quick-edit",
        "max_tokens": 512,
        "system": "be brief",
        "messages": [
            {"role": "user", "content": "weather in Oakland?"},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "get_weather",
                 "input": {"city": "Oakland"}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_1",
                 "content": "68F"}]},
        ],
        "tools": ANTHROPIC_TOOLS,
        "tool_choice": {"type": "any"},
    }
    request = AnthropicDialect().parse_request(body_in)
    body = _backend("openai")._build_body(request)
    assert body["tools"] == OPENAI_TOOLS
    assert body["tool_choice"] == "required"
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert body["messages"][3]["tool_call_id"] == "toolu_1"


def test_cross_dialect_openai_request_to_anthropic_tier():
    body_in = {
        "model": "quick-edit",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "weather?"},
        ],
        "tools": OPENAI_TOOLS,
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
    }
    request = OpenAIDialect().parse_request(body_in)
    body = _backend("anthropic")._build_body(request)
    assert body["tools"] == ANTHROPIC_TOOLS
    assert body["tool_choice"] == {"type": "tool", "name": "get_weather"}
    # system message dropped from messages; carried on the top-level field
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert body["system"] == "be brief"


def test_tool_free_request_body_is_unchanged():
    """Regression pin: without tool artifacts the body matches the old shape."""
    body_in = {
        "model": "chat",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello there"},
        ],
        "temperature": 0.5,
        "max_tokens": 64,
    }
    request = OpenAIDialect().parse_request(body_in)
    body = _backend("openai")._build_body(request)
    assert body == {
        "model": "concrete-model",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello there"},
        ],
        "stream": False,
        "max_tokens": 64,
        "temperature": 0.5,
    }
    assert "tools" not in body and "tool_choice" not in body
