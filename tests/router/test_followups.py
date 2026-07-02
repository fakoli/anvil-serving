"""Follow-up hardening pins (architecture-review round 2).

Covers three changes:

* ``[router].profile_path`` — the live server loads a MEASURED profile.json
  instead of the hand-authored seeds, and fail-fasts on an unloadable one.
* real usage passthrough — the upstream's real token accounting reaches the
  rendered response instead of the word-count estimate.
* classifier haystack — a harness's multi-thousand-word standing system prompt
  no longer drowns the last user turn's intent in keyword multi-matches.
"""
from __future__ import annotations

import json
import textwrap

import pytest

from anvil_serving.router.backends.cloud import CloudBackend
from anvil_serving.router.classify import classify
from anvil_serving.router.config import ConfigError, Tier, load
from anvil_serving.router.dialects.anthropic import AnthropicDialect
from anvil_serving.router.dialects.openai import OpenAIDialect
from anvil_serving.router.internal import InternalRequest, Message, StructuredResult
from anvil_serving.router.serve import build_server


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _write_config(tmp_path, extra_router_lines: str = "") -> str:
    cfg = tmp_path / "config.toml"
    cfg.write_text(textwrap.dedent(f"""\
        [router]
        mapping_version = "test-1"
        {extra_router_lines}

        [[router.tiers]]
        id            = "fast-local"
        base_url      = "http://127.0.0.1:30001/v1"
        model         = "m"
        dialect       = "openai"
        context_limit = 32768
        privacy       = "local"
        tool_support  = true
        auth_env      = "ANVIL_FAST_LOCAL_KEY"

        [router.presets]
        chat = ["fast-local"]
        quick-edit = ["fast-local"]
        review = ["fast-local"]
        planning = ["fast-local"]
        long-context = ["fast-local"]
        """), encoding="utf-8")
    return str(cfg)


def _write_profile(tmp_path) -> str:
    """A minimal measured profile: fast-local is ALLOWED for planning."""
    doc = {
        "schema": "anvil-serving.router.profile_bootstrap/v1",
        "mode": "replay",
        "entries": [
            {"tier_id": "fast-local", "work_class": "planning",
             "decision": "allow", "quality_score": 0.91, "sample_n": 6,
             "last_measured": "2026-07-01T00:00:00Z"},
        ],
    }
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# [router].profile_path
# --------------------------------------------------------------------------- #
def test_profile_path_parses_and_expands(tmp_path):
    cfg = load(_write_config(tmp_path, 'profile_path = "./profile.json"'))
    assert cfg.profile_path == "./profile.json"
    assert load(_write_config(tmp_path)).profile_path is None


def test_profile_path_rejects_non_string(tmp_path):
    with pytest.raises(ConfigError):
        load(_write_config(tmp_path, "profile_path = 42"))
    with pytest.raises(ConfigError):
        load(_write_config(tmp_path, 'profile_path = ""'))


def test_build_server_loads_measured_profile(tmp_path):
    profile_path = _write_profile(tmp_path)
    config_path = _write_config(
        tmp_path, f'profile_path = "{profile_path}"'
    )
    httpd = build_server(config_path, port=0)
    try:
        routing = httpd.anvil_routing
        # The measured row replaced the seed verdict: planning on fast-local is
        # "allow" (the seed profile says "deny").
        assert routing._profile.decision("fast-local", "planning") == "allow"
        assert routing._profile.score("fast-local", "planning") == 0.91
    finally:
        httpd.server_close()


def test_build_server_fail_fast_on_unloadable_profile(tmp_path):
    config_path = _write_config(
        tmp_path, f'profile_path = "{tmp_path / "missing.json"}"'
    )
    with pytest.raises(ConfigError) as ei:
        build_server(config_path, port=0)
    assert "profile_path" in str(ei.value)


def test_build_server_without_profile_path_keeps_seeds(tmp_path):
    httpd = build_server(_write_config(tmp_path), port=0)
    try:
        routing = httpd.anvil_routing
        assert routing._profile.decision("fast-local", "planning") == "deny"
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- #
# real usage passthrough
# --------------------------------------------------------------------------- #
def _tier(dialect: str) -> Tier:
    return Tier(
        id="t", base_url="https://api.example.test", dialect=dialect,
        context_limit=200_000, privacy="cloud", tool_support=True,
        auth_env="EXAMPLE_KEY", model="m",
    )


