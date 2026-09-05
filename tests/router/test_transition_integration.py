"""Hermetic one-workstation/two-GPU transition behavior."""
from __future__ import annotations

import json
import textwrap
import threading
import types

import pytest

from anvil_serving.router.availability import AlwaysAvailable, AvailabilityResult
from anvil_serving.router.config import ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
from anvil_serving.router.serve import ReplicaRuntime, RoutingBackend


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
    heavy = _tier("primary-local", 30002)
    fast = _tier("auxiliary-local", 30003)
    config = RouterConfig(
        tiers=(heavy, fast),
        model_routes={
            "llm.primary": heavy.id,
            "llm.voice": fast.id,
        },
    )
    return RoutingBackend(
        config,
        {heavy.id: heavy_backend, fast.id: fast_backend},
        availability=AlwaysAvailable(),
    )


def _request(model="llm.primary"):
    return InternalRequest(model=model, messages=[Message("user", "reply")])


def test_heavy_drains_while_another_direct_route_uses_resident_fast():
    heavy = _BlockingBackend()
    fast = _TextBackend("FAST")
    routing = _routing(heavy, fast)
    result = []
    worker = threading.Thread(target=lambda: result.append("".join(routing.generate(_request()))))
    worker.start()
    assert heavy.entered.wait(1)

    snapshot = routing.quiesce_tier("primary-local")
    assert snapshot["active_requests"] == 1
    assert "".join(routing.generate(_request("llm.voice"))) == "FAST"
    assert fast.calls == 1

    heavy.release.set()
    worker.join(1)
    assert result == ["HEAVY"]
    assert routing.drain_tier("primary-local", 1)["drained"] is True
    assert any(
        record.served_tier == "auxiliary-local"
        for record in routing._decision_log.records
    )


def test_heavy_only_request_fails_closed_while_quiesced():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    routing.quiesce_tier("primary-local")

    with pytest.raises(NoAvailableTierError) as exc:
        routing.generate(_request("llm.primary"))
    assert exc.value.kind == "unavailable"


def test_direct_stream_close_releases_full_generation_lease():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    stream = routing.generate(_request())
    assert routing._admission.snapshot("primary-local").active_requests == 1
    assert next(stream) == "HEAVY"
    stream.close()
    assert routing._admission.snapshot("primary-local").active_requests == 0


def test_unadvanced_direct_stream_close_releases_admission_lease():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    stream = routing.generate(_request())
    assert routing._admission.snapshot("primary-local").active_requests == 1
    stream.close()
    assert routing._admission.snapshot("primary-local").active_requests == 0


# --------------------------------------------------------------------------- #
# Qualified replica sets T009 — compound member lease drains on stream end
# --------------------------------------------------------------------------- #
class _ReplicaReadiness:
    def __init__(self):
        self.calls = []
        self.invalidated = []

    def invalidate(self, tier_id):
        self.invalidated.append(tier_id)

    def check(self, tier):
        self.calls.append((tier.id, None))
        return AvailabilityResult(True, "ready", "identity_passed", tier.model, tier.model)

    def check_member(self, tier, member_id):
        self.calls.append((tier.id, member_id))
        return AvailabilityResult(True, "ready", "identity_passed", tier.model, tier.model)


def _replica_stream_routing(blocking, peer):
    tier = Tier(
        id="replica-primary",
        base_url="",
        model="replica-model",
        dialect="openai",
        context_limit=131072,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
        health_path="/health",
        model_identity=True,
        replicas=(
            ReplicaMember(
                "member-a", "http://127.0.0.1:33001/v1", "node-a",
                "resource-a", "qualification:a",
            ),
            ReplicaMember(
                "member-b", "http://127.0.0.1:33002/v1", "node-a",
                "resource-b", "qualification:b",
            ),
        ),
        replica_identity=ReplicaIdentity(
            model_revision="revision-1",
            engine_version="engine-1",
            image_digest="sha256:" + "1" * 64,
            config_fingerprint="sha256:" + "2" * 64,
        ),
    )
    return tier, RoutingBackend(
        RouterConfig(tiers=(tier,), model_routes={"replica.stream": tier.id}),
        {tier.id: ReplicaRuntime({"member-a": blocking, "member-b": peer})},
        availability=_ReplicaReadiness(),
    )


