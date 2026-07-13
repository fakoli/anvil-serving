"""Hermetic one-workstation/two-GPU transition behavior."""
from __future__ import annotations

import textwrap
import threading
import types

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


# ---- reservation eviction composes this transition (gpu-reservations:T005) ----
#
# ADR-0017 §5: an over-budget `on-demand` acquisition evicts committed
# `evictable` reservations by composing the ADR-0018 steps against the REAL
# router admission state — quiesce the victim's tier, bounded drain via its
# counted `AdmissionLease` generations, and only then stop the container
# (which IS the reservation release). Docker is a fake `_run` seam; the
# admission/drain machinery is the real thing.


def _eviction_manifest(tmp_path):
    path = tmp_path / "serves.toml"
    path.write_text(textwrap.dedent("""
        [[gpu_roles]]
        id = "dark-fast"
        vram_mib = 32768
        reserve_mib = 2048

        [[serve]]
        name = "fast"
        container = "vllm-fast"
        port = 30003
        model = "fast-local"
        engine = "vllm"
        gpu_role = "dark-fast"
        vram_mib = 20480
        residency = "on-demand"
        up = "docker compose -f {dir}/compose.yml up -d fast"

        [[serve]]
        name = "exp"
        container = "vllm-exp"
        port = 30002
        model = "heavy-local"
        engine = "vllm"
        gpu_role = "dark-fast"
        vram_mib = 16384
        residency = "evictable"
        router_tier = "heavy-local"
        up = "docker compose -f {dir}/compose.yml up -d exp"
    """), encoding="utf-8")
    return str(path)


def _docker_run(states, journal):
    """Fake docker seam: inspect answers from `states`; a stop is journaled
    (ordering evidence) and flips the container to exited."""
    def run(argv, **kwargs):
        if isinstance(argv, list) and argv[:2] == ["docker", "inspect"]:
            state = states.get(argv[-1], "absent")
            if state == "absent":
                return types.SimpleNamespace(
                    returncode=1, stdout="", stderr="Error: No such object")
            return types.SimpleNamespace(returncode=0, stdout=state + "\n", stderr="")
        if isinstance(argv, list) and argv[:2] == ["docker", "stop"]:
            journal.append(("stop", argv[-1]))
            states[argv[-1]] = "exited"
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    return run


def _admission_transition(routing, journal, drain_started=None):
    """The eviction step seam wired to a REAL RoutingBackend (returncode
    semantics, matching the deployed router CLI boundary it stands in for)."""
    def transition(action, tier_id, timeout=None):
        if action == "quiesce":
            routing.quiesce_tier(tier_id)
            journal.append(("quiesce", tier_id))
            return 0
        if action == "drain":
            if drain_started is not None:
                drain_started.set()
            outcome = routing.drain_tier(tier_id, timeout)
            journal.append(("drained", tier_id, outcome["drained"]))
            return 0 if outcome["drained"] else 1
        if action == "readmit":
            outcome = routing.readmit_tier(tier_id)
            journal.append(("readmit", tier_id, outcome["readmitted"]))
            return 0 if outcome["readmitted"] else 1
        raise AssertionError("unexpected transition action %r" % action)

    return transition


def test_eviction_drains_the_in_flight_admission_lease_before_container_stop(tmp_path):
    from anvil_serving import serves as serves_mod

    victim_backend = _BlockingBackend()
    routing = _routing(victim_backend, _TextBackend("FAST"))
    loaded = serves_mod.load_manifest(_eviction_manifest(tmp_path))

    result = []
    worker = threading.Thread(target=lambda: result.append(
        "".join(routing.generate(_request("heavy-only")))))
    worker.start()
    assert victim_backend.entered.wait(1)
    assert routing._admission.snapshot("heavy-local").active_requests == 1

    journal = []
    drain_started = threading.Event()

    def release_after_drain_starts():
        drain_started.wait(2)
        victim_backend.release.set()

    releaser = threading.Thread(target=release_after_drain_starts)
    releaser.start()

    states = {"vllm-exp": "running"}
    rc = serves_mod.cmd_up(
        loaded, ["fast"], evict=True, drain_timeout=5,
        _transition=_admission_transition(routing, journal, drain_started),
        _run=_docker_run(states, journal))
    worker.join(1)
    releaser.join(1)

    assert rc == 0
    assert result == ["HEAVY"]              # the in-flight generation FINISHED
    # Drain genuinely waited on the victim's AdmissionLease (the generation
    # was only released after the drain began), and the stop came after it:
    assert journal[:3] == [
        ("quiesce", "heavy-local"),
        ("drained", "heavy-local", True),
        ("stop", "vllm-exp"),
    ]
    assert states["vllm-exp"] == "exited"
    snapshot = routing._admission.snapshot("heavy-local")
    assert snapshot.active_requests == 0
    # The evicted tier stays quiesced: readmission is the guarded transition.
    assert snapshot.quiesced is True


def test_eviction_drain_timeout_aborts_without_operating_containers(tmp_path):
    from anvil_serving import serves as serves_mod

    victim_backend = _BlockingBackend()   # never released: drain must time out
    routing = _routing(victim_backend, _TextBackend("FAST"))
    loaded = serves_mod.load_manifest(_eviction_manifest(tmp_path))

    stream = routing.generate(_request("heavy-only"))
    assert routing._admission.snapshot("heavy-local").active_requests == 1

    journal = []
    states = {"vllm-exp": "running"}
    rc = serves_mod.cmd_up(
        loaded, ["fast"], evict=True, drain_timeout=0.05,
        _transition=_admission_transition(routing, journal),
        _run=_docker_run(states, journal))

    assert rc == 2
    assert states["vllm-exp"] == "running"  # bounded abort: NO container op
    assert journal == [
        ("quiesce", "heavy-local"),
        ("drained", "heavy-local", False),
        # Guarded readmit cannot prove identity readiness here, so the
        # compensation leaves admission fail-closed — quiesced, not half-open.
        ("readmit", "heavy-local", False),
    ]
    assert routing._admission.snapshot("heavy-local").quiesced is True
    stream.close()
