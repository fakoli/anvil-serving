"""Focused tests for the local voice capture harness."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.voice import local_loop_demo


def test_capture_flag_without_value_uses_default_prefix():
    args = local_loop_demo.build_parser().parse_args(["--capture"])
    assert "anvil-voice-captures" in local_loop_demo.resolve_capture_prefix(args.capture)


def test_configured_auth_env_errors_reports_missing_env(monkeypatch):
    monkeypatch.delenv("ANVIL_ROUTER_TOKEN", raising=False)
    errors = local_loop_demo.configured_auth_env_errors({
        "voice": {"llm": {"api_key_env": "ANVIL_ROUTER_TOKEN"}, "stt": {}, "tts": {}}
    })
    assert errors == ["voice.llm.api_key_env names ANVIL_ROUTER_TOKEN, which is not set in the environment"]


def test_write_capture_records_configured_alias(tmp_path, monkeypatch):
    findings_doc = tmp_path / "findings.md"
    findings_doc.write_text(
        "## Session log\n\n| timestamp (UTC) | turns completed | barge-in observed? | avg TTFA (ms) | avg turn latency (ms) | route probe provider | mic recording | assistant recording | session JSON |\n|---|---:|---|---:|---:|---|---|---|---|\n| _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(local_loop_demo, "FINDINGS_DOC", findings_doc)
    turn = local_loop_demo.TurnMetric(0, "turn-1", 1, 1.0, 2.0, "hello", True, 0, 2)
    artifacts = local_loop_demo.write_capture(
        str(tmp_path / "proof"), [b"\x00\x00"], [b"\x01\x00"], 16000, 16000,
        [turn], [], "llm.voice", "test manifest",
    )
    assert all(Path(path).exists() for path in artifacts.values())
    session = json.loads(Path(artifacts["session_json"]).read_text(encoding="utf-8"))
    assert session["llm_model"] == "llm.voice"


def test_capture_acceptance_requires_barge_in_latency_and_audio():
    turn = local_loop_demo.TurnMetric(0, "turn-1", 1, 1.0, 2.0, "hello", True, 0, 2)
    assert local_loop_demo.capture_acceptance_passed("proof", [turn], [], 1, [b"\x00\x00"])
