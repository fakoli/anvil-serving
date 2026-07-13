"""Runtime readiness keeps stopped serves out of router dispatch."""
from __future__ import annotations

import textwrap
import types
import urllib.error

import pytest

from anvil_serving.router.availability import (
    AvailabilityResult,
    HttpHealthAvailability,
)
from anvil_serving.router.config import RouterConfig, Tier, load
from anvil_serving.router.fallback import (
    Budget,
    CircuitBreaker,
    RoutingDecision,
    route_with_fallback,
)
from anvil_serving.router.internal import (
    InternalRequest,
    Message,
    NoAvailableTierError,
)
from anvil_serving.router.serve import RoutingBackend


def _tier(tier_id: str, port: int, *, health_path: str | None = "/health") -> Tier:
    return Tier(
        id=tier_id,
        base_url=f"http://127.0.0.1:{port}/v1",
        dialect="openai",
        context_limit=131072,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
        model=tier_id,
        health_path=health_path,
    )


def _config(*tiers: Tier, verify_local_min: bool = False) -> RouterConfig:
    return RouterConfig(
        tiers=tuple(tiers),
        presets={"chat-fast": tuple(t.id for t in tiers)},
        mapping_version="availability-test",
        verify_local_min=verify_local_min,
        availability_probe_interval=5.0,
        availability_probe_timeout=0.25,
    )


class _Response:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def getcode(self) -> int:
        return self.status


class _Profile:
    def decision(self, tier_id, work_class, *, is_cloud=False):
        return "allow"

    def score(self, tier_id, work_class):
        return 1.0


class _Backend:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        yield self.text


class _Availability:
    def __init__(self, **states: bool) -> None:
        self.states = states

    def check(self, tier):
        available = self.states[tier.id]
        return AvailabilityResult(
            available,
            "ready" if available else "unavailable",
            "health_passed" if available else "health_transport_ConnectionRefusedError",
        )


def _request() -> InternalRequest:
    return InternalRequest(
        model="chat-fast",
        messages=[Message("user", "return READY")],
    )


