"""Tests for local-serve preflight validation."""

import json
import sys

import pytest

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

    def fake_smoke(base, model, key, ctk, max_tokens, reasoning_effort, evidence, timeout):
        calls.append((ctk, max_tokens, reasoning_effort))
        assert timeout == 60.0
        evidence.append({"test": "smoke", "finish_reason": "stop", "reasoning_chars": 12})
        return True, "ok"

    monkeypatch.setattr(pf, "t_smoke", fake_smoke)
    out = tmp_path / "preflight.json"
    rc = pf.main([
        "--base-url", "http://127.0.0.1:30000/v1", "--model", "candidate",
        "--checks", "smoke", "--thinking-mode", "enabled",
        "--visible-answer-tokens", "256", "--reasoning-headroom-tokens", "4096",
        "--timeout", "60",
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
    def fake_smoke(base, model, key, ctk, max_tokens, reasoning_effort, evidence, timeout):
        assert timeout == 60.0
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
        "--timeout", "60",
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


def test_preflight_dry_run_never_requests_or_writes(monkeypatch, tmp_path, capsys):
    def boom(*args, **kwargs):
        raise AssertionError("dry-run crossed a deferred boundary")

    monkeypatch.setattr(pf, "t_smoke", boom)
    monkeypatch.setattr(pf, "_atomic_write_json", boom)
    out = tmp_path / "preflight.json"
    assert pf.main([
        "--base-url", "http://127.0.0.1:30000/v1",
        "--model", "candidate",
        "--checks", "smoke",
        "--output", str(out),
        "--timeout", "15",
        "--dry-run",
    ]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["workload"] == "preflight"
    assert plan["timeout_seconds"] == 15.0
    assert not out.exists()


def test_preflight_rejects_invalid_output_before_live_probe(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("invalid output must fail before a live probe")

    monkeypatch.setattr(pf, "t_smoke", boom)
    out = tmp_path / "missing" / "preflight.json"
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "candidate",
            "--checks", "smoke",
            "--output", str(out),
        ])
    assert exc.value.code == 2


@pytest.mark.parametrize("flag,value", [
    ("--needle-ctx", "0"),
    ("--tool-batch", "129"),
    ("--timeout", "0"),
    ("--visible-answer-tokens", "0"),
])
def test_preflight_rejects_unsafe_bounds(flag, value):
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "candidate",
            flag, value,
        ])
    assert exc.value.code == 2


def test_preflight_rejects_missing_api_key_environment(monkeypatch):
    monkeypatch.delenv("MISSING_PREFLIGHT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "candidate",
            "--api-key-env", "MISSING_PREFLIGHT_TOKEN",
            "--dry-run",
        ])
    assert exc.value.code == 2


def test_preflight_rejects_wrong_model_family_control(capsys):
    with pytest.raises(SystemExit) as exc:
        pf.main([
            "--base-url", "http://127.0.0.1:30000/v1",
            "--model", "openai/gpt-oss-120b",
            "--thinking-mode", "disabled",
            "--dry-run",
        ])
    assert exc.value.code == 2
    assert "does not use Qwen" in capsys.readouterr().err


def test_console_safe_escapes_model_text_for_legacy_windows_console(monkeypatch):
    monkeypatch.setattr(sys, "stdout", type("LegacyStdout", (), {"encoding": "cp1252"})())
    assert pf._console_safe("model returned snowman \u2603") == (
        "model returned snowman \\u2603"
    )


def test_atomic_preflight_write_preserves_existing_target_on_replace_failure(
    monkeypatch, tmp_path
):
    out = tmp_path / "preflight.json"
    out.write_text("old evidence\n", encoding="utf-8")

    def fail_replace(*args, **kwargs):
        raise OSError("replace denied")

    monkeypatch.setattr(pf.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace denied"):
        pf._atomic_write_json(out, {"passed": True})

    assert out.read_text(encoding="utf-8") == "old evidence\n"
    assert list(tmp_path.glob(".preflight.json.*.tmp")) == []