def test_replica_stream_drain_waits_for_compound_lease_then_releases_both_counts():
    selected = _BlockingBackend()
    peer = _TextBackend("must-not-run")
    tier, routing = _replica_stream_routing(selected, peer)
    completed = []
    worker = threading.Thread(target=lambda: completed.append(
        "".join(routing.generate(_request("replica.stream")))
    ))
    worker.start()
    assert selected.entered.wait(timeout=5)
    snapshot = routing._admission.snapshot(tier.id)
    assert snapshot.active_requests == 1
    assert snapshot.member_active_requests == (("member-a", 1), ("member-b", 0))

    routing.quiesce_tier(tier.id)
    timed_out = routing.drain_tier(tier.id, 0.001)
    assert timed_out["drained"] is False
    assert timed_out["timed_out"] is True
    assert routing._admission.snapshot(tier.id).active_requests == 1
    drain_started = threading.Event()
    drained = []

    def wait_for_drain() -> None:
        drain_started.set()
        drained.append(routing.drain_tier(tier.id, 5))

    drainer = threading.Thread(target=wait_for_drain)
    try:
        drainer.start()
        assert drain_started.wait(timeout=5)
        assert routing._admission.snapshot(tier.id).active_requests == 1
    finally:
        selected.release.set()
        worker.join(timeout=5)
        drainer.join(timeout=5)

    assert not worker.is_alive() and not drainer.is_alive()
    assert completed == ["HEAVY"]
    assert drained[0]["drained"] is True
    assert drained[0]["timed_out"] is False
    snapshot = routing._admission.snapshot(tier.id)
    assert snapshot.active_requests == 0
    assert snapshot.member_active_requests == (("member-a", 0), ("member-b", 0))
    assert peer.calls == 0


def test_member_and_tier_readmission_clear_only_their_own_quiesce_scope():
    tier, routing = _replica_stream_routing(_TextBackend("a"), _TextBackend("b"))
    ready = routing._availability
    try:
        row = routing.quiesce_tier(tier.id, "maintenance", member_id="member-a")
        assert row["member_id"] == "member-a" and row["state"] == "quiesced"
        assert not routing._admission.snapshot(tier.id).quiesced
        assert "".join(routing.generate(_request("replica.stream"))) == "b"
        routing.quiesce_tier(tier.id, "maintenance")
        ready.calls.clear()
        result = routing.readmit_tier(tier.id, member_id="member-a")
        assert result["readmitted"] is True
        assert result["status"]["tiers"][0]["member_id"] == "member-a"
        assert ready.calls == [(tier.id, "member-a")]
        assert ready.invalidated[-1] == tier.id
        assert routing._admission.snapshot(tier.id).quiesced
        assert not routing._admission.member_snapshot(tier.id, "member-a").quiesced
        with pytest.raises(NoAvailableTierError):
            routing.generate(_request("replica.stream"))
        routing.quiesce_tier(tier.id, "maintenance", member_id="member-a")
        assert routing.readmit_tier(tier.id)["readmitted"] is True
        assert not routing._admission.snapshot(tier.id).quiesced
        assert routing._admission.member_snapshot(tier.id, "member-a").quiesced
        assert "".join(routing.generate(_request("replica.stream"))) == "b"
    finally:
        routing.close()


def test_member_status_reads_only_selected_member_and_preserves_tier_status_shape():
    tier, routing = _replica_stream_routing(_TextBackend("a"), _TextBackend("b"))
    ready = routing._availability
    try:
        routing.quiesce_tier(tier.id, member_id="member-a")
        status = routing.transition_status(tier.id, member_id="member-a")
        assert len(status["tiers"]) == 1
        row = status["tiers"][0]
        assert row["member_id"] == "member-a" and row["state"] == "quiesced"
        assert row["expected_model"] == row["observed_model"] == tier.model
        assert ready.calls == [(tier.id, "member-a")]
        legacy_row = routing.transition_status(tier.id)["tiers"][0]
        assert "member_id" not in legacy_row and legacy_row["state"] == "admitting"
    finally:
        routing.close()


