"""Readiness remains a direct-route gate, never a fallback selector."""
from __future__ import annotations

import json
import textwrap
import threading
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

import anvil_serving.router.availability as availability_module
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


def test_replica_members_are_independent_and_hide_mismatch_identity():
    tier = _replica_tier(ports=(30002, 30003, 30004, 30005))

    def open_(request, timeout):
        del timeout
        if ":30003/health" in request.full_url:
            return _Response(503)
        if ":30005/health" in request.full_url:
            raise ConnectionRefusedError("synthetic endpoint")
        if request.full_url.endswith("/health"):
            return _Response(200)
        model = "unexpected-secret-model" if ":30004/" in request.full_url else "served-heavy"
        return _Response(200, json.dumps({"data": [{"id": model}]}).encode())

    availability = HttpHealthAvailability(_probe_config(tier), opener=open_)
    ready = availability.check_member(tier, "member-0")
    unavailable = availability.check_member(tier, "member-1")
    mismatch = availability.check_member(tier, "member-2")
    failed = availability.check_member(tier, "member-3")

    assert ready.available is True and ready.observed_model == "served-heavy"
    assert unavailable.reason == "health_http_503"
    assert mismatch.reason == "identity_mismatch" and mismatch.observed_model is None
    assert failed.reason == "health_transport_ConnectionRefusedError"
    assert "secret" not in mismatch.reason + failed.reason
    assert availability.cached(tier.id, "member-0") is not None
    assert availability.cached(tier.id) is None


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


@pytest.mark.parametrize("identity", [
    None,
    {},
    ReplicaIdentity("", "runtime-1", "sha256:" + "a" * 64, "sha256:" + "b" * 64),
    ReplicaIdentity("revision-a", "runtime-1", "wrong", "sha256:" + "b" * 64),
])
def test_member_probe_requires_valid_declared_identity_before_io(identity):
    tier = replace(_replica_tier(), replica_identity=identity)
    calls = []

    def open_(request, timeout):
        calls.append(request)
        return _Response(200, b'{"data":[{"id":"served-heavy"}]}')

    availability = HttpHealthAvailability(_probe_config(tier), opener=open_)
    for probe in (availability.check_member, availability.probe_member_now):
        result = probe(tier, "member-0")
        assert result.available is False
        assert result.reason == "replica_probe_not_configured"
    assert calls == []


@pytest.mark.parametrize("stage", ["health", "identity"])
@pytest.mark.parametrize("code", ["https://token:secret@100.64.0.10/private", True, -1, 999999])
def test_member_http_error_status_is_bounded_and_content_free(stage, code):
    tier = _replica_tier()

    def open_(request, timeout):
        if stage == "identity" and request.full_url.endswith("/health"):
            return _Response(200)
        raise urllib.error.HTTPError("http://ignored", code, "private", {}, None)

    result = HttpHealthAvailability(_probe_config(tier), opener=open_).check_member(
        tier, "member-0"
    )
    assert result.available is False
    assert result.reason == stage + "_http_unknown"
    assert "secret" not in repr(result)


def test_same_member_id_across_tiers_has_independent_cache_and_probe_lock():
    first = _replica_tier("tier-a", (30002, 30003))
    second = _replica_tier("tier-b", (30004, 30005))
    both_started = threading.Event()
    release = threading.Event()
    starts = []
    starts_lock = threading.Lock()

    def open_(request, timeout):
        del timeout
        if request.full_url.endswith("/health"):
            with starts_lock:
                starts.append(request.full_url)
                if len(starts) == 2:
                    both_started.set()
            assert release.wait(2)
            return _Response(200)
        return _Response(200, b'{"data":[{"id":"served-heavy"}]}')

    availability = HttpHealthAvailability(_probe_config(first), opener=open_)
    results = []
    threads = [
        threading.Thread(
            target=lambda tier=tier: results.append(
                availability.check_member(tier, "member-0")
            )
        )
        for tier in (first, second)
    ]
    for thread in threads:
        thread.start()
    assert both_started.wait(2)
    release.set()
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()

    assert len(results) == 2 and all(result.available for result in results)
    assert availability.cached("tier-a", "member-0") is not None
    assert availability.cached("tier-b", "member-0") is not None


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
