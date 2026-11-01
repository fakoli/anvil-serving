"""Readiness remains a direct-route gate, never a fallback selector."""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.availability import (
    AlwaysAvailable,
    AvailabilityResult,
    HttpHealthAvailability,
)
from tests.router.helpers import StaticBackend
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, RouterConfig, Tier, load
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
from anvil_serving.router.serve import RoutingBackend


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


class _Unavailable:
    def check(self, tier):
        return AvailabilityResult(False, "unavailable", "connection_error")


class _Ready:
    def check(self, tier):
        return AvailabilityResult(True, "ready", "ok")


def _request(model: str = "llm.primary") -> InternalRequest:
    return InternalRequest(model=model, messages=(Message("user", "hello"),), raw={})


def _routing(availability) -> RoutingBackend:
    config = load(_CONFIG)
    return RoutingBackend(
        config,
        {"primary-local": StaticBackend(["heavy"]), "omni-local": StaticBackend(["omni"])},
        availability=availability,
    )


def test_unknown_alias_is_not_probed_or_substituted():
    routing = _routing(_Ready())

    with pytest.raises(NoAvailableTierError) as error:
        routing.generate(_request("not-configured"))

    assert error.value.kind == "unknown_model"
    assert routing._decision_log.records == ()


class _Response:
    def __init__(self, status, body=b""):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def getcode(self):
        return self.status

    def read(self, amount=-1):
        return self.body if amount < 0 else self.body[:amount]


def _probe_tier(*, model_identity=False):
    return Tier(
        id="primary-local",
        base_url="http://127.0.0.1:30002/v1",
        model="served-heavy",
        dialect="openai",
        context_limit=131072,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
        health_path="/health",
        model_identity=model_identity,
    )


def _probe_config(tier):
    return RouterConfig(
        tiers=(tier,),
        model_routes={"llm.primary": tier.id},
        availability_probe_interval=5.0,
        availability_probe_timeout=0.25,
        availability_probe_max_bytes=4096,
    )


def _replica_tier(tier_id="primary-local", ports=(30002, 30003)):
    return replace(
        _probe_tier(model_identity=True),
        id=tier_id,
        base_url="",
        replica_identity=ReplicaIdentity(
            model_revision="revision-a", engine_version="runtime-1",
            image_digest="sha256:" + "a" * 64,
            config_fingerprint="sha256:" + "b" * 64,
        ),
        replicas=tuple(
            ReplicaMember(
                id=f"member-{index}",
                base_url=f"http://127.0.0.1:{port}/v1",
                host_id="host-a",
                resource_id=f"gpu-{index}",
                qualification_ref=f"qualification:{index}",
            )
            for index, port in enumerate(ports)
        ),
    )


def test_identity_readiness_requires_exact_model_and_sends_configured_token():
    tier = _probe_tier(model_identity=True)
    calls = []

    def open_(request, timeout):
        calls.append((
            request.full_url,
            request.get_header("Authorization"),
        ))
        if request.full_url.endswith("/health"):
            return _Response(200)
        return _Response(200, json.dumps({
            "data": [{"id": "served-heavy"}],
        }).encode())

    result = HttpHealthAvailability(
        _probe_config(tier),
        opener=open_,
        env={"ANVIL_TEST_KEY": "secret"},
    ).check(tier)

    assert result.available is True
    assert result.expected_model == "served-heavy"
    assert result.observed_model == "served-heavy"
    assert calls == [
        ("http://127.0.0.1:30002/health", "Bearer secret"),
        ("http://127.0.0.1:30002/v1/models", "Bearer secret"),
    ]


def test_logical_replica_tier_and_always_available_fail_closed():
    tier = _replica_tier()
    availability = HttpHealthAvailability(
        _probe_config(tier), opener=lambda *_args, **_kwargs: _Response(200)
    )

    assert availability.check(tier).reason == "member_selection_required"
    assert availability.probe_now(tier).reason == "member_selection_required"
    assert availability.check_member(tier, "unknown").reason == "replica_member_unknown"
    assert AlwaysAvailable().check(tier).reason == "member_selection_required"
    assert AlwaysAvailable().check_member(tier, "member-0").reason == "member_readiness_not_configured"


def test_member_invalidation_is_scoped_and_member_recovers_after_expiry():
    clock = [0.0]
    first = _replica_tier("tier-a")
    second = _replica_tier("tier-b")
    first_health = [True]

    def open_(request, timeout):
        del timeout
        if request.full_url.endswith("/health"):
            if first_health[0]:
                first_health[0] = False
                return _Response(503)
            return _Response(200)
        return _Response(200, b'{"data":[{"id":"served-heavy"}]}')

    availability = HttpHealthAvailability(
        _probe_config(first), opener=open_, clock=lambda: clock[0]
    )
    assert availability.check_member(first, "member-0").available is False
    clock[0] = 6.0
    assert availability.check_member(first, "member-0").available is True
    assert availability.check_member(second, "member-0").available is True
    availability.invalidate("tier-a")
    assert availability.cached("tier-a", "member-0") is None
    assert availability.cached("tier-b", "member-0") is not None


@pytest.mark.parametrize("tier_id", ["primary-local", None])
def test_invalidation_fences_inflight_member_probe(tier_id):
    tier = _replica_tier()
    started = threading.Event()
    release = threading.Event()

    def open_(request, timeout):
        del timeout
        if request.full_url.endswith("/health"):
            started.set()
            assert release.wait(2)
            return _Response(200)
        return _Response(200, b'{"data":[{"id":"served-heavy"}]}')

    availability = HttpHealthAvailability(_probe_config(tier), opener=open_)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(availability.probe_member_now(tier, "member-0"))
    )
    thread.start()
    assert started.wait(2)
    availability.invalidate(tier_id)
    release.set()
    thread.join(2)

    assert not thread.is_alive()
    assert result[0].reason == "probe_invalidated"
    assert availability.cached(tier.id, "member-0") is None
