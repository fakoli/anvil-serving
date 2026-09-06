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
    replica_identity_passed,
)
from anvil_serving.router.config import ConfigError, ReplicaIdentity, ReplicaMember, RouterConfig, Tier
from anvil_serving.router.internal import InternalRequest, Message, NoAvailableTierError
from anvil_serving.router.model_capacity import replica_metadata
from anvil_serving.router.replica_scheduler import (
    PressureFreshness,
    PressureSignalState,
    ReplicaPressure,
)
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


def test_replica_request_uses_exact_member_and_excludes_invalid_peer():
    exact = AvailabilityResult(
        True, "ready", "identity_passed", "replica-model", "replica-model"
    )
    invalid = AvailabilityResult(True, "ready", "health_passed")
    tier, routing, first, second = _replica_routing_with_readiness(
        {"member-a": invalid, "member-b": exact}
    )
    try:
        assert "".join(routing.generate(_request("replica.stream"))) == "B"
        assert first.calls == 0
        assert second.calls == 1
        decision = routing._decision_log.last
        assert decision is not None
        assert decision.served_tier == tier.id
        assert decision.replica_member_id == "member-b"
        assert decision.replica_selection == "identity_passed"
    finally:
        routing.close()


def test_replica_identity_guard_negative_control(monkeypatch):
    invalid = AvailabilityResult(True, "ready", "health_passed")
    monkeypatch.setattr(router_serve, "replica_identity_passed", lambda tier, result: True)
    _tier, routing, first, second = _replica_routing_with_readiness(
        {"member-a": invalid, "member-b": invalid}
    )
    try:
        with pytest.raises(AssertionError, match="weakened guard dispatched"):
            output = "".join(routing.generate(_request("replica.stream")))
            assert output == "" and first.calls == second.calls == 0, (
                "weakened guard dispatched"
            )
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


def test_capacity_scheduler_integrated_exact_ordering_and_freshness_expiry():
    tier, routing, pressure, members = _capacity_routing_for_ordering()
    ready = {
        member.id: AvailabilityResult(True, "ready", "identity_passed")
        for member in tier.replicas
    }
    seed = routing._admission.acquire_member(
        tier.id,
        ready,
        {"member-a": _fresh_pressure(1_000_000), "member-b": _fresh_pressure(0)},
    )
    assert seed is not None and seed.member_id == "member-b"
    try:
        pressure.values = {"member-a": ReplicaPressure(), "member-b": _fresh_pressure(0)}
        assert "".join(routing.generate(_request("replica.stream"))) == "member-a"
        decision = routing._decision_log.last.replica_scheduler
        assert decision.selected_member_id == "member-a"
        assert [
            (score.member_id, score.local_numerator, score.local_denominator,
             score.upstream_unknown)
            for score in decision.scores
        ] == [
            ("member-a", 0, 2, True),
            ("member-b", 1, 2, False),
        ]
    finally:
        seed.release()

    # With equal local ratios, fresh evidence wins over unknown evidence.
    assert "".join(routing.generate(_request("replica.stream"))) == "member-b"
    assert routing._decision_log.last.replica_scheduler.selected_member_id == "member-b"

    # With equal local and unknown pressure, the full lexical membership cursor rotates.
    pressure.values = {"member-a": ReplicaPressure(), "member-b": ReplicaPressure()}
    assert "".join(routing.generate(_request("replica.stream"))) == "member-a"
    assert "".join(routing.generate(_request("replica.stream"))) == "member-b"
    assert members["member-a"].calls == 2
    assert members["member-b"].calls == 2
    routing.close()

    # A completed pressure owner may age a sample without collecting. Fresh
    # pressure is decisive at five seconds; one microsecond later it is stale
    # and the lexical cursor wins.
    tier, routing, pressure, _members = _capacity_routing_for_ordering()
    now = [5.0]

    class ExpiringPressure:
        def snapshot(self, _tier_id):
            freshness = (
                PressureFreshness.FRESH if now[0] <= 5.0 else PressureFreshness.STALE
            )
            if freshness is PressureFreshness.FRESH:
                return {"member-a": _fresh_pressure(900_000), "member-b": _fresh_pressure(100_000)}
            return {
                "member-a": ReplicaPressure(freshness),
                "member-b": ReplicaPressure(freshness),
            }

        def close(self):
            return None

    routing._replica_pressure = ExpiringPressure()
    assert "".join(routing.generate(_request("replica.stream"))) == "member-b"
    now[0] = 5.000001
    assert "".join(routing.generate(_request("replica.stream"))) == "member-a"
    assert routing._decision_log.records[-2].replica_scheduler.scores[0].freshness.value == "fresh"
    assert routing._decision_log.records[-1].replica_scheduler.scores[0].freshness.value == "stale"
    routing.close()


