"""An explicit tier policy protects clients that do not load request hooks."""
from copy import deepcopy


from anvil_serving.router.backends.relay import RelayBackend
from anvil_serving.router.config import _parse_tier
from anvil_serving.router.dialects.openai import OpenAIDialect


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
