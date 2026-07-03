"""Backend-construction tests for genericity F001 (T005 / T003 / T002).

Hermetic and stdlib-only: every test injects a fake transport, never touching a
real network.

  * T005 — [router].relay_timeout is threaded through build_backends ->
    build_backend_for_tier so a LOCAL tier's backend actually uses it as its
    transport timeout (cloud tiers keep the 120s default).
  * T003 — a tier's extra_body is merged verbatim into the upstream request
    body (both dialects); absent extra_body is a no-op (no regression).
  * T002 — a local tier with model=None auto-derives its served model id from
    GET {base_url}/v1/models at backend-build time; explicit model= always
    wins; a malformed (0/>1 candidate) catalog is a ConfigError; a network
    failure is non-fatal (model stays None).
"""
from __future__ import annotations

import json
from typing import Dict

import pytest

from anvil_serving.router.backends.cloud import discover_single_model
from anvil_serving.router.config import ConfigError, RouterConfig, Tier
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
# T007 — per-tier `timeout` overrides the global relay_timeout
# --------------------------------------------------------------------------- #
def test_per_tier_timeout_overrides_relay_timeout_on_local_tier():
    """flexibility:T007 — a LOCAL tier with an explicit `timeout` uses IT (not the
    global relay_timeout) for its backend; a sibling tier without one still uses
    config.relay_timeout."""
    with_override = _local_tier(id="fast-override", timeout=120.0)
    without = _local_tier(id="fast-default")  # timeout=None -> global default
    config = _config(with_override, without, relay_timeout=5.0)
    backends, skipped = build_backends(config, env={})
    assert not skipped
    assert backends["fast-override"]._timeout == pytest.approx(120.0)
    assert backends["fast-default"]._timeout == pytest.approx(5.0)


def test_per_tier_timeout_overrides_cloud_default():
    """flexibility:T007 — a CLOUD tier's explicit `timeout` overrides the 120s
    cloud default; a cloud tier without one keeps that 120s default (unchanged)."""
    overridden = _cloud_tier(id="cloud-fast", timeout=30.0)
    default = _cloud_tier(id="cloud-default")  # timeout=None -> 120s default
    config = _config(overridden, default, relay_timeout=5.0)
    env = {"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}
    backends, skipped = build_backends(config, env=env)
    assert not skipped
    assert backends["cloud-fast"]._timeout == pytest.approx(30.0)
    assert backends["cloud-default"]._timeout == pytest.approx(120.0)


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


# --------------------------------------------------------------------------- #
# T002 — GET /v1/models auto-derive for a local tier with model=None
# --------------------------------------------------------------------------- #
def _models_fake(model_ids):
    """A fake GET transport(url, *, headers, timeout) advertising `model_ids`."""
    captured: Dict[str, object] = {}

    def fake(url, *, headers, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return json.dumps({"data": [{"id": m} for m in model_ids]}).encode("utf-8")

    return fake, captured


def test_auto_derive_model_single_candidate_forwards_that_id():
    """model=None + a stub upstream advertising exactly one model -> the backend
    forwards THAT id (not the preset routing token) in the upstream body."""
    discovery_fake, discovery_captured = _models_fake(["qwen3-32b-awq"])
    post_fake, post_captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )

    tier = _local_tier(model=None)
    relay = build_backend_for_tier(
        tier, env={}, transport=post_fake, model_discovery_transport=discovery_fake
    )
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))

    assert discovery_captured["url"] == "http://127.0.0.1:30001/v1/models"
    assert post_captured["body"]["model"] == "qwen3-32b-awq"  # NOT "chat"


def test_auto_derive_model_zero_candidates_raises_config_error():
    discovery_fake, _ = _models_fake([])
    with pytest.raises(ConfigError):
        build_backend_for_tier(
            _local_tier(model=None), env={}, model_discovery_transport=discovery_fake
        )


def test_auto_derive_model_multiple_candidates_raises_config_error():
    discovery_fake, _ = _models_fake(["model-a", "model-b"])
    with pytest.raises(ConfigError) as excinfo:
        build_backend_for_tier(
            _local_tier(model=None), env={}, model_discovery_transport=discovery_fake
        )
    assert "fast-local" in str(excinfo.value)


def test_auto_derive_model_explicit_model_skips_the_probe():
    """An explicit model= always wins: the discovery transport is never called."""
    def _boom(url, *, headers, timeout):
        raise AssertionError("discovery transport must not be called when model= is set")

    post_fake, post_captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    relay = build_backend_for_tier(
        _local_tier(model="served-model"),  # already set
        env={}, transport=post_fake, model_discovery_transport=_boom,
    )
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    assert post_captured["body"]["model"] == "served-model"


def test_auto_derive_model_network_error_is_non_fatal_leaves_model_none():
    """A network failure during discovery must NOT crash backend construction —
    model stays unresolved and the existing request.model fallback applies."""
    def _network_error(url, *, headers, timeout):
        raise OSError("connection refused")

    post_fake, post_captured = _post_fake(
        json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")
    )
    relay = build_backend_for_tier(
        _local_tier(model=None), env={}, transport=post_fake,
        model_discovery_transport=_network_error,
    )
    list(relay.generate(InternalRequest(model="chat", messages=[Message("user", "hi")])))
    # request.model ("chat") is forwarded, unchanged from today's behaviour.
    assert post_captured["body"]["model"] == "chat"


def test_auto_derive_model_malformed_response_is_non_fatal():
    """A reachable-but-garbage response (not valid JSON) is treated the same as
    a network error -- non-fatal, model stays unresolved."""
    def _garbage(url, *, headers, timeout):
        return b"not json"

    relay = build_backend_for_tier(
        _local_tier(model=None), env={}, model_discovery_transport=_garbage,
    )
    assert relay._tier.model is None


def test_discover_single_model_cloud_tier_is_a_no_op():
    """discover_single_model() never probes a cloud tier, even if model=None."""
    def _boom(url, *, headers, timeout):
        raise AssertionError("must not probe a cloud tier")

    tier = _cloud_tier(model=None)
    out = discover_single_model(tier, transport=_boom)
    assert out is tier


def test_discover_single_model_pure_function_returns_new_tier():
    """discover_single_model() is a pure function: the input Tier is untouched
    (frozen dataclass) and a NEW Tier with model set is returned."""
    fake, _ = _models_fake(["only-model"])
    tier = _local_tier(model=None)
    out = discover_single_model(tier, transport=fake)
    assert tier.model is None  # original untouched
    assert out.model == "only-model"
    assert out.id == tier.id  # every other field carried over