def test_cloud_backend_extracts_openai_usage():
    backend = CloudBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"})
    raw = json.dumps({
        "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1234, "completion_tokens": 56},
    }).encode()
    s = backend._extract_structured(raw)
    assert s.usage == {"input_tokens": 1234, "output_tokens": 56}


def test_cloud_backend_extracts_anthropic_usage():
    backend = CloudBackend(_tier("anthropic"), env={"EXAMPLE_KEY": "k"})
    raw = json.dumps({
        "content": [{"type": "text", "text": "hi"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 777, "output_tokens": 42},
    }).encode()
    s = backend._extract_structured(raw)
    assert s.usage == {"input_tokens": 777, "output_tokens": 42}


def test_cloud_backend_usage_absent_or_malformed_is_none():
    backend = CloudBackend(_tier("openai"), env={"EXAMPLE_KEY": "k"})
    no_usage = json.dumps({"choices": [{"message": {"content": "x"}}]}).encode()
    assert backend._extract_structured(no_usage).usage is None
    bad = json.dumps({
        "choices": [{"message": {"content": "x"}}],
        "usage": {"prompt_tokens": "many", "completion_tokens": 5},
    }).encode()
    assert backend._extract_structured(bad).usage is None


def _request(dialect: str) -> InternalRequest:
    return InternalRequest(
        model="m", messages=[Message("user", "hello world")],
        max_tokens=64, dialect=dialect,
    )


def test_openai_render_prefers_real_usage():
    structured = StructuredResult(
        finish_reason="stop",
        usage={"input_tokens": 1234, "output_tokens": 56},
    )
    out = OpenAIDialect().render(_request("openai"), "hi", structured=structured)
    assert out["usage"] == {
        "prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290,
    }


def test_anthropic_render_prefers_real_usage():
    structured = StructuredResult(
        finish_reason="end_turn",
        usage={"input_tokens": 777, "output_tokens": 42},
    )
    out = AnthropicDialect().render(
        _request("anthropic"), "hi", structured=structured
    )
    assert out["usage"] == {"input_tokens": 777, "output_tokens": 42}


def test_anthropic_stream_prefers_real_output_tokens():
    structured = StructuredResult(
        finish_reason="end_turn",
        usage={"input_tokens": 777, "output_tokens": 42},
    )
    frames = b"".join(AnthropicDialect().stream(
        _request("anthropic"), iter(["one giant buffered delta"]),
        get_structured=lambda: structured,
    ))
    events = [json.loads(line[len(b"data: "):])
              for line in frames.split(b"\n") if line.startswith(b"data: ")]
    msg_delta = next(e for e in events if e.get("type") == "message_delta")
    assert msg_delta["usage"]["output_tokens"] == 42


def test_render_without_usage_keeps_estimates():
    out = OpenAIDialect().render(_request("openai"), "one two three")
    assert out["usage"]["completion_tokens"] == 3  # word-count estimate


# --------------------------------------------------------------------------- #
# classifier: long system prompts leave the keyword haystack
# --------------------------------------------------------------------------- #
def test_short_system_prompt_still_carries_intent():
    req = InternalRequest(
        model="x", messages=[Message("user", "")],
        system="You audit code for security issues",
    )
    c = classify(req)
    assert c.work_class == "review"
    assert c.confident is True


def test_long_harness_system_prompt_is_excluded_from_keywords():
    # A standing harness prompt: thousands of words, permanently containing
    # keywords from EVERY class. Before the fix this multi-matched on every
    # request and collapsed to an ambiguous top-priority class.
    harness_system = (
        "You are a coding agent. You can plan, review, refactor, edit, fix, "
        "audit, and implement changes across the codebase. " + ("word " * 400)
    )
    req = InternalRequest(
        model="x",
        messages=[Message("user", "fix the typo in README")],
        system=harness_system,
    )
    c = classify(req)
    assert c.work_class == "bounded-edit"
    assert c.confident is True
    assert c.signals["matched_keywords"] == ["bounded-edit"]
