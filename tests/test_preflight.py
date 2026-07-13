"""Tests for local-serve preflight validation."""

import json

from anvil_serving import preflight as pf


def test_response_observation_records_finish_and_reasoning_evidence():
    observation = pf.response_observation({
        "choices": [{
            "finish_reason": "length",
            "message": {"content": "", "reasoning_content": "still working"},
        }],
        "usage": {"completion_tokens_details": {"reasoning_tokens": 256}},
    })

    assert observation["finish_reason"] == "length"
    assert observation["content_chars"] == 0
    assert observation["content"] == ""
    assert observation["reasoning_field"] == "reasoning_content"
    assert observation["reasoning_chars"] == len("still working")
    assert observation["reasoning_tokens"] == 256


def test_preflight_main_records_model_controls_and_budget(monkeypatch, tmp_path):
    calls = []

    def fake_smoke(base, model, key, ctk, max_tokens, reasoning_effort, evidence):
        calls.append((ctk, max_tokens, reasoning_effort))
        evidence.append({"test": "smoke", "finish_reason": "stop", "reasoning_chars": 12})
        return True, "ok"

    monkeypatch.setattr(pf, "t_smoke", fake_smoke)
    out = tmp_path / "preflight.json"
    rc = pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "smoke", "--thinking-mode", "enabled",
        "--visible-answer-tokens", "256", "--reasoning-headroom-tokens", "4096",
        "--json-out", str(out),
    ])

    assert rc == 0
    assert calls == [({"enable_thinking": True}, 4352, None)]
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert b"\r\n" not in out.read_bytes()
    assert artifact["schema_version"] == "preflight/v2"
    assert artifact["thinking"]["mode"] == "enabled"
    assert artifact["budget"]["max_completion_tokens"] == 4352
    assert artifact["observations"][0]["finish_reason"] == "stop"


def test_preflight_rejects_length_finish_and_missing_required_reasoning(monkeypatch):
    def fake_smoke(base, model, key, ctk, max_tokens, reasoning_effort, evidence):
        evidence.append({
            "test": "smoke", "finish_reason": "length", "reasoning_chars": 0,
            "reasoning_tokens": None,
        })
        return True, "structural output happened to pass"

    monkeypatch.setattr(pf, "t_smoke", fake_smoke)
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "smoke", "--thinking-mode", "enabled",
        "--reasoning-headroom-tokens", "4096", "--reasoning-evidence", "required",
    ]) == 1


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
