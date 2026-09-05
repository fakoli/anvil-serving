"""Readiness remains a direct-route gate, never a fallback selector."""
from __future__ import annotations

import json
import textwrap
import urllib.error
from pathlib import Path

import pytest

import anvil_serving.router.availability as availability_module
from anvil_serving.router.availability import (
    AvailabilityResult,
    HttpHealthAvailability,
)
from tests.router.helpers import StaticBackend
from anvil_serving.router.config import RouterConfig, Tier, load
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


def test_unready_direct_route_never_calls_its_upstream():
    routing = _routing(_Unavailable())

    with pytest.raises(NoAvailableTierError) as error:
        routing.generate(_request())

    assert error.value.kind == "unavailable"
    record = routing._decision_log.records[-1]
    assert record.served_tier is None
    assert record.attempts[0].tier_id == "primary-local"
    assert record.attempts[0].reason == "unavailable"


def test_ready_alias_relays_to_its_single_configured_tier():
    routing = _routing(_Ready())

    assert list(routing.generate(_request("llm.voice"))) == ["omni"]
    record = routing._decision_log.records[-1]
    assert record.served_tier == "omni-local"
    assert record.requested_tier == "omni-local"


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

    tier = _probe_tier()
    availability = HttpHealthAvailability(
        _probe_config(tier),
        opener=open_,
        clock=lambda: clock[0],
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
    assert len(calls) == 2


def test_default_readiness_transport_disables_proxies_and_redirects(monkeypatch):
    captured = {}

    class Opener:
        def open(self, request, timeout):
            if request.full_url.endswith("/health"):
                return _Response(200)
            return _Response(200, b'{"data":[{"id":"served-heavy"}]}')

    def build_opener(*handlers):
        captured["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(
        availability_module.urllib.request, "build_opener", build_opener
    )
    tier = _probe_tier(model_identity=True)
    result = HttpHealthAvailability(
        _probe_config(tier), env={"ANVIL_TEST_KEY": "secret"}
    ).check(tier)

    assert result.available is True
    handlers = captured["handlers"]
    proxy = next(
        handler for handler in handlers
        if isinstance(handler, availability_module.urllib.request.ProxyHandler)
    )
    assert proxy.proxies == {}
    assert any(
        isinstance(handler, availability_module._NoRedirect)
        for handler in handlers
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


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (
            urllib.error.HTTPError(
                "http://ignored", 401, "secret", {}, None
            ),
            "identity_http_401",
        ),
        (
            TimeoutError("secret timeout detail"),
            "identity_transport_TimeoutError",
        ),
        (
            ConnectionRefusedError("secret endpoint"),
            "identity_transport_ConnectionRefusedError",
        ),
    ],
)
def test_identity_transport_failures_are_content_free(failure, reason):
    tier = _probe_tier(model_identity=True)

    def open_(request, timeout):
        if request.full_url.endswith("/health"):
            return _Response(200)
        raise failure

    result = HttpHealthAvailability(
        _probe_config(tier), opener=open_
    ).check(tier)

    assert result.available is False
    assert result.reason == reason
    assert "secret" not in result.reason


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"not-json", "identity_malformed"),
        (b'{"data":{}}', "identity_malformed"),
        (b'{"data":[]}', "identity_mismatch"),
        (b"x" * 4097, "identity_oversized"),
    ],
)
def test_identity_payload_failures_are_bounded(body, reason):
    tier = _probe_tier(model_identity=True)

    def open_(request, timeout):
        return _Response(
            200, b"" if request.full_url.endswith("/health") else body
        )

    result = HttpHealthAvailability(
        _probe_config(tier), opener=open_
    ).check(tier)

    assert result.available is False
    assert result.reason == reason


@pytest.mark.parametrize(
    "health_path",
    ["health", "//other-host/health", "/health?full=1", "/health#x"],
)
def test_config_rejects_unsafe_health_path(tmp_path, health_path):
    path = tmp_path / "router.toml"
    path.write_text(textwrap.dedent(f"""
        [router]

        [[router.tiers]]
        id = "primary-local"
        base_url = "http://127.0.0.1:30002/v1"
        model = "served-heavy"
        dialect = "openai"
        context_limit = 32768
        privacy = "local"
        tool_support = true
        auth_env = "ANVIL_TEST_KEY"
        health_path = "{health_path}"

        [router.model_routes]
        llm.primary = "primary-local"
    """), encoding="utf-8")

    with pytest.raises(ValueError, match="health_path"):
        load(str(path))
