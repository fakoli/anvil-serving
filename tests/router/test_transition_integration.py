"""Hermetic one-workstation/two-GPU transition behavior."""
from __future__ import annotations

import json
import textwrap
import threading
import types
from dataclasses import replace

import pytest

import anvil_serving.router.serve as router_serve
from anvil_serving.router.availability import (
    AlwaysAvailable,
    AvailabilityResult,
)
from anvil_serving.router.config import ConfigError, ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
from anvil_serving.router.replica_scheduler import (
    PressureFreshness,
    PressureSignalState,
    ReplicaPressure,
)
from anvil_serving.router.serve import ReplicaRuntime, RoutingBackend


@pytest.fixture(autouse=True)
def _isolated_operator_home(monkeypatch, tmp_path):
    """Keep every transition's owner locks, recovery fences and policy local."""
    home = tmp_path / ".anvil-serving"
    monkeypatch.setenv("ANVIL_SERVING_HOME", str(home))
    return home


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


def test_heavy_only_request_fails_closed_while_quiesced():
    routing = _routing(_TextBackend("HEAVY"), _TextBackend("FAST"))
    routing.quiesce_tier("primary-local")

    with pytest.raises(NoAvailableTierError) as exc:
        routing.generate(_request("llm.primary"))
    assert exc.value.kind == "unavailable"


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