@pytest.mark.parametrize("readiness", [
    AvailabilityResult(True, "ready", "identity_passed"),
    AvailabilityResult(True, "ready", "identity_passed", "replica-model", "other-model"),
    AvailabilityResult(True, "ready", "configured", "replica-model", "replica-model"),
    AvailabilityResult(False, "unavailable", "identity_mismatch", "replica-model", "other-model"),
    RuntimeError("private upstream data"),
    {"available": True},
])
def test_member_readmit_requires_exact_identity_and_never_reprobes_or_reads_peer(readiness):
    tier, routing = _replica_stream_routing(_TextBackend("a"), _TextBackend("b"))
    calls = []

    class Ready:
        def check_member(self, tier, member_id):
            calls.append((tier.id, member_id))
            if isinstance(readiness, Exception):
                raise readiness
            return readiness

        def check(self, tier):
            raise AssertionError("aggregate readiness is forbidden")

    routing._availability = Ready()
    try:
        routing.quiesce_tier(tier.id, member_id="member-a")
        result = routing.readmit_tier(tier.id, member_id="member-a")
        assert result["readmitted"] is False
        assert routing._admission.member_snapshot(tier.id, "member-a").quiesced
        assert calls == [(tier.id, "member-a")]
        assert "private upstream data" not in json.dumps(result)
    finally:
        routing.close()


@pytest.mark.parametrize("member_id", ["unknown", "", " member-a", "member/a", 1, True, []])
def test_invalid_member_scopes_fail_before_admission_or_probe(member_id):
    tier, routing = _replica_stream_routing(_TextBackend("a"), _TextBackend("b"))
    try:
        before = routing._admission.snapshot(tier.id)
        for action in (
            lambda: routing.transition_status(tier.id, member_id=member_id),
            lambda: routing.quiesce_tier(tier.id, member_id=member_id),
            lambda: routing.drain_tier(tier.id, 1, member_id=member_id),
            lambda: routing.readmit_tier(tier.id, member_id=member_id),
        ):
            with pytest.raises(ValueError):
                action()
        assert routing._admission.snapshot(tier.id) == before
        assert routing._availability.calls == routing._availability.invalidated == []
    finally:
        routing.close()


def test_member_requires_explicit_replica_tier():
    routing = _routing(_TextBackend("a"), _TextBackend("b"))
    try:
        for tier_id in (None, "primary-local", "unknown"):
            for fn in (
                lambda: routing.transition_status(tier_id, member_id="member-a"),
                lambda: routing.quiesce_tier(tier_id, member_id="member-a"),
                lambda: routing.drain_tier(tier_id, 1, member_id="member-a"),
                lambda: routing.readmit_tier(tier_id, member_id="member-a"),
            ):
                with pytest.raises((KeyError, ValueError)):
                    fn()
        assert all(not row.quiesced for row in routing._admission.snapshots())
    finally:
        routing.close()


def test_member_drain_requires_own_quiesce_and_never_cancels_or_waits_for_peer():
    tier, routing = _replica_stream_routing(_TextBackend("a"), _TextBackend("b"))
    streams = [routing.generate(_request("replica.stream")) for _ in range(2)]
    try:
        routing.quiesce_tier(tier.id)
        with pytest.raises(ValueError, match="member must be quiesced"):
            routing.drain_tier(tier.id, 1, member_id="member-a")
        routing.quiesce_tier(tier.id, member_id="member-a")
        result = routing.drain_tier(tier.id, 0.001, member_id="member-a")
        assert result["timed_out"] and result["snapshot"]["active_requests"] == 1
        assert routing._admission.snapshot(tier.id).active_requests == 2
        streams[0].close()
        result = routing.drain_tier(tier.id, 1, member_id="member-a")
        assert result["drained"] and result["snapshot"]["member_id"] == "member-a"
        assert routing._admission.snapshot(tier.id).active_requests == 1
        assert routing.drain_tier(tier.id, 0.001)["timed_out"]
        streams[1].close()
        assert routing.drain_tier(tier.id, 1)["drained"]
    finally:
        for stream in streams:
            stream.close()
        routing.close()


def test_active_member_drain_refuses_readmit_until_real_lease_closes(monkeypatch):
    tier, routing = _replica_stream_routing(_TextBackend("a"), _TextBackend("b"))
    stream = routing.generate(_request("replica.stream"))
    routing.quiesce_tier(tier.id, member_id="member-a")
    waiting = threading.Event()
    condition = routing._admission._condition(tier.id)
    original_wait = condition.wait

    def wait(timeout):
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(condition, "wait", wait)
    results = []
    drainer = threading.Thread(target=lambda: results.append(
        routing.drain_tier(tier.id, 5, member_id="member-a")
    ))
    try:
        drainer.start()
        assert waiting.wait(2)
        with pytest.raises(ValueError, match="member drain is in progress"):
            routing.readmit_tier(tier.id, member_id="member-a")
        assert routing._admission.snapshot(tier.id).active_requests == 1
    finally:
        stream.close()
        drainer.join(5)
        routing.close()
    assert not drainer.is_alive()
    assert results[0]["drained"]


