"""Backend-construction tests for genericity F001 (T005 / T003 / T002).

Hermetic and stdlib-only: every test injects a fake transport, never touching a
real network.

  * T005 — [router].relay_timeout is threaded through build_backends ->
    build_backend_for_tier so a LOCAL tier's backend actually uses it as its
    transport timeout.
  * T003 — a tier's extra_body is merged verbatim into the upstream request
    body (both dialects); absent extra_body is a no-op (no regression).
  * T002 — a local tier with model=None auto-derives its served model id from
    GET {base_url}/v1/models at backend-build time; explicit model= always
    wins; a malformed (0/>1 candidate) catalog is a ConfigError; a network
    failure is non-fatal (model stays None).
"""
from __future__ import annotations

import io
import json
import threading
import urllib.error
from dataclasses import replace
from typing import Dict

import pytest

from anvil_serving.router.backends import relay as relay_module
from anvil_serving.router import serve as serve_module
from anvil_serving.router.backends.relay import (
    _urlopen_transport,
)
from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import (
    ConfigError,
    ReplicaIdentity,
    ReplicaMember,
    RouterConfig,
    Tier,
)
from anvil_serving.router.internal import (
    BackendClientError,
    InternalRequest,
    Message,
    StructuredResult,
)
from anvil_serving.router.serve import (
    ReplicaRuntime,
    RoutingBackend,
    build_backend_for_tier,
    build_backends,
)
from anvil_serving.router.replica_scheduler import ReplicaPressure, normalize_replica_pressure


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def _local_tier(**overrides) -> Tier:
    base = dict(
        id="auxiliary-local", base_url="http://127.0.0.1:30001/v1", dialect="openai",
        context_limit=32768, privacy="local", tool_support=True,
        auth_env="ANVIL_AUXILIARY_LOCAL_KEY", model="served-model",
    )
    base.update(overrides)
    return Tier(**base)


def _anthropic_tier(**overrides) -> Tier:
    base = dict(
        id="anthropic-local", base_url="http://127.0.0.1:30002/v1", dialect="anthropic",
        context_limit=200000, privacy="local", tool_support=True,
        auth_env="ANVIL_TEST_CLOUD_KEY", model="claude-opus-4-20250514",
    )
    base.update(overrides)
    return Tier(**base)