def _replica_tier():
    return Tier(
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


def _replica_stream_routing(blocking, peer):
    tier = _replica_tier()
    return tier, RoutingBackend(
        RouterConfig(tiers=(tier,), model_routes={"replica.stream": tier.id}),
        {tier.id: ReplicaRuntime({"member-a": blocking, "member-b": peer})},
        availability=_ReplicaReadiness(),
    )


class _AvailabilityResultSubclass(AvailabilityResult):
    pass


class _MemberResults:
    def __init__(self, results):
        self.results = results

    def check_member(self, tier, member_id):
        result = self.results[member_id]
        if isinstance(result, BaseException):
            raise result
        return result


def _replica_routing_with_readiness(results):
    tier = _replica_tier()
    first = _TextBackend("A")
    second = _TextBackend("B")
    routing = RoutingBackend(
        RouterConfig(tiers=(tier,), model_routes={"replica.stream": tier.id}),
        {tier.id: ReplicaRuntime({"member-a": first, "member-b": second})},
        availability=_MemberResults(results),
    )
    return tier, routing, first, second


@pytest.mark.parametrize(
    "invalid",
    [
        AvailabilityResult(True, "ready", "health_passed"),
        AvailabilityResult(
            True, "ready", "identity_passed", "replica-model", "other-model"
        ),
        AvailabilityResult(
            True, "unavailable", "identity_mismatch", "replica-model", None
        ),
        _AvailabilityResultSubclass(
            True, "ready", "identity_passed", "replica-model", "replica-model"
        ),
        AvailabilityResult(
            1, "ready", "identity_passed", "replica-model", "replica-model"
        ),
        {"available": True},
    ],
    ids=[
        "health-only",
        "observed-mismatch",
        "state-inconsistent",
        "result-subclass",
        "truthy-non-bool",
        "malformed-result",
    ],
)
def test_replica_request_requires_exact_member_identity_before_dispatch(invalid):
    tier, routing, first, second = _replica_routing_with_readiness(
        {"member-a": invalid, "member-b": invalid}
    )
    try:
        with pytest.raises(NoAvailableTierError):
            "".join(routing.generate(_request("replica.stream")))
        assert first.calls == second.calls == 0
        decision = routing._decision_log.last
        assert decision is not None
        assert decision.requested_tier == tier.id
        assert decision.served_tier is None
        assert decision.replica_member_id is None
        assert decision.replica_selection == "not_admitted"
    finally:
        routing.close()


def _replica_config():
    tier = _replica_tier()
    return tier, RouterConfig(
        tiers=(tier,), model_routes={"replica.stream": tier.id}
    )


class _ConcurrentReplicaWorkload:
    def __init__(self, attempts):
        self.attempts = attempts
        self.condition = threading.Condition()
        self.release = threading.Event()
        self.all_decided = threading.Event()
        self.entered = 0
        self.rejected = 0
        self.active = 0
        self.maximum = 0
        self.member_active = {"member-a": 0, "member-b": 0}
        self.member_maximum = {"member-a": 0, "member-b": 0}

    def _decided_locked(self):
        if self.entered + self.rejected == self.attempts:
            self.all_decided.set()

    def enter(self, member_id):
        with self.condition:
            self.entered += 1
            self.active += 1
            self.member_active[member_id] += 1
            self.maximum = max(self.maximum, self.active)
            self.member_maximum[member_id] = max(
                self.member_maximum[member_id], self.member_active[member_id]
            )
            self._decided_locked()

    def reject(self):
        with self.condition:
            self.rejected += 1
            self._decided_locked()

    def leave(self, member_id):
        with self.condition:
            self.active -= 1
            self.member_active[member_id] -= 1


class _HeldReplicaMember:
    def __init__(self, member_id, workload):
        self.member_id = member_id
        self.workload = workload
        self.calls = 0

    def generate(self, _request):
        self.calls += 1
        self.workload.enter(self.member_id)
        try:
            assert self.workload.release.wait(timeout=5)
            yield self.member_id
        finally:
            self.workload.leave(self.member_id)


class _CompletedUnknownPressure:
    def __init__(self, member_ids):
        self.member_ids = tuple(member_ids)

    def snapshot(self, _tier_id):
        return {member_id: ReplicaPressure() for member_id in self.member_ids}

    def close(self):
        return None


class _MutableCompletedPressure:
    def __init__(self, values):
        self.values = values

    def snapshot(self, _tier_id):
        return dict(self.values)

    def close(self):
        return None


def _fresh_pressure(value):
    return ReplicaPressure(
        PressureFreshness.FRESH,
        value,
        PressureSignalState.VALID,
        PressureSignalState.MISSING,
    )


def _capacity_routing_for_ordering():
    tier = replace(
        _replica_tier(),
        replica_strategy="capacity",
        replicas=tuple(
            replace(member, max_concurrency=2)
            for member in _replica_tier().replicas
        ),
    )
    members = {"member-a": _TextBackend("member-a"), "member-b": _TextBackend("member-b")}
    routing = RoutingBackend(
        RouterConfig(tiers=(tier,), model_routes={"replica.stream": tier.id}),
        {tier.id: ReplicaRuntime(members)},
        availability=_ReplicaReadiness(),
        capacity_metrics=lambda _tier: pytest.fail("no metrics collection"),
    )
    routing._replica_pressure.close()
    pressure = _MutableCompletedPressure({
        "member-a": ReplicaPressure(), "member-b": ReplicaPressure(),
    })
    routing._replica_pressure = pressure
    return tier, routing, pressure, members


def test_member_intent_survives_restart_and_readmission_is_independently_scoped(tmp_path):
    path = tmp_path / "intent.json"
    tier, config = _replica_config()
    admission = router_serve._durable_admission(str(path), config)
    admission.quiesce(tier.id, "maintenance")
    admission.quiesce_member(tier.id, "member-a", "promotion")
    admission.quiesce_member(tier.id, "member-b", "maintenance")

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "tiers": {tier.id: {"state": "quiesced", "reason": "maintenance"}},
        "members": {
            tier.id: {"member-a": "promotion", "member-b": "maintenance"}
        },
    }

    restored = router_serve._durable_admission(str(path), config)
    assert restored.snapshot(tier.id).quiesced
    assert restored.member_snapshot(tier.id, "member-a").reason == "promotion"
    assert restored.member_snapshot(tier.id, "member-b").quiesced

    restored.readmit(tier.id)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["tiers"] == {}
    assert set(persisted["members"][tier.id]) == {"member-a", "member-b"}
    restored.readmit_member(tier.id, "member-a")
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["members"] == {tier.id: {"member-b": "maintenance"}}


