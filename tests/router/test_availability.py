"""Runtime readiness keeps stopped serves out of router dispatch."""
from __future__ import annotations

import textwrap
import types
import urllib.error
import json

import pytest

import anvil_serving.router.availability as availability_mod
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


def _tier(
    tier_id: str,
    port: int,
    *,
    health_path: str | None = "/health",
    model_identity: bool = False,
) -> Tier:
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
        model_identity=model_identity,
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
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def getcode(self) -> int:
        return self.status

    def read(self, amount: int = -1) -> bytes:
        return self.body if amount < 0 else self.body[:amount]


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
            availability_probe_max_bytes = 4096

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
            model_identity = true

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
    assert config.availability_probe_max_bytes == 4096
    assert config.tier("fast-local").model_identity is True


def test_identity_readiness_requires_exact_advertised_model_and_is_cached():
    tier = _tier("heavy-local", 30002, model_identity=True)
    calls = []

    def open_(request, timeout):
        calls.append((request.full_url, request.get_header("Authorization")))
        if request.full_url.endswith("/health"):
            return _Response(200)
        body = json.dumps({"object": "list", "data": [{"id": "heavy-local"}]}).encode()
        return _Response(200, body)

    availability = HttpHealthAvailability(
        _config(tier), opener=open_, env={"ANVIL_TEST_KEY": "secret"}
    )
    result = availability.check(tier)
    assert result.available is True
    assert result.reason == "identity_passed"
    assert result.expected_model == "heavy-local"
    assert result.observed_model == "heavy-local"
    assert availability.check(tier) is result
    assert calls == [
        ("http://127.0.0.1:30002/health", None),
        ("http://127.0.0.1:30002/v1/models", "Bearer secret"),
    ]


def test_default_identity_transport_disables_proxies_and_redirects(monkeypatch):
    captured = {}

    class Opener:
        def open(self, request, timeout):
            if request.full_url.endswith("/health"):
                return _Response(200)
            return _Response(200, b'{"data":[{"id":"heavy-local"}]}')

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(availability_mod.urllib.request, "build_opener", build_opener)
    tier = _tier("heavy-local", 30002, model_identity=True)
    result = HttpHealthAvailability(
        _config(tier), env={"ANVIL_TEST_KEY": "secret"}
    ).check(tier)
    assert result.available is True
    handlers = captured["handlers"]
    proxy = next(h for h in handlers if isinstance(h, availability_mod.urllib.request.ProxyHandler))
    assert proxy.proxies == {}
    assert any(isinstance(h, availability_mod._NoRedirect) for h in handlers)


def test_healthy_wrong_model_fails_closed_without_raw_payload():
    tier = _tier("heavy-local", 30002, model_identity=True)

    def open_(request, timeout):
        if request.full_url.endswith("/health"):
            return _Response(200)
        return _Response(200, b'{"data":[{"id":"wrong-secret-model"}]}')

    result = HttpHealthAvailability(_config(tier), opener=open_).check(tier)
    assert result.available is False
    assert result.reason == "identity_mismatch"
    assert result.expected_model == "heavy-local"
    assert result.observed_model == "wrong-secret-model"
    assert "wrong-secret-model" not in result.reason


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (urllib.error.HTTPError("http://ignored", 401, "secret", {}, None), "identity_http_401"),
        (TimeoutError("secret timeout detail"), "identity_transport_TimeoutError"),
        (ConnectionRefusedError("secret endpoint"), "identity_transport_ConnectionRefusedError"),
    ],
)
def test_identity_transport_failures_are_content_free(failure, reason):
    tier = _tier("heavy-local", 30002, model_identity=True)

    def open_(request, timeout):
        if request.full_url.endswith("/health"):
            return _Response(200)
        raise failure

    result = HttpHealthAvailability(_config(tier), opener=open_).check(tier)
    assert result.available is False
    assert result.reason == reason
    assert "secret" not in result.reason


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"not-json", "identity_malformed"),
        (b'{"data":{}}', "identity_malformed"),
        (b'{"data":[]}', "identity_mismatch"),
        (b"x" * (64 * 1024 + 1), "identity_oversized"),
    ],
    ids=["not-json", "wrong-data-shape", "empty-list", "oversized"],
)
def test_identity_payload_failures_are_bounded(body, reason):
    tier = _tier("heavy-local", 30002, model_identity=True)

    def open_(request, timeout):
        return _Response(200, b"" if request.full_url.endswith("/health") else body)

    result = HttpHealthAvailability(_config(tier), opener=open_).check(tier)
    assert result.available is False
    assert result.reason == reason


def test_config_rejects_identity_without_model_or_health(tmp_path):
    path = tmp_path / "router.toml"
    path.write_text(
        textwrap.dedent(
            """
            [router]
            mapping_version = "test"
            [[router.tiers]]
            id = "heavy-local"
            base_url = "http://127.0.0.1:30002/v1"
            dialect = "openai"
            context_limit = 32768
            privacy = "local"
            tool_support = true
            auth_env = "ANVIL_HEAVY_KEY"
            model_identity = true
            [router.presets]
            chat = ["heavy-local"]
            """
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="model_identity requires"):
        load(str(path))


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