def test_capacity_scheduler_twenty_way_workload_respects_ceilings_drains_and_restores(
    tmp_path,
):
    attempts = 20
    tier = replace(
        _replica_tier(),
        replica_strategy="capacity",
        max_concurrency=4,
        replicas=tuple(
            replace(member, max_concurrency=2)
            for member in _replica_tier().replicas
        ),
    )
    config = RouterConfig(tiers=(tier,), model_routes={"replica.stream": tier.id})
    workload = _ConcurrentReplicaWorkload(attempts)
    members = {
        member.id: _HeldReplicaMember(member.id, workload)
        for member in tier.replicas
    }
    routing = RoutingBackend(
        config,
        {tier.id: ReplicaRuntime(members)},
        availability=_ReplicaReadiness(),
        capacity_metrics=lambda _tier: pytest.fail("no collection in synthetic workload"),
    )
    routing._replica_pressure.close()
    routing._replica_pressure = _CompletedUnknownPressure(members)
    start = threading.Barrier(attempts)
    results = []
    failures = []
    result_lock = threading.Lock()

    def run_attempt():
        try:
            start.wait(timeout=5)
            value = "".join(routing.generate(_request("replica.stream")))
            with result_lock:
                results.append(value)
        except NoAvailableTierError:
            workload.reject()
        except BaseException as exc:
            with result_lock:
                failures.append(exc)
            workload.reject()

    workers = [threading.Thread(target=run_attempt) for _ in range(attempts)]
    member_drained = []
    tier_drained = []
    drain_waits = threading.Event()
    drain_wait_count = 0
    condition = routing._admission._conditions[tier.id]
    original_wait = condition.wait

    def observed_wait(timeout=None):
        nonlocal drain_wait_count
        drain_wait_count += 1
        if drain_wait_count >= 2:
            drain_waits.set()
        return original_wait(timeout)

    condition.wait = observed_wait
    member_drainer = threading.Thread(
        target=lambda: member_drained.append(
            routing.drain_tier(tier.id, 5, member_id="member-a")
        )
    )
    tier_drainer = threading.Thread(
        target=lambda: tier_drained.append(routing.drain_tier(tier.id, 5))
    )
    try:
        for worker in workers:
            worker.start()
        assert workload.all_decided.wait(timeout=5)
        assert workload.entered == 4
        assert workload.rejected == 16
        assert workload.maximum == 4
        assert workload.member_maximum == {"member-a": 2, "member-b": 2}
        snapshot = routing._admission.snapshot(tier.id)
        assert snapshot.active_requests == 4
        assert dict(snapshot.member_active_requests) == {"member-a": 2, "member-b": 2}

        routing.quiesce_tier(tier.id, "maintenance", member_id="member-a")
        routing.quiesce_tier(tier.id, "maintenance")
        member_drainer.start()
        tier_drainer.start()
        assert drain_waits.wait(timeout=5)
        assert member_drained == [] and tier_drained == []
    finally:
        workload.release.set()
        for worker in workers:
            worker.join(timeout=5)
        member_drainer.join(timeout=5)
        tier_drainer.join(timeout=5)
        condition.wait = original_wait

    assert all(not worker.is_alive() for worker in workers)
    assert not member_drainer.is_alive() and not tier_drainer.is_alive()
    assert failures == []
    assert sorted(results) == ["member-a", "member-a", "member-b", "member-b"]
    assert member_drained[0]["drained"] is True
    assert tier_drained[0]["drained"] is True
    snapshot = routing._admission.snapshot(tier.id)
    assert snapshot.active_requests == 0
    assert dict(snapshot.member_active_requests) == {"member-a": 0, "member-b": 0}

    path = tmp_path / "synthetic-intent.json"
    router_serve._write_admission_intent(str(path), routing._admission)
    restored = router_serve._durable_admission(str(path), config)
    assert restored.snapshot(tier.id).quiesced
    assert restored.member_snapshot(tier.id, "member-a").quiesced
    restored.readmit_member(tier.id, "member-a")
    assert restored.snapshot(tier.id).quiesced
    assert not restored.member_snapshot(tier.id, "member-a").quiesced
    restored.quiesce_member(tier.id, "member-a", "maintenance")
    restored.readmit(tier.id)
    assert not restored.snapshot(tier.id).quiesced
    assert restored.member_snapshot(tier.id, "member-a").quiesced
    routing.close()


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


