from dataclasses import replace
import threading


from anvil_serving.router.replica_scheduler import (
    PressureFreshness as Freshness,
    ReplicaCandidate,
    ReplicaDecisionReason,
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


def test_no_eligible_member_is_exhaustion_not_invalid_input():
    decision = rank_replica_candidates((_candidate("a", active=2), _candidate("b", eligible=False)), cursor=1)
    assert decision.to_dict() == {
        "selected_member_id": None, "eligible_member_ids": [], "scores": [], "reason": "no-eligible-member",
    }
    assert decision.reason is ReplicaDecisionReason.NO_ELIGIBLE_MEMBER


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


def _capacity_tier(tier_id="primary-local"):
    original = _replica_config().tiers[0]
    return replace(
        original, id=tier_id, replica_strategy="capacity",
        replicas=tuple(replace(member, max_concurrency=4) for member in original.replicas),
    )


def _cache_finished(cache):
    with cache._condition:
        assert cache._condition.wait_for(
            lambda: all(
                entry.running_at is None and not entry.queued and not entry.running
                for entry in cache._entries.values()
            ),
            timeout=1,
        )


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