def test_http_health_is_cached_then_rechecks_and_recovers():
    clock = [100.0]
    outcomes = [ConnectionRefusedError(), _Response(200)]
    calls = []

    def open_(request, timeout):
        calls.append((request.full_url, timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    tier = _tier("fast-local", 30001)
    availability = HttpHealthAvailability(
        _config(tier), opener=open_, clock=lambda: clock[0]
    )

    first = availability.check(tier)
    assert first.available is False
    assert first.reason == "health_transport_ConnectionRefusedError"
    assert availability.check(tier) is first
    assert len(calls) == 1

    clock[0] += 5.1
    recovered = availability.check(tier)
    assert recovered.available is True
    assert recovered.reason == "health_passed"
    assert calls == [
        ("http://127.0.0.1:30001/health", 0.25),
        ("http://127.0.0.1:30001/health", 0.25),
    ]


def test_unconfigured_or_cloud_health_is_no_network_compatibility_path():
    calls = []
    local = _tier("local", 30001, health_path=None)
    cloud = types.SimpleNamespace(
        **{**_tier("cloud", 30002).__dict__, "privacy": "cloud"}
    )
    availability = HttpHealthAvailability(
        _config(local), opener=lambda *a, **k: calls.append((a, k))
    )
    assert availability.check(local).available is True
    assert availability.check(cloud).available is True
    assert calls == []


def test_http_error_is_unavailable_with_content_free_reason():
    def open_(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 503, "secret", {}, None)

    tier = _tier("fast-local", 30001)
    result = HttpHealthAvailability(_config(tier), opener=open_).check(tier)
    assert result.available is False
    assert result.reason == "health_http_503"
    assert "secret" not in result.reason


def test_fallback_skips_unavailable_without_call_or_breaker_failure():
    fast = _tier("fast-local", 30001)
    heavy = _tier("heavy-local", 30002)
    config = _config(fast, heavy, verify_local_min=True)
    backends = {fast.id: _Backend("FAST"), heavy.id: _Backend("HEAVY")}
    breaker = CircuitBreaker()
    states = {
        fast.id: AvailabilityResult(False, "unavailable", "health_transport_refused"),
        heavy.id: AvailabilityResult(True, "ready", "health_passed"),
    }

    result = route_with_fallback(
        _request(),
        RoutingDecision((fast.id, heavy.id), "chat-fast"),
        config,
        backend_for=lambda tier: backends[tier.id],
        budget=Budget(circuit_threshold=2),
        breaker=breaker,
        availability_for=lambda tier_id: states[tier_id],
    )

    assert result.served_tier == heavy.id
    assert result.record.fell_back is True
    assert [a.outcome for a in result.record.attempts] == [
        "skipped-unavailable",
        "served",
    ]
    assert backends[fast.id].calls == 0
    assert backends[heavy.id].calls == 1
    assert breaker.failure_count(fast.id) == 0


def test_routing_backend_uses_next_ready_tier_and_route_probe_matches():
    fast = _tier("fast-local", 30001)
    heavy = _tier("heavy-local", 30002)
    backends = {fast.id: _Backend("FAST"), heavy.id: _Backend("HEAVY")}
    routing = RoutingBackend(
        _config(fast, heavy),
        backends,
        _Profile(),
        availability=_Availability(**{fast.id: False, heavy.id: True}),
    )

    assert "".join(routing.generate(_request())) == "HEAVY"
    assert backends[fast.id].calls == 0
    assert backends[heavy.id].calls == 1
    route = routing.decide(_request())
    assert route["provider"] == heavy.id
    assert "unavailable: ['fast-local']" in route["reason"]


def test_all_bound_but_unready_tiers_raise_distinct_unavailable_kind():
    fast = _tier("fast-local", 30001)
    routing = RoutingBackend(
        _config(fast),
        {fast.id: _Backend("FAST")},
        _Profile(),
        availability=_Availability(**{fast.id: False}),
    )

    with pytest.raises(NoAvailableTierError) as exc:
        routing.generate(_request())
    assert exc.value.kind == "unavailable"
    assert "were not dispatched" in str(exc.value)
    assert routing._circuit_breaker.failure_count(fast.id) == 0


def test_config_parses_health_path_and_probe_controls(tmp_path):
    path = tmp_path / "router.toml"
    path.write_text(
        textwrap.dedent(
            """
            [router]
            mapping_version = "test"
            availability_probe_interval = 7
            availability_probe_timeout = 0.5

            [[router.tiers]]
            id = "fast-local"
            base_url = "http://127.0.0.1:30001/v1"
            model = "fast"
            dialect = "openai"
            context_limit = 32768
            privacy = "local"
            tool_support = true
            auth_env = "ANVIL_FAST_KEY"
            health_path = "/healthz"

            [router.presets]
            chat-fast = ["fast-local"]
            """
        ),
        encoding="utf-8",
    )

    config = load(str(path))
    assert config.tier("fast-local").health_path == "/healthz"
    assert config.availability_probe_interval == 7.0
    assert config.availability_probe_timeout == 0.5


@pytest.mark.parametrize(
    "health_path", ["health", "//other-host/health", "/health?full=1", "/health#x"]
)
def test_config_rejects_unsafe_health_path(tmp_path, health_path):
    path = tmp_path / "router.toml"
    path.write_text(
        textwrap.dedent(
            f"""
            [router]
            mapping_version = "test"
            [[router.tiers]]
            id = "fast-local"
            base_url = "http://127.0.0.1:30001/v1"
            model = "fast"
            dialect = "openai"
            context_limit = 32768
            privacy = "local"
            tool_support = true
            auth_env = "ANVIL_FAST_KEY"
            health_path = "{health_path}"
            [router.presets]
            chat-fast = ["fast-local"]
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="health_path"):
        load(str(path))
