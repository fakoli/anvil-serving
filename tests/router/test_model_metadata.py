"""Unit contracts for secret-free router metadata projections."""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.model_metadata import (
    build_model_capabilities,
    build_model_fingerprints,
    build_router_status,
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


def test_capabilities_project_declared_allowlist_and_readiness():
    result = build_model_capabilities(_config(), _Availability(), {})
    (row,) = result["data"]
    assert row == {
        "object": "model_capabilities",
        "id": "primary-local",
        "aliases": ["llm.primary", "vision.general"],
        "model": "qwen35-122b-a10b-nvfp4",
        "context_limit_tokens": 262_144,
        "tools": {"supported": True},
        "modalities": ["image", "text"],
        "thinking": {"supported": True, "default": "enabled", "caller_override": True},
        "compat": {"supportsUsageInStreaming": True, "supportsStrictMode": True,
                   "supportedReasoningEfforts": ["low", "high", "xhigh"]},
        "limits": {
            "max_output_tokens": 5120,
            "images_per_request": 1,
            "video_per_request": 0,
        },
        "readiness": {"loaded": True, "state": "ready", "reason": "identity_passed"},
    }


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


def test_reasoning_efforts_and_strict_mode_allowlist_behavior():
    # Invalid reasoningEfforts entries are dropped; strict-mode is a strict bool;
    # unknown keys never leak.
    config = _config()
    config.tiers[0].params["capabilities"] = {
        "modalities": ["text"],
        "thinking": {"supported": True},
        "compat": {"supportsStrictMode": "not-a-bool",
                   "supportsUsageInStreaming": True,
                   "supportedReasoningEfforts": ["low", "BAD VALUE", 5, "xhigh", "high"],
                   "secretKey": "never-leak"},
    }
    (row,) = build_model_capabilities(config, _Availability(), {})["data"]
    # source order preserved, invalid dropped, dedup kept
    assert row["compat"]["supportedReasoningEfforts"] == ["low", "xhigh", "high"]
    # supportsStrictMode is dropped because the value is not a bool.
    assert row["compat"]["supportsUsageInStreaming"] is True
    assert "supportsStrictMode" not in row["compat"]
    body = json.dumps(build_model_capabilities(config, _Availability(), {}))
    assert "BAD VALUE" not in body
    assert "secretKey" not in body


def test_supported_reasoning_efforts_omitted_when_absent():
    # A compat block without supportedReasoningEfforts must emit no such key.
    config = _config()
    config.tiers[0].params["capabilities"] = {
        "modalities": ["text"],
        "compat": {"supportsUsageInStreaming": True},
    }
    (row,) = build_model_capabilities(config, _Availability(), {})["data"]
    assert "supportedReasoningEfforts" not in row["compat"]


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


@pytest.mark.parametrize("builder", [build_model_capabilities, build_model_fingerprints])
def test_model_query_is_normalized_and_unknown_or_unsupported_queries_fail(builder):
    config = _config()
    assert len(builder(config, _Availability(), {"model": [" LLM.PRIMARY "]})["data"]) == 1
    with pytest.raises(KeyError):
        builder(config, _Availability(), {"model": ["missing"]})
    with pytest.raises(ValueError, match="unsupported"):
        builder(config, _Availability(), {"tier": ["primary-local"]})


def test_router_status_has_injected_clock_routes_counts_and_safe_stable_hash():
    config = _config()
    result = build_router_status(config, started_at=1_700_000_000, now=1_700_000_042, package_version="test-version")
    assert result == {
        "object": "router_status",
        "package_version": "test-version",
        "started_at": "2023-11-14T22:13:20Z",
        "uptime_seconds": 42,
        "model_aliases": ["llm.primary", "vision.general"],
        "tier_counts": {"configured": 1, "enabled": 1},
        "config_sha256": result["config_sha256"],
    }
    assert len(result["config_sha256"]) == 64
    assert "127.0.0.1" not in json.dumps(result)
    assert "ANVIL_TEST_KEY" not in json.dumps(result)
    assert build_router_status(config, started_at=1_700_000_000, now=1_700_000_042, package_version="test-version") == result


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


@pytest.mark.parametrize("builder", [build_model_capabilities, build_model_fingerprints])
def test_replica_metadata_has_one_alias_bounded_members_and_declared_provenance(builder):
    config = _replica_config()
    availability = _MemberAvailability()
    (row,) = builder(config, availability, {})["data"]
    assert row["aliases"] == ["llm.primary"]
    assert row["id"] == "primary-local"
    assert row["deployment_identity_source"] == "declared"
    assert row["runtime_deployment_identity_verified"] is False
    assert row["replica_identity"]["model_revision"] == "revision-1"
    assert row["readiness"] == {"loaded": True, "state": "ready", "reason": "replicas_partial"}
    assert [member["id"] for member in row["members"]] == ["member-a", "member-b"]
    assert row["members"][1]["served_identity"] == {"expected": config.tiers[0].model, "observed": None}
    assert availability.calls == [("primary-local", "member-a"), ("primary-local", "member-b")]
    if builder is build_model_fingerprints:
        assert row["fingerprint"] == row["replica_identity"]
    payload = json.dumps(row)
    for secret in ("unexpected-private-model", "127.0.0.1", "ANVIL_TEST_KEY", "node-a", "resource-a", "private_note", "never-emit-me"):
        assert secret not in payload


@pytest.mark.parametrize("builder", [build_model_capabilities, build_model_fingerprints])
def test_replica_projection_never_adopts_unverified_or_arbitrary_readiness(builder):
    config = _replica_config()

    class Hostile:
        def check_member(self, tier, member):
            return AvailabilityResult(True, "secret_state", "secret_code", "secret_expected", "secret_observed")

    (row,) = builder(config, Hostile(), {})["data"]
    assert row["readiness"]["loaded"] is False
    assert all(member["readiness"]["reason"] == "identity_mismatch" for member in row["members"])
    assert "secret_" not in json.dumps(row)
    assert build_model_capabilities(config, object(), {})["data"][0]["readiness"]["loaded"] is False


def test_replica_public_config_hash_tracks_safe_membership_but_not_endpoints():
    config = _replica_config()
    def digest(value):
        return build_router_status(value, started_at=0, now=1)["config_sha256"]
    tier = config.tiers[0]
    changed_url = replace(tier.replicas[0], base_url="http://127.0.0.1:30004/v1")
    assert digest(replace(config, tiers=(replace(tier, replicas=(changed_url, tier.replicas[1])),))) == digest(config)
    changed_ref = replace(tier.replicas[0], qualification_ref="qualification:new")
    assert digest(replace(config, tiers=(replace(tier, replicas=(changed_ref, tier.replicas[1])),))) != digest(config)
    assert digest(replace(config, tiers=(replace(tier, replicas=tuple(reversed(tier.replicas))),))) == digest(config)
