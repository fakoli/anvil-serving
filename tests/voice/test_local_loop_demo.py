"""Focused tests for the local voice capture harness."""
from __future__ import annotations


from scripts.voice import local_loop_demo


def test_configured_auth_env_errors_reports_missing_env(monkeypatch):
    monkeypatch.delenv("ANVIL_ROUTER_TOKEN", raising=False)
    errors = local_loop_demo.configured_auth_env_errors({
        "voice": {"llm": {"api_key_env": "ANVIL_ROUTER_TOKEN"}, "stt": {}, "tts": {}}
    })
    assert errors == ["voice.llm.api_key_env names ANVIL_ROUTER_TOKEN, which is not set in the environment"]
