from dataclasses import FrozenInstanceError, replace
from itertools import permutations
import json
import threading

import pytest

from anvil_serving.router.replica_scheduler import (
    MAX_PRESSURE_PPM,
    PressureFreshness as Freshness,
    PressureSignalState as Signal,
    ReplicaCandidate,
    ReplicaDecisionReason,
    ReplicaPressure,
    normalize_replica_pressure,
    rank_replica_candidates,
)
from anvil_serving.router.model_capacity import MetricsSnapshot, ReplicaPressureCache
from tests.router.test_model_metadata import _replica_config


def _pressure(**kwargs):
    values = dict(observed_at=10, now_monotonic=10, successful=True,
                  requests_running=0, requests_waiting=0, scheduler_capacity=2)
    values.update(kwargs)
    return normalize_replica_pressure(**values)


def _candidate(member, *, active=0, ceiling=2, pressure=None, eligible=True):
    return ReplicaCandidate(member, eligible, active, ceiling, pressure or _pressure())


@pytest.mark.parametrize("values,ppm,requests,kv", [
    ({}, 0, Signal.VALID, Signal.MISSING),
    ({"requests_running": 1, "scheduler_capacity": 3}, 333334, Signal.VALID, Signal.MISSING),
    ({"requests_running": 1.0, "requests_waiting": 2.0}, 1500000, Signal.VALID, Signal.MISSING),
    ({"requests_running": 1_000_000_000, "requests_waiting": 1_000_000_000, "scheduler_capacity": 1},
     MAX_PRESSURE_PPM, Signal.VALID, Signal.MISSING),
    ({"kv_cache_usage_fraction": 0.25}, 250000, Signal.VALID, Signal.VALID),
    ({"requests_running": 1, "kv_cache_usage_fraction": 0.25}, 500000, Signal.VALID, Signal.VALID),
    ({"requests_waiting": None, "kv_cache_usage_fraction": 1}, 1000000, Signal.MISSING, Signal.VALID),
    ({"requests_running": None, "scheduler_capacity": None, "kv_cache_usage_fraction": 5e-324},
     1, Signal.MISSING, Signal.VALID),
])
def test_pressure_uses_conservative_ppm_without_clamping_request_backlog(values, ppm, requests, kv):
    pressure = _pressure(**values)
    assert pressure == ReplicaPressure(Freshness.FRESH, ppm, requests, kv)
    assert 0 <= pressure.pressure_ppm < 2**53


@pytest.mark.parametrize("field", ["requests_running", "requests_waiting", "scheduler_capacity", "kv_cache_usage_fraction"])
@pytest.mark.parametrize("invalid", [True, False, -1, float("nan"), float("inf"), "secret-marker", [], {}])
def test_invalid_optional_signal_never_becomes_fresh_zero(field, invalid):
    values = {"requests_running": None, "kv_cache_usage_fraction": 0.0, field: invalid}
    pressure = _pressure(**values)
    assert pressure.freshness is Freshness.UNKNOWN
    assert pressure.pressure_ppm is None
    assert (pressure.kv_state if field == "kv_cache_usage_fraction" else pressure.requests_state) is Signal.INVALID
    assert "secret-marker" not in repr(pressure)


@pytest.mark.parametrize("values", [
    {"requests_running": 0.5}, {"requests_waiting": 1_000_000_001},
    {"scheduler_capacity": 0}, {"scheduler_capacity": 2.0}, {"scheduler_capacity": 100001},
    {"kv_cache_usage_fraction": 1.01}, {"requests_running": 10**1000},
])
def test_numeric_type_range_and_integrality_defects_are_unknown(values):
    assert _pressure(**values).freshness is Freshness.UNKNOWN


@pytest.mark.parametrize("values,freshness", [
    ({"now_monotonic": 15}, Freshness.FRESH),
    ({"now_monotonic": 15.000001}, Freshness.STALE),
    ({"now_monotonic": 9}, Freshness.UNKNOWN),
    ({"observed_at": -1}, Freshness.UNKNOWN),
    ({"observed_at": None}, Freshness.UNKNOWN),
    ({"observed_at": float("nan")}, Freshness.UNKNOWN),
    ({"now_monotonic": float("inf")}, Freshness.UNKNOWN),
    ({"now_monotonic": 10**1000}, Freshness.UNKNOWN),
    ({"observed_at": True}, Freshness.UNKNOWN),
    ({"successful": False}, Freshness.FAILED),
    ({"successful": 1}, Freshness.UNKNOWN),
    ({"successful": "true"}, Freshness.UNKNOWN),
    ({"requests_running": None}, Freshness.UNKNOWN),
])
def test_freshness_boundaries_and_missing_signals(values, freshness):
    pressure = _pressure(**values)
    assert pressure.freshness is freshness
    assert (pressure.pressure_ppm is None) == (freshness is not Freshness.FRESH)