def test_oversized_or_duplicate_intent_refuses_with_fixed_safe_error(tmp_path):
    path = tmp_path / "intent.json"
    _, config = _replica_config()
    payloads = [
        b'{"version":1,"tiers":{},"tiers":{}}',
        b'{"version":1,"tiers":{},"members":{}}' + b" " * (1024 * 1024),
    ]
    for payload in payloads:
        path.write_bytes(payload)
        with pytest.raises(ConfigError) as exc_info:
            router_serve._durable_admission(str(path), config)
        assert "fix or delete" in str(exc_info.value)
        assert payload[:24].decode("ascii") not in str(exc_info.value)


def test_valid_removed_member_intent_is_ignored_after_full_validation(tmp_path):
    path = tmp_path / "intent.json"
    tier, config = _replica_config()
    path.write_text(json.dumps({
        "version": 1,
        "tiers": {},
        "members": {
            "retired-tier": {"retired-member": "maintenance"},
            tier.id: {"retired-member": "promotion"},
        },
    }), encoding="utf-8")

    admission = router_serve._durable_admission(str(path), config)

    assert all(not member.quiesced for member in admission.snapshot(tier.id).members)
    assert path.read_text(encoding="utf-8") == '{"tiers":{},"version":1}'


def test_restore_suppresses_callbacks_until_combined_state_is_complete(tmp_path, monkeypatch):
    path = tmp_path / "intent.json"
    tier, config = _replica_config()
    path.write_text(json.dumps({
        "version": 1,
        "tiers": {tier.id: {"state": "quiesced", "reason": "maintenance"}},
        "members": {tier.id: {"member-a": "promotion"}},
    }), encoding="utf-8")
    writes = []
    original = router_serve._write_admission_intent

    def observe(path, admission, *, write_lock=None):
        snapshot = admission.snapshot(tier.id)
        writes.append((snapshot.quiesced, snapshot.members[0].quiesced))
        return original(path, admission, write_lock=write_lock)

    monkeypatch.setattr(router_serve, "_write_admission_intent", observe)

    router_serve._durable_admission(str(path), config)

    assert writes == [(True, True)]


def test_failed_replace_preserves_old_file_and_cleans_only_own_temp(tmp_path, monkeypatch):
    path = tmp_path / "intent.json"
    path.write_bytes(b"old-intent")
    unrelated = tmp_path / ".intent.json.unrelated.tmp"
    unrelated.write_bytes(b"keep")
    tier, config = _replica_config()
    admission = router_serve._configured_admission(config)
    admission.quiesce_member(tier.id, "member-a", "maintenance")
    sources = []
    snapshot_calls = 0
    original_snapshots = admission.snapshots

    def counted_snapshots():
        nonlocal snapshot_calls
        snapshot_calls += 1
        return original_snapshots()

    def fail_replace(source, target):
        sources.append(source)
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(router_serve.os, "replace", fail_replace)
    monkeypatch.setattr(admission, "snapshots", counted_snapshots)
    with pytest.raises(OSError, match="synthetic replace failure"):
        router_serve._write_admission_intent(str(path), admission)

    assert path.read_bytes() == b"old-intent"
    assert snapshot_calls == 1
    assert unrelated.read_bytes() == b"keep"
    assert len(sources) == 1 and sources[0] != str(path) + ".tmp"
    assert sorted(tmp_path.iterdir()) == [unrelated, path]


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
    _AvailabilityResultSubclass(
        True, "ready", "identity_passed", "replica-model", "replica-model"
    ),
    AvailabilityResult(1, "ready", "identity_passed", "replica-model", "replica-model"),
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


def test_exact_replica_identity_predicate_matches_projection_and_readmission():
    tier, routing = _replica_stream_routing(_TextBackend("a"), _TextBackend("b"))
    exact = AvailabilityResult(
        True, "ready", "identity_passed", tier.model, tier.model
    )
    invalid = _AvailabilityResultSubclass(
        True, "ready", "identity_passed", tier.model, tier.model
    )
    try:
        assert replica_identity_passed(tier, exact) is True
        assert replica_identity_passed(tier, invalid) is False
        metadata, readiness = replica_metadata(
            tier, _MemberResults({"member-a": invalid, "member-b": exact})
        )
        assert [row["readiness"]["loaded"] for row in metadata["members"]] == [
            False,
            True,
        ]
        assert readiness.available is True
        routing.quiesce_tier(tier.id, member_id="member-a")
        routing._availability = _MemberResults(
            {"member-a": exact, "member-b": exact}
        )
        assert routing.readmit_tier(tier.id, member_id="member-a")["readmitted"] is True
        routing.quiesce_tier(tier.id, member_id="member-b")
        routing._availability = _MemberResults(
            {"member-a": exact, "member-b": invalid}
        )
        assert routing.readmit_tier(tier.id, member_id="member-b")["readmitted"] is False
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
