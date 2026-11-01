"""Unit contracts for secret-free router metadata projections."""
from __future__ import annotations

import json
from dataclasses import replace


from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.model_metadata import (
    build_model_capabilities,
    build_model_fingerprints,
)


class _Availability:
    def check(self, _tier):
        return AvailabilityResult(
            True, "ready", "identity_passed",
            expected_model="qwen35-122b-a10b-nvfp4",
            observed_model="qwen35-122b-a10b-nvfp4",
        )


def _config() -> RouterConfig:
    tier = Tier(
        id="primary-local",
        base_url="http://127.0.0.1:30002/v1",
        model="qwen35-122b-a10b-nvfp4",
        dialect="openai",
        context_limit=262_144,
        max_output_tokens=5120,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
        engine="vllm",
        quantization="nvfp4",
        model_identity=True,
        params={
            "private_note": "never-emit-me",
            "capabilities": {
                "modalities": ["text", "image", "wrong value", 1],
                "thinking": {"supported": True, "default": "enabled", "caller_override": True, "private": "nope"},
                "compat": {
                    "supportsUsageInStreaming": True,
                    "supportsStrictMode": True,
                    "supportedReasoningEfforts": ["low", "high", "MEDIUM", "xhigh", 1, "bad value"],
                    "supportsSomethingPrivate": "never-leak-me",
                },
                "images_per_request": 1,
                "video_per_request": 0,
            },
            "fingerprint": {
                "model_revision": "9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2",
                "engine_version": "vllm-0.12.0",
                "image_digest": "sha256:abc123",
                "config_fingerprint": "d34db33f",
                "private_url": "http://127.0.0.1:30002/v1",
            },
        },
    )
    return RouterConfig(
        tiers=(tier,),
        model_routes={"llm.primary": "primary-local", "vision.general": "primary-local"},
    )


def test_capabilities_compat_unset_emits_null_and_never_leaks_unknown_keys():
    # No declared compat (or only unknown keys) -> null; private/unknown never leak.
    config = _config()
    config.tiers[0].params["capabilities"] = {
        "modalities": ["text"],
        "compat": {"unlistedCapability": True},
    }
    (row,) = build_model_capabilities(config, _Availability(), {})["data"]
    assert row["compat"] is None
    body = json.dumps(build_model_capabilities(config, _Availability(), {}))
    assert "supportsSomethingPrivate" not in body
    assert "unlistedCapability" not in body


def test_fingerprints_exclude_arbitrary_params_and_connection_details():
    result = build_model_fingerprints(_config(), _Availability(), {})
    (row,) = result["data"]
    assert row["fingerprint"] == {
        "model_revision": "9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2",
        "engine_version": "vllm-0.12.0",
        "image_digest": "sha256:abc123",
        "config_fingerprint": "d34db33f",
    }
    assert row["served_identity"] == {
        "expected": "qwen35-122b-a10b-nvfp4",
        "observed": "qwen35-122b-a10b-nvfp4",
    }
    body = json.dumps(result)
    assert "private_note" not in body
    assert "private_url" not in body
    assert "127.0.0.1" not in body
    assert "ANVIL_TEST_KEY" not in body

    config = _config()
    config.tiers[0].params["fingerprint"]["image_digest"] = (
        "http://127.0.0.1/private"
    )
    assert build_model_fingerprints(
        config, _Availability(), {}
    )["data"][0]["fingerprint"]["image_digest"] is None


def _replica_config():
    tier = replace(
        _config().tiers[0], base_url="", health_path="/health",
        replicas=(
            ReplicaMember("member-b", "http://127.0.0.1:30003/v1", "node-a", "resource-b", "qualification:b"),
            ReplicaMember("member-a", "http://127.0.0.1:30002/v1", "node-a", "resource-a", "qualification:a"),
        ),
        replica_identity=ReplicaIdentity("revision-1", "engine-1", "sha256:" + "a" * 64, "sha256:" + "b" * 64),
    )
    return RouterConfig(tiers=(tier,), model_routes={"llm.primary": tier.id})


class _MemberAvailability:
    def __init__(self):
        self.calls = []

    def check(self, tier):
        raise AssertionError("replica sentinel must not use direct readiness")

    def check_member(self, tier, member):
        self.calls.append((tier.id, member))
        if member == "member-a":
            return AvailabilityResult(True, "ready", "identity_passed", tier.model, tier.model)
        return AvailabilityResult(False, "unavailable", "identity_mismatch", tier.model, "unexpected-private-model")
