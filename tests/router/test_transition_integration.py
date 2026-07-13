"""Hermetic one-workstation/two-GPU transition behavior."""
from __future__ import annotations

import threading

import pytest

from anvil_serving.router.availability import AlwaysAvailable, AvailabilityResult
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
from anvil_serving.router.serve import RoutingBackend


class _Profile:
    def decision(self, tier_id, work_class, *, is_cloud=False):
        return "allow"

    def score(self, tier_id, work_class):
        return 1.0


class _TextBackend:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        yield self.text


class _BlockingBackend:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate(self, request):
        self.entered.set()
        self.release.wait(2)
        yield "HEAVY"


def _tier(tier_id, port):
    return Tier(
        id=tier_id,
        base_url=f"http://127.0.0.1:{port}/v1",
        model=tier_id,
        dialect="openai",
        context_limit=131072,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
    )


def _routing(heavy_backend, fast_backend):
    heavy = _tier("heavy-local", 30002)
    fast = _tier("fast-local", 30003)
    config = RouterConfig(
        tiers=(heavy, fast),
        presets={
            "chat": (heavy.id, fast.id),
            "heavy-only": (heavy.id,),
        },
        mapping_version="transition-test",
        verify_local_min=False,
    )
    return RoutingBackend(
        config,
        {heavy.id: heavy_backend, fast.id: fast_backend},
        _Profile(),
        availability=AlwaysAvailable(),
    )


def _request(model="chat"):
    return InternalRequest(model=model, messages=[Message("user", "reply")])


def test_heavy_drains_while_late_eligible_request_uses_resident_fast():
    heavy = _BlockingBackend()
    fast = _TextBackend("FAST")
    routing = _routing(heavy, fast)
    result = []
    worker = threading.Thread(target=lambda: result.append("".join(routing.generate(_request()))))
    worker.start()
    assert heavy.entered.wait(1)

    snapshot = routing.quiesce_tier("heavy-local")
    assert snapshot["active_requests"] == 1
    assert "".join(routing.generate(_request())) == "FAST"
    assert fast.calls == 1
    assert routing._circuit_breaker.failure_count("heavy-local") == 0

    heavy.release.set()
    worker.join(1)
    assert result == ["HEAVY"]
    assert routing.drain_tier("heavy-local", 1)["drained"] is True
    record = routing._decision_log.records[-1]
    assert [attempt.outcome for attempt in record.attempts] == [
        "skipped-quiesced", "served",
    ]


def test_heavy_only_request_fails_closed_while_quiesced():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    routing.quiesce_tier("heavy-local")

    with pytest.raises(NoAvailableTierError) as exc:
        routing.generate(_request("heavy-only"))
    assert exc.value.kind == "unavailable"
    assert routing._circuit_breaker.failure_count("heavy-local") == 0


def test_direct_stream_close_releases_full_generation_lease():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    stream = routing.generate(_request())
    assert routing._admission.snapshot("heavy-local").active_requests == 1
    assert next(stream) == "HEAVY"
    stream.close()
    assert routing._admission.snapshot("heavy-local").active_requests == 0


def test_unadvanced_direct_stream_close_releases_admission_lease():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    stream = routing.generate(_request())
    assert routing._admission.snapshot("heavy-local").active_requests == 1
    stream.close()
    assert routing._admission.snapshot("heavy-local").active_requests == 0


def test_route_decision_never_advertises_a_quiesced_tier():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    routing.quiesce_tier("heavy-local")
    with pytest.raises(NoAvailableTierError) as exc:
        routing.decide(_request("heavy-only"))
    assert exc.value.kind == "unavailable"


def test_guarded_readmit_requires_identity_readiness_configuration():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    routing.quiesce_tier("heavy-local")
    result = routing.readmit_tier("heavy-local")
    assert result["readmitted"] is False
    assert result["reason"] == "identity_not_configured"
    assert routing._admission.snapshot("heavy-local").quiesced is True


def test_successful_readmit_keeps_the_identity_result_cached():
    heavy = Tier(
        **{
            **_tier("heavy-local", 30002).__dict__,
            "health_path": "/health",
            "model_identity": True,
        }
    )

    class Ready:
        def __init__(self):
            self.invalidated = []

        def invalidate(self, tier_id=None):
            self.invalidated.append(tier_id)

        def check(self, tier):
            return AvailabilityResult(
                True, "ready", "identity_passed", tier.model, tier.model
            )

    ready = Ready()
    routing = RoutingBackend(
        RouterConfig(
            tiers=(heavy,),
            presets={"heavy-only": (heavy.id,)},
            mapping_version="transition-test",
            verify_local_min=False,
        ),
        {heavy.id: _TextBackend("HEAVY")},
        _Profile(),
        availability=ready,
    )
    routing.quiesce_tier(heavy.id)
    assert routing.readmit_tier(heavy.id)["readmitted"] is True
    assert ready.invalidated == [heavy.id, heavy.id]


def test_guarded_readmit_rejects_available_without_exact_identity_evidence():
    heavy = Tier(
        **{
            **_tier("heavy-local", 30002).__dict__,
            "health_path": "/health",
            "model_identity": True,
        }
    )
    routing = RoutingBackend(
        RouterConfig(
            tiers=(heavy,),
            presets={"heavy-only": (heavy.id,)},
            mapping_version="transition-test",
            verify_local_min=False,
        ),
        {heavy.id: _TextBackend("HEAVY")},
        _Profile(),
        availability=AlwaysAvailable(),
    )
    routing.quiesce_tier(heavy.id)
    result = routing.readmit_tier(heavy.id)
    assert result["readmitted"] is False
    assert result["reason"] == "identity_not_verified"
