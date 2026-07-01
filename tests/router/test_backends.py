"""Backend-construction tests for genericity F001 (T005 / T003 / T002).

Hermetic and stdlib-only: every test injects a fake transport, never touching a
real network.

  * T005 — [router].relay_timeout is threaded through build_backends ->
    build_backend_for_tier so a LOCAL tier's backend actually uses it as its
    transport timeout (cloud tiers keep the 120s default).
  * T003 — a tier's extra_body is merged verbatim into the upstream request
    body (both dialects); absent extra_body is a no-op (no regression).
"""
from __future__ import annotations

import json
from typing import Dict

import pytest

from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.serve import build_backend_for_tier, build_backends


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _local_tier(**overrides) -> Tier:
    base = dict(
        id="fast-local", base_url="http://127.0.0.1:30001/v1", dialect="openai",
        context_limit=32768, privacy="local", tool_support=True,
        auth_env="ANVIL_FAST_LOCAL_KEY", model="served-model",
    )
    base.update(overrides)
    return Tier(**base)


def _cloud_tier(**overrides) -> Tier:
    base = dict(
        id="cloud", base_url="https://api.anthropic.com", dialect="anthropic",
        context_limit=200000, privacy="cloud", tool_support=True,
        auth_env="ANVIL_TEST_CLOUD_KEY", model="claude-opus-4-20250514",
    )
    base.update(overrides)
    return Tier(**base)


def _config(*tiers: Tier, **overrides) -> RouterConfig:
    kwargs: Dict[str, object] = dict(
        tiers=tuple(tiers),
        presets={"chat": tuple(t.id for t in tiers)},
        mapping_version="test.0",
    )
    kwargs.update(overrides)
    return RouterConfig(**kwargs)


def _post_fake(response_body: bytes):
    """A fake POST transport(url, *, data, headers, timeout) capturing the call."""
    captured: Dict[str, object] = {}

    def fake(url, *, data, headers, timeout):
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["body"] = json.loads(data)
        captured["timeout"] = timeout
        return response_body

    return fake, captured


# --------------------------------------------------------------------------- #
# T005 — configurable relay timeout, plumbed through build_backends
# --------------------------------------------------------------------------- #
def test_relay_timeout_plumbed_through_build_backends_to_local_backend():
    """build_backends threads config.relay_timeout into a LOCAL tier's backend."""
    config = _config(_local_tier(), relay_timeout=5.0)
    backends, skipped = build_backends(config, env={})
    assert not skipped
    assert backends["fast-local"]._timeout == pytest.approx(5.0)


def test_relay_timeout_plumbed_does_not_affect_cloud_backend():
    """A cloud tier keeps the 120s cloud-tuned default even when relay_timeout
    is set short — relay_timeout only governs LOCAL tiers."""
    config = _config(
        _local_tier(), _cloud_tier(),
        relay_timeout=5.0,
    )
    backends, skipped = build_backends(
        config, env={"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}
    )
    assert not skipped
    assert backends["fast-local"]._timeout == pytest.approx(5.0)
    assert backends["cloud"]._timeout == pytest.approx(120.0)


def test_relay_timeout_default_is_20s_end_to_end():
    """No explicit relay_timeout in config -> RouterConfig default (20s) is what
    build_backends actually threads through (not the 120s build_backend_for_tier
    default, which only applies to a direct un-configured call)."""
    config = _config(_local_tier())  # relay_timeout not overridden -> 20.0
    backends, _skipped = build_backends(config, env={})
    assert backends["fast-local"]._timeout == pytest.approx(20.0)


def test_build_backend_for_tier_direct_call_keeps_120s_default():
    """A caller that builds a single backend directly (bypassing build_backends)
    keeps the pre-existing 120s default -- relay_timeout is a build_backends-level
    concern, not a change to build_backend_for_tier's own default."""
    relay = build_backend_for_tier(_local_tier(), env={})
    assert relay._timeout == pytest.approx(120.0)


# --------------------------------------------------------------------------- #
# T003 — per-tier extra_body merged into the upstream body
# --------------------------------------------------------------------------- #
def test_extra_body_merged_into_openai_body():
    fake, captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    tier = _local_tier(
        extra_body={"chat_template_kwargs": {"enable_thinking": False}}
    )
    relay = build_backend_for_tier(tier, env={}, transport=fake)
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    # Router-set keys are untouched.
    assert captured["body"]["model"] == "served-model"


def test_extra_body_merged_into_anthropic_body():
    fake, captured = _post_fake(
        json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode("utf-8")
    )
    tier = _cloud_tier(extra_body={"top_k": 40})
    cloud = build_backend_for_tier(
        tier, env={"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}, transport=fake
    )
    list(cloud.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert captured["body"]["top_k"] == 40
    assert captured["body"]["model"] == "claude-opus-4-20250514"


def test_extra_body_absent_body_unchanged():
    """No regression: extra_body absent -> the body is byte-for-byte what it was
    before T003 (no extra keys, no key removed)."""
    fake, captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    relay = build_backend_for_tier(_local_tier(), env={}, transport=fake)
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert set(captured["body"].keys()) == {"model", "messages", "stream"}


def test_extra_body_can_override_a_router_set_key_when_operator_configures_it():
    """extra_body is applied last (body.update); an operator who explicitly sets
    a colliding key (e.g. stream) gets the override -- documented, intentional
    passthrough, not accidental clobbering."""
    fake, captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    tier = _local_tier(extra_body={"stream": True})
    relay = build_backend_for_tier(tier, env={}, transport=fake)
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert captured["body"]["stream"] is True