def _config(*tiers: Tier, **overrides) -> RouterConfig:
    kwargs: Dict[str, object] = dict(
        tiers=tuple(tiers),
        model_routes={t.id: t.id for t in tiers},
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


def _replica_tier(
    tier_id: str = "replica-local", *, first_port: int = 31001
) -> Tier:
    return _local_tier(
        id=tier_id,
        base_url="",
        timeout=7.0,
        health_path="/health",
        model_identity=True,
        replicas=(
            ReplicaMember(
                "member-a",
                f"http://127.0.0.1:{first_port}/v1",
                "node-a",
                f"resource-{tier_id}-a",
                "qualification:a",
            ),
            ReplicaMember(
                "member-b",
                f"http://127.0.0.1:{first_port + 1}/v1",
                "node-a",
                f"resource-{tier_id}-b",
                "qualification:b",
            ),
        ),
        replica_identity=ReplicaIdentity(
            model_revision="revision-1",
            engine_version="engine-1",
            image_digest="sha256:" + "1" * 64,
            config_fingerprint="sha256:" + "2" * 64,
        ),
    )


# --------------------------------------------------------------------------- #
# T005 — configurable relay timeout, plumbed through build_backends
# --------------------------------------------------------------------------- #
def test_relay_timeout_plumbed_through_build_backends_to_local_backend():
    """build_backends threads config.relay_timeout into a LOCAL tier's backend."""
    config = _config(_local_tier(), relay_timeout=5.0)
    backends, skipped = build_backends(config, env={})
    assert not skipped
    assert backends["auxiliary-local"]._timeout == pytest.approx(5.0)


def test_relay_timeout_applies_to_every_direct_local_backend():
    config = _config(
        _local_tier(), _anthropic_tier(),
        relay_timeout=5.0,
    )
    backends, skipped = build_backends(
        config, env={"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}
    )
    assert not skipped
    assert backends["auxiliary-local"]._timeout == pytest.approx(5.0)
    assert backends["anthropic-local"]._timeout == pytest.approx(5.0)


def test_relay_timeout_default_is_20s_end_to_end():
    """No explicit relay_timeout in config -> RouterConfig default (20s) is what
    build_backends actually threads through (not the 120s build_backend_for_tier
    default, which only applies to a direct un-configured call)."""
    config = _config(_local_tier())  # relay_timeout not overridden -> 20.0
    backends, _skipped = build_backends(config, env={})
    assert backends["auxiliary-local"]._timeout == pytest.approx(20.0)


@pytest.mark.parametrize("status", [400, 413, 415, 422])
def test_caller_correctable_upstream_status_is_sanitized_4xx(monkeypatch, status):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://private-upstream.invalid/v1/chat/completions",
            status,
            "private provider detail",
            {},
            io.BytesIO(b'{"error":"private response body"}'),
        )

    monkeypatch.setattr(relay_module, "_direct_open", fail)

    with pytest.raises(BackendClientError) as exc_info:
        _urlopen_transport(
            "http://127.0.0.1:30010/v1/chat/completions",
            data=b"{}",
            headers={},
            timeout=1,
        )

    assert exc_info.value.status == status
    assert "private" not in exc_info.value.message


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


def test_per_tier_timeout_overrides_direct_default():
    overridden = _anthropic_tier(id="anthropic-fast", timeout=30.0)
    default = _anthropic_tier(id="anthropic-default")
    config = _config(overridden, default, relay_timeout=5.0)
    env = {"ANVIL_TEST_CLOUD_KEY": "sk-test-DEADBEEF"}
    backends, skipped = build_backends(config, env=env)
    assert not skipped
    assert backends["anthropic-fast"]._timeout == pytest.approx(30.0)
    assert backends["anthropic-default"]._timeout == pytest.approx(5.0)


# --------------------------------------------------------------------------- #
# T003 — per-tier extra_body merged into the upstream body
# --------------------------------------------------------------------------- #


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


def test_auto_derive_model_multiple_candidates_raises_config_error():
    discovery_fake, _ = _models_fake(["model-a", "model-b"])
    with pytest.raises(ConfigError) as excinfo:
        build_backend_for_tier(
            _local_tier(model=None), env={}, model_discovery_transport=discovery_fake
        )
    assert "auxiliary-local" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# Qualified replica sets T007 — immutable member runtime construction
# --------------------------------------------------------------------------- #
class _MemberBackend:
    def __init__(
        self,
        fragment: str,
        *,
        structured: StructuredResult | None = None,
        invoked: threading.Event | None = None,
        eager_error: BaseException | None = None,
        lazy_error: BaseException | None = None,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.fragment = fragment
        self.structured = structured
        self.invoked = invoked
        self.eager_error = eager_error
        self.lazy_error = lazy_error
        self.barrier = barrier
        self.calls = 0

    def generate(self, _request: InternalRequest):
        self.calls += 1
        if self.invoked is not None:
            self.invoked.set()
        if self.eager_error is not None:
            raise self.eager_error

        def fragments():
            if self.barrier is not None:
                self.barrier.wait(timeout=5)
            if self.lazy_error is not None:
                raise self.lazy_error
            yield self.fragment

        return fragments()

    def get_last_structured(self) -> StructuredResult | None:
        return self.structured


def _request() -> InternalRequest:
    return InternalRequest(model="chat", messages=[Message("user", "hi")])


# --------------------------------------------------------------------------- #
# Qualified replica sets T008 — readiness snapshot, compound lease, one relay
# --------------------------------------------------------------------------- #
class _ReplicaAvailability:
    """Deterministic composite-key member readiness with no tier fallback."""

    def __init__(self, results: dict[tuple[str, str], AvailabilityResult]) -> None:
        self.results = results
        self.member_calls: list[tuple[str, str]] = []
        self.tier_calls = 0

    def check(self, _tier: Tier) -> AvailabilityResult:
        self.tier_calls += 1
        raise AssertionError("replica routing must not use aggregate readiness")

    def check_member(self, tier: Tier, member_id: str) -> AvailabilityResult:
        self.member_calls.append((tier.id, member_id))
        return self.results[(tier.id, member_id)]


def _ready(model: str = "served-model") -> AvailabilityResult:
    return AvailabilityResult(True, "ready", "identity_passed", model, model)


def _unavailable() -> AvailabilityResult:
    return AvailabilityResult(False, "unavailable", "member_unavailable")


def _replica_request(tier_id: str) -> InternalRequest:
    return InternalRequest(model=tier_id, messages=[Message("user", "hi")])


def _replica_routing(
    tiers: tuple[Tier, ...],
    runtimes: dict[str, ReplicaRuntime],
    availability: _ReplicaAvailability,
    *,
    admission: object | None = None,
) -> RoutingBackend:
    return RoutingBackend(
        _config(*tiers), runtimes, availability=availability, admission=admission
    )


def test_replica_unavailable_member_is_skipped_and_recovery_uses_cursor():
    tier = _replica_tier()
    members = {
        "member-a": _MemberBackend("a"),
        "member-b": _MemberBackend("b"),
    }
    availability = _ReplicaAvailability({
        (tier.id, "member-a"): _unavailable(),
        (tier.id, "member-b"): _ready(),
    })
    routing = _replica_routing(
        (tier,), {tier.id: ReplicaRuntime(members)}, availability
    )

    assert list(routing.generate(_replica_request(tier.id))) == ["b"]
    availability.results[(tier.id, "member-a")] = _ready()
    assert list(routing.generate(_replica_request(tier.id))) == ["a"]
    assert members["member-a"].calls == members["member-b"].calls == 1


class _CountingLease:
    def __init__(self, member_id: str) -> None:
        self.member_id = member_id
        self.selection = None  # Match a real round-robin MemberAdmissionLease.
        self.release_calls = 0

    def release(self) -> None:
        self.release_calls += 1


class _CountingMemberAdmission:
    def __init__(self, member_id: str = "member-a") -> None:
        self.lease = _CountingLease(member_id)
        self.readiness: dict[str, AvailabilityResult] | None = None

    def acquire_member(self, _tier_id: str, readiness: dict[str, AvailabilityResult]):
        self.readiness = readiness
        return self.lease


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("eager failure"),
        BackendClientError(429, "rate_limit", "request denied"),
        TimeoutError("deadline exceeded"),
    ],
)
def test_replica_selected_eager_failures_do_not_retry_peer_or_leak_lease(error):
    tier = _replica_tier()
    first = _MemberBackend("a", eager_error=error)
    second = _MemberBackend("b")
    availability = _ReplicaAvailability({
        (tier.id, "member-a"): _ready(),
        (tier.id, "member-b"): _ready(),
    })
    admission = _CountingMemberAdmission()
    routing = _replica_routing(
        (tier,), {tier.id: ReplicaRuntime({"member-a": first, "member-b": second})},
        availability,
        admission=admission,
    )

    with pytest.raises(type(error)):
        list(routing.generate(_replica_request(tier.id)))
    assert first.calls == 1
    assert second.calls == 0
    assert admission.lease.release_calls == 1