def test_tier_promotion_is_not_restored_but_member_promotion_is(tmp_path):
    path = tmp_path / "intent.json"
    tier, config = _replica_config()
    path.write_text(json.dumps({
        "version": 1,
        "tiers": {tier.id: {"state": "quiesced", "reason": "promotion"}},
        "members": {tier.id: {"member-a": "promotion"}},
    }), encoding="utf-8")

    admission = router_serve._durable_admission(str(path), config)

    assert not admission.snapshot(tier.id).quiesced
    assert admission.member_snapshot(tier.id, "member-a").quiesced
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "tiers": {},
        "members": {tier.id: {"member-a": "promotion"}},
    }


def test_legacy_tier_fallback_and_byte_shape_remain_compatible(tmp_path):
    path = tmp_path / "intent.json"
    tier, config = _replica_config()
    path.write_text(json.dumps({
        "version": 1,
        "tiers": {tier.id: {"state": "quiesced", "reason": None}},
    }), encoding="utf-8")

    admission = router_serve._durable_admission(str(path), config)

    assert admission.snapshot(tier.id).reason == "restored"
    assert path.read_text(encoding="utf-8") == (
        '{"tiers":{"replica-primary":{"reason":"restored",'
        '"state":"quiesced"}},"version":1}'
    )


@pytest.mark.parametrize("raw", [
    b'{"version":1,"tiers":{},"members":null}',
    b'{"version":1,"tiers":{},"members":[]}',
    b'{"version":1,"tiers":{},"members":{"":{"member-a":"maintenance"}}}',
    b'{"version":1,"tiers":{},"members":{"retired":{"bad/id":"maintenance"}}}',
    b'{"version":1,"tiers":{},"members":{"retired":{"member-a":{}}}}',
    b'{"version":1,"tiers":{},"members":{"retired":{"member-a":"bad reason"}}}',
    b'{"version":1,"tiers":{},"members":{"retired":{"member-a":"a","member-a":"b"}}}',
])
def test_malformed_member_intent_refuses_before_any_restore_or_rewrite(tmp_path, raw):
    path = tmp_path / "intent.json"
    tier, config = _replica_config()
    document = raw.replace(b'"tiers":{}', (
        b'"tiers":{"' + tier.id.encode("ascii")
        + b'":{"state":"quiesced","reason":"maintenance"}}'
    ))
    path.write_bytes(document)

    with pytest.raises(ConfigError, match="admission intent"):
        router_serve._durable_admission(str(path), config)

    assert path.read_bytes() == document


def test_concurrent_member_callbacks_serialize_snapshot_and_atomic_write(tmp_path, monkeypatch):
    path = tmp_path / "intent.json"
    tier, config = _replica_config()
    admission = router_serve._durable_admission(str(path), config)
    first_replace = threading.Event()
    release_first = threading.Event()
    second_callback = threading.Event()
    original_replace = router_serve.os.replace
    original_callback = admission._on_state_change
    replace_count = 0

    def blocked_replace(source, target):
        nonlocal replace_count
        replace_count += 1
        if replace_count == 1:
            first_replace.set()
            assert release_first.wait(timeout=5)
        original_replace(source, target)

    def observed_callback(tier_id):
        if admission.member_snapshot(tier.id, "member-b").quiesced:
            second_callback.set()
        original_callback(tier_id)

    monkeypatch.setattr(router_serve.os, "replace", blocked_replace)
    admission._on_state_change = observed_callback
    first = threading.Thread(target=lambda: admission.quiesce_member(
        tier.id, "member-a", "maintenance"
    ))
    second = threading.Thread(target=lambda: admission.quiesce_member(
        tier.id, "member-b", "promotion"
    ))
    try:
        first.start()
        assert first_replace.wait(timeout=5)
        second.start()
        assert second_callback.wait(timeout=5)
    finally:
        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert json.loads(path.read_text(encoding="utf-8"))["members"] == {
        tier.id: {"member-a": "maintenance", "member-b": "promotion"}
    }


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


def test_eviction_drain_timeout_aborts_without_operating_containers(
    tmp_path, _isolated_operator_home,
):
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

    assert (_isolated_operator_home / "locks" / "serves-switch-promotion.lock").is_file()
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