def test_lower_local_pressure_beats_fresh_telemetry_but_unknown_loses_at_equal_local_pressure():
    unknown = ReplicaPressure()
    candidates = (_candidate("a", pressure=unknown), _candidate("b", active=1))
    assert rank_replica_candidates(candidates, cursor=1).selected_member_id == "a"
    candidates = (candidates[0], _candidate("b"))
    decision = rank_replica_candidates(candidates, cursor=0)
    assert decision.selected_member_id == "b"
    assert [row.member_id for row in decision.scores] == ["b", "a"]
    assert decision.eligible_member_ids == ("a", "b")


def test_local_ratio_is_exact_and_upstream_backlog_above_one_remains_ordered():
    # Both floor to 10 local ppm, so approximate local scoring would pick a.
    candidates = (_candidate("a", active=1, ceiling=99999), _candidate("b", active=1, ceiling=100000))
    assert rank_replica_candidates(candidates, cursor=0).selected_member_id == "b"
    candidates = (_candidate("a", pressure=_pressure(requests_running=3)),
                  _candidate("b", pressure=_pressure(requests_running=4)))
    assert rank_replica_candidates(candidates, cursor=1).selected_member_id == "a"


def test_all_permutations_keep_full_membership_cursor_and_exact_decision():
    candidates = (_candidate("a"), _candidate("b"), _candidate("c", eligible=False))
    for cursor, selected in [(0, "a"), (1, "b"), (2, "a")]:
        reference = rank_replica_candidates(candidates, cursor=cursor)
        assert reference.selected_member_id == selected
        for ordering in permutations(candidates):
            assert rank_replica_candidates(ordering, cursor=cursor) == reference


def test_no_eligible_member_is_exhaustion_not_invalid_input():
    decision = rank_replica_candidates((_candidate("a", active=2), _candidate("b", eligible=False)), cursor=1)
    assert decision.to_dict() == {
        "selected_member_id": None, "eligible_member_ids": [], "scores": [], "reason": "no-eligible-member",
    }
    assert decision.reason is ReplicaDecisionReason.NO_ELIGIBLE_MEMBER


@pytest.mark.parametrize("changes", [
    {"member_id": "bad/secret"}, {"member_id": "a" * 65}, {"eligible": 1},
    {"active_requests": True}, {"active_requests": -1}, {"active_requests": 1_000_000_001},
    {"max_concurrency": 0}, {"max_concurrency": 100001}, {"pressure": {}},
])
def test_candidate_rejects_malformed_types_bounds_without_echo(changes):
    with pytest.raises(ValueError, match="^invalid replica scheduler input$"):
        replace(_candidate("a"), **changes)


@pytest.mark.parametrize("members,cursor", [
    ((), 0), ([], 0), ((_candidate("a"),), 0),
    ((_candidate("a"), _candidate("a")), 0),
    ((_candidate("a"), _candidate("b")), -1),
    ((_candidate("a"), _candidate("b")), 2),
    ((_candidate("a"), _candidate("b")), True),
    ((_candidate("a"), object()), 0),
    (tuple(_candidate(f"m{index}") for index in range(17)), 0),
])
def test_rank_refuses_bad_membership_and_cursor(members, cursor):
    with pytest.raises(ValueError, match="^invalid replica scheduler input$"):
        rank_replica_candidates(members, cursor=cursor)


def test_sixteen_member_bound_and_metadata_only_immutable_decision():
    candidates = tuple(_candidate(f"m{index:02}") for index in range(16))
    decision = rank_replica_candidates(candidates, cursor=15)
    assert decision.selected_member_id == "m15"
    assert len(decision.scores) == 16
    with pytest.raises(FrozenInstanceError):
        decision.selected_member_id = "another"
    with pytest.raises(FrozenInstanceError):
        decision.scores[0].local_numerator = 99
    serialized = json.dumps(decision.to_dict(), allow_nan=False)
    assert all(word not in serialized for word in ("endpoint", "token", "prompt", "http:", "exception"))
    assert decision.scores[0].freshness is Freshness.FRESH


@pytest.mark.parametrize("changes", [
    {"freshness": "fresh"}, {"pressure_ppm": 0}, {"requests_state": "missing"},
    {"freshness": Freshness.FRESH},
    {"freshness": Freshness.FRESH, "pressure_ppm": True, "kv_state": Signal.VALID},
    {"freshness": Freshness.FRESH, "pressure_ppm": MAX_PRESSURE_PPM + 1, "kv_state": Signal.VALID},
])
def test_pressure_value_contract_rejects_inconsistent_or_unbounded_values(changes):
    with pytest.raises(ValueError, match="^invalid replica scheduler input$"):
        replace(ReplicaPressure(), **changes)


class _Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