class _CompletedPressure:
    """A completed cache view, never a collector invoked by admission."""

    def __init__(self, tiers, *, metrics_provider):
        self.tiers = tiers
        self.metrics_provider = metrics_provider
        self.values = {
            tier.id: {member.id: ReplicaPressure() for member in tier.replicas}
            for tier in tiers
        }
        self.calls = []
        self.closed = 0

    def snapshot(self, tier_id):
        self.calls.append(tier_id)
        return dict(self.values[tier_id])

    def close(self):
        self.closed += 1


def _capacity_routing(monkeypatch, *, cap=None, member_cap=2, strategy="capacity", error=None):
    monkeypatch.setattr(serve_module, "ReplicaPressureCache", _CompletedPressure)
    original = _replica_tier()
    tier = replace(
        original, max_concurrency=cap, replica_strategy=strategy,
        replicas=tuple(replace(member, max_concurrency=member_cap) for member in original.replicas),
    )
    members = {"member-a": _MemberBackend("a", eager_error=error), "member-b": _MemberBackend("b")}
    availability = _ReplicaAvailability({(tier.id, member.id): _ready() for member in tier.replicas})
    routing = _replica_routing((tier,), {tier.id: ReplicaRuntime(members)}, availability)
    return tier, routing, members


def test_capacity_dispatch_prefers_fresh_on_tie_but_local_counts_are_authoritative(monkeypatch):
    tier, routing, members = _capacity_routing(monkeypatch)
    cache = routing._replica_pressure
    cache.values[tier.id]["member-b"] = normalize_replica_pressure(
        observed_at=1, now_monotonic=1, successful=True,
        requests_running=0, requests_waiting=0, scheduler_capacity=2,
    )
    first = routing.generate(_replica_request(tier.id))
    second = routing.generate(_replica_request(tier.id))
    try:
        assert [members[key].calls for key in ("member-a", "member-b")] == [1, 1]
        assert next(first) == "b"  # fresh wins at equal 0/2
        assert next(second) == "a"  # unknown 0/2 beats fresh 1/2
        snapshot = routing._admission.snapshot(tier.id)
        assert snapshot.active_requests == 2
        assert snapshot.member_active_requests == (("member-a", 1), ("member-b", 1))
        assert cache.calls == [tier.id, tier.id]
    finally:
        first.close()
        second.close()
        routing.close()
    assert routing._admission.snapshot(tier.id).active_requests == 0