def test_guarded_readmit_requires_identity_readiness_configuration():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    routing.quiesce_tier("primary-local")
    result = routing.readmit_tier("primary-local")
    assert result["readmitted"] is False
    assert result["reason"] == "identity_not_configured"
    assert routing._admission.snapshot("primary-local").quiesced is True


def test_successful_readmit_keeps_the_identity_result_cached():
    heavy = Tier(
        **{
            **_tier("primary-local", 30002).__dict__,
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
            model_routes={"llm.primary": heavy.id},
        ),
        {heavy.id: _TextBackend("HEAVY")},
        availability=ready,
    )
    routing.quiesce_tier(heavy.id)
    assert routing.readmit_tier(heavy.id)["readmitted"] is True
    assert ready.invalidated == [heavy.id, heavy.id]


def test_guarded_readmit_rejects_available_without_exact_identity_evidence():
    heavy = Tier(
        **{
            **_tier("primary-local", 30002).__dict__,
            "health_path": "/health",
            "model_identity": True,
        }
    )
    routing = RoutingBackend(
        RouterConfig(
            tiers=(heavy,),
            model_routes={"llm.primary": heavy.id},
        ),
        {heavy.id: _TextBackend("HEAVY")},
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
        runtime = "docker"
        port = 30003
        model = "auxiliary-local"
        engine = "vllm"
        gpu_role = "dark-fast"
        vram_mib = 20480
        residency = "on-demand"
        up = "docker compose -f {dir}/compose.yml up -d fast"

        [[serve]]
        name = "exp"
        container = "vllm-exp"
        runtime = "docker"
        port = 30002
        model = "primary-local"
        engine = "vllm"
        gpu_role = "dark-fast"
        vram_mib = 16384
        residency = "evictable"
        router_tier = "primary-local"
        up = "docker compose -f {dir}/compose.yml up -d exp"
    """), encoding="utf-8")
    return str(path)


def _docker_run(states, journal):
    """Fake docker seam: inspect answers from `states`; a stop is journaled
    (ordering evidence) and flips the container to exited."""
    def run(argv, **kwargs):
        if isinstance(argv, list) and argv[:3] == ["docker", "ps", "-a"]:
            rows = [
                json.dumps({"Names": name, "State": state})
                for name, state in states.items()
                if state != "absent"
            ]
            return types.SimpleNamespace(
                returncode=0, stdout="\n".join(rows), stderr=""
            )
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
        "".join(routing.generate(_request("llm.primary")))))
    worker.start()
    assert victim_backend.entered.wait(1)
    assert routing._admission.snapshot("primary-local").active_requests == 1

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
        ("quiesce", "primary-local"),
        ("drained", "primary-local", True),
        ("stop", "vllm-exp"),
    ]
    assert states["vllm-exp"] == "exited"
    snapshot = routing._admission.snapshot("primary-local")
    assert snapshot.active_requests == 0
    # The evicted tier stays quiesced: readmission is the guarded transition.
    assert snapshot.quiesced is True


def test_eviction_drain_timeout_aborts_without_operating_containers(tmp_path):
    from anvil_serving import serves as serves_mod

    victim_backend = _BlockingBackend()   # never released: drain must time out
    routing = _routing(victim_backend, _TextBackend("FAST"))
    loaded = serves_mod.load_manifest(_eviction_manifest(tmp_path))

    stream = routing.generate(_request("llm.primary"))
    assert routing._admission.snapshot("primary-local").active_requests == 1

    journal = []
    states = {"vllm-exp": "running"}
    rc = serves_mod.cmd_up(
        loaded, ["fast"], evict=True, drain_timeout=0.05,
        _transition=_admission_transition(routing, journal),
        _run=_docker_run(states, journal))

    assert rc == 2
    assert states["vllm-exp"] == "running"  # bounded abort: NO container op
    assert journal == [
        ("quiesce", "primary-local"),
        ("drained", "primary-local", False),
        # Guarded readmit cannot prove identity readiness here, so the
        # compensation leaves admission fail-closed — quiesced, not half-open.
        ("readmit", "primary-local", False),
    ]
    assert routing._admission.snapshot("primary-local").quiesced is True
    stream.close()