def test_pressure_cache_is_nonblocking_single_flight_and_keys_members_by_tier():
    original = _replica_config().tiers[0]
    tier = replace(
        original,
        replica_strategy="capacity",
        replicas=tuple(replace(member, max_concurrency=2) for member in original.replicas),
    )
    started = threading.Event()
    release = threading.Event()
    calls = []
    calls_condition = threading.Condition()
    clock = _Clock()

    def provider(view):
        with calls_condition:
            calls.append((view.id, view.base_url, view.replicas))
            if len(calls) == 2:
                started.set()
            calls_condition.notify_all()
        assert release.wait(1)
        return MetricsSnapshot("available", {"requests_running": 1.0})

    cache = ReplicaPressureCache((tier,), metrics_provider=provider, monotonic=clock)
    try:
        first = cache.snapshot(tier.id)
        assert all(value.freshness is Freshness.UNKNOWN for value in first.values())
        assert started.wait(1)
        second = cache.snapshot(tier.id)
        assert all(value.freshness is Freshness.UNKNOWN for value in second.values())
        assert len(calls) == 2
        assert all(call[2] == () for call in calls)
        release.set()
    finally:
        cache.close()


def test_pressure_cache_bounds_registration_and_close_fails_closed():
    original = _replica_config().tiers[0]
    tier = replace(
        original,
        replica_strategy="capacity",
        replicas=tuple(replace(member, max_concurrency=2) for member in original.replicas),
    )
    cache = ReplicaPressureCache((tier,), metrics_provider=lambda _tier: MetricsSnapshot("unavailable", {}, "metrics_missing"))
    cache.close()
    assert all(value.freshness is Freshness.UNKNOWN for value in cache.snapshot(tier.id).values())
    with pytest.raises(ValueError):
        cache.snapshot("missing")
    with pytest.raises(ValueError):
        ReplicaPressureCache((replace(tier, replica_strategy="round_robin"),))


def _capacity_tier(tier_id="primary-local"):
    original = _replica_config().tiers[0]
    return replace(
        original, id=tier_id, replica_strategy="capacity",
        replicas=tuple(replace(member, max_concurrency=4) for member in original.replicas),
    )


def _cache_finished(cache):
    with cache._condition:
        assert cache._condition.wait_for(
            lambda: all(entry.running_at is None and not entry.queued for entry in cache._entries.values()),
            timeout=1,
        )


def test_pressure_cache_refresh_boundary_stale_expiry_and_failure_classification():
    tier = _capacity_tier()
    clock = _Clock()
    calls = []
    refresh_started = threading.Event()
    refresh_release = threading.Event()

    def provider(_view):
        calls.append(1)
        if len(calls) > 4:
            refresh_started.set()
            assert refresh_release.wait(1)
        return MetricsSnapshot("available", {"requests_running": 1.0, "requests_waiting": 2.0})

    cache = ReplicaPressureCache((tier,), metrics_provider=provider, monotonic=clock)
    try:
        cache.snapshot(tier.id)
        _cache_finished(cache)
        assert len(calls) == 2
        assert all(value.freshness is Freshness.FRESH for value in cache.snapshot(tier.id).values())
        clock.value = 1
        cache.snapshot(tier.id)
        _cache_finished(cache)
        assert len(calls) == 4
        clock.value = 6
        assert all(value.freshness is Freshness.FRESH for value in cache.snapshot(tier.id).values())
        assert refresh_started.wait(1)
        clock.value = 6.000001
        assert all(value.freshness is Freshness.STALE for value in cache.snapshot(tier.id).values())
    finally:
        refresh_release.set()
        cache.close()

    failed = ReplicaPressureCache(
        (tier,), metrics_provider=lambda _view: MetricsSnapshot("unavailable", {}, "metrics_http"), monotonic=_Clock(),
    )
    try:
        failed.snapshot(tier.id)
        _cache_finished(failed)
        assert all(value.freshness is Freshness.FAILED for value in failed.snapshot(tier.id).values())
    finally:
        failed.close()


def test_pressure_cache_deadline_cross_tier_keys_queue_bound_and_close():
    first, second = _capacity_tier("tier-a"), _capacity_tier("tier-b")
    clock = _Clock()
    started = threading.Event()
    release = threading.Event()
    calls = []
    lock = threading.Lock()

    def provider(view):
        with lock:
            calls.append((view.id, view.base_url))
            if len(calls) == 2:
                started.set()
        assert release.wait(1)
        return MetricsSnapshot("available", {"requests_running": 1.0})

    cache = ReplicaPressureCache((first, second), metrics_provider=provider, monotonic=clock)
    try:
        cache.snapshot(first.id)
        cache.snapshot(second.id)
        assert started.wait(1)
        with cache._condition:
            assert len(cache._workers) == 2
            assert len(cache._queue) == 2
        clock.value = 1.000001
        assert all(value.freshness is Freshness.FAILED for value in cache.snapshot(first.id).values())
        cache.close()
        assert all(value.freshness is Freshness.UNKNOWN for value in cache.snapshot(first.id).values())
        assert all(value.freshness is Freshness.UNKNOWN for value in cache.snapshot(second.id).values())
        release.set()
    finally:
        release.set()
        cache.close()
