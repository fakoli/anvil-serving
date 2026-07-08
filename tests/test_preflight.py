"""Tests for local-serve preflight validation."""

from anvil_serving import preflight as pf


def test_validate_tool_call_accepts_schema_valid_function_call():
    ok, detail = pf.validate_tool_call({
        "tool_calls": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": '{"city": "Oakland"}',
            },
        }]
    })

    assert ok is True
    assert "Oakland" in detail


def test_validate_tool_call_rejects_plain_text_claim():
    ok, detail = pf.validate_tool_call({
        "content": "I will call get_weather for Oakland."
    })

    assert ok is False
    assert detail == "response did not include tool_calls"


def test_validate_tool_call_rejects_missing_required_argument():
    ok, detail = pf.validate_tool_call({
        "tool_calls": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": "{}",
            },
        }]
    })

    assert ok is False
    assert "missing required string argument" in detail


def test_t_tool_one_rejects_text_only_response(monkeypatch):
    def fake_chat(*args, **kwargs):
        return {
            "choices": [{
                "message": {"content": "The weather in Oakland is sunny."}
            }]
        }, 0.01

    monkeypatch.setattr(pf, "chat", fake_chat)

    ok, detail = pf.t_tool_one(
        "http://127.0.0.1:30000/v1",
        "candidate",
        None,
        "shared prefix",
    )

    assert ok is False
    assert "did not include tool_calls" in detail
