"""Hermetic tests for bounded persistent fleet workload collection."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from anvil_serving.observability.fleet_workload_collection import build_fleet_workloads
from anvil_serving.observability.fleet_workload_collector import FleetWorkloadCollector
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    MAX_NODES,
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadOwner,
    WorkloadQuery,
)


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _node(host: str, now: datetime = NOW):
    sources = {
        owner: SourceResult(owner, ResultStatus.COMPLETE, now, (), Truncation(0, 0))
        for owner in WorkloadOwner
    }
    return build_node_workloads(host, WorkloadQuery(), now, sources)


def _by_host(result):
    return {node.host: node for node in result.nodes}


def _stop(collector: FleetWorkloadCollector, releases=()):
    collector.close()
    for release in releases:
        release.set()
    for worker in collector._workers.values():
        worker.join(1)
        assert not worker.is_alive()


def test_constructor_is_inert_and_copies_exact_bounded_registration():
    calls = []
    readers = {"node-a": lambda *args: calls.append(args) or _node("node-a")}
    collector = FleetWorkloadCollector(readers)
    readers.clear()
    try:
        assert calls == []
        assert collector._workers == {}
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.COMPLETE
        assert len(calls) == 1
    finally:
        _stop(collector)

    for invalid in (
        {"bad host": None},
        {"node-a": object()},
        {f"node-{index}": None for index in range(MAX_NODES + 1)},
    ):
        with pytest.raises(ValueError, match="invalid fleet workload collector"):
            FleetWorkloadCollector(invalid)


def test_four_persistent_workers_bound_concurrency_and_drain_hosts(monkeypatch):
    from anvil_serving.observability import fleet_workload_collector as module

    monkeypatch.setattr(module, "_AGGREGATE_SECONDS", 1.0)
    entered = threading.Condition()
    release = threading.Event()
    active = 0
    maximum = 0
    called = []

    def reader(host, query, now):
        nonlocal active, maximum
        with entered:
            active += 1
            maximum = max(maximum, active)
            called.append(host)
            entered.notify_all()
        assert release.wait(2)
        with entered:
            active -= 1
        return _node(host, now)

    collector = FleetWorkloadCollector(
        {f"node-{letter}": reader for letter in "abcdef"}
    )
    outcome = []
    thread = threading.Thread(
        target=lambda: outcome.append(collector.collect(WorkloadQuery(), NOW))
    )
    thread.start()
    try:
        with entered:
            assert entered.wait_for(lambda: len(called) == 4, timeout=1)
            assert len(called) == 4
        release.set()
        thread.join(2)
        assert not thread.is_alive()
        assert sorted(called) == [f"node-{letter}" for letter in "abcdef"]
        assert maximum == 4
        assert len(collector._workers) == 4
    finally:
        _stop(collector, (release,))
    assert outcome[0].status is ResultStatus.COMPLETE


def test_concurrent_collect_returns_immediate_fallback_without_duplicate_calls(monkeypatch):
    from anvil_serving.observability import fleet_workload_collector as module

    monkeypatch.setattr(module, "_AGGREGATE_SECONDS", 0.3)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(host, query, now):
        calls.append(host)
        entered.set()
        assert release.wait(1)
        return _node(host, now)

    collector = FleetWorkloadCollector({"node-a": blocked})
    first = []
    thread = threading.Thread(
        target=lambda: first.append(collector.collect(WorkloadQuery(), NOW))
    )
    thread.start()
    try:
        assert entered.wait(1)
        second = collector.collect(WorkloadQuery(), NOW)
        assert second.status is ResultStatus.UNAVAILABLE
        assert calls == ["node-a"]
    finally:
        release.set()
        thread.join(1)
        _stop(collector)
    assert not thread.is_alive() and len(first) == 1


def test_expired_old_host_is_not_overlapped_and_healthy_peer_progresses(monkeypatch):
    from anvil_serving.observability import fleet_workload_collector as module

    monkeypatch.setattr(module, "_NODE_SECONDS", 0.03)
    monkeypatch.setattr(module, "_AGGREGATE_SECONDS", 0.08)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    host_a_active = 0
    host_a_maximum = 0
    host_a_calls = 0
    host_b_calls = []

    def blocked(host, query, now):
        nonlocal host_a_active, host_a_maximum, host_a_calls
        host_a_calls += 1
        host_a_active += 1
        host_a_maximum = max(host_a_maximum, host_a_active)
        entered.set()
        assert release.wait(1)
        host_a_active -= 1
        exited.set()
        return _node(host, now)

    def healthy(host, query, now):
        host_b_calls.append(now)
        return _node(host, now)

    collector = FleetWorkloadCollector({"node-a": blocked, "node-b": healthy})
    try:
        first = collector.collect(WorkloadQuery(host="node-a"), NOW)
        assert entered.is_set()
        assert _by_host(first)["node-b"].status is ResultStatus.COMPLETE
        later = datetime(2026, 9, 5, 12, 0, 1, tzinfo=timezone.utc)
        second = collector.collect(WorkloadQuery(host="node-b"), later)
        assert _by_host(second)["node-a"].status is ResultStatus.COMPLETE
        assert _by_host(second)["node-b"].status is ResultStatus.COMPLETE
        assert host_b_calls == [later]
        assert host_a_maximum == 1
        assert host_a_calls == 1
        release.set()
        assert exited.wait(1)
        with collector._condition:
            assert collector._condition.wait_for(
                lambda: all(
                    job is None or job.host != "node-a"
                    for job in collector._slots.values()
                ),
                timeout=1,
            )
        latest = datetime(2026, 9, 5, 12, 0, 2, tzinfo=timezone.utc)
        third = collector.collect(WorkloadQuery(), latest)
        assert _by_host(third)["node-a"].status is ResultStatus.COMPLETE
        assert host_a_calls == 2
        assert host_b_calls == [later, latest]
    finally:
        _stop(collector, (release,))


def test_close_discards_queued_callback_and_returns_without_blocked_workers(monkeypatch):
    from anvil_serving.observability import fleet_workload_collector as module

    monkeypatch.setattr(module, "_AGGREGATE_SECONDS", 1.0)
    entered = threading.Condition()
    release = threading.Event()
    calls = []

    def blocked(host, query, now):
        with entered:
            calls.append(host)
            entered.notify_all()
        assert release.wait(2)
        return _node(host, now)

    collector = FleetWorkloadCollector(
        {f"node-{letter}": blocked for letter in "abcde"}
    )
    result = []
    thread = threading.Thread(
        target=lambda: result.append(collector.collect(WorkloadQuery(), NOW))
    )
    thread.start()
    try:
        with entered:
            assert entered.wait_for(lambda: len(calls) == 4, timeout=1)
        started = time.perf_counter()
        collector.close()
        assert time.perf_counter() - started < 0.1
        thread.join(1)
        assert not thread.is_alive()
        assert len(calls) == 4
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.UNAVAILABLE
    finally:
        release.set()
        _stop(collector)


def test_aggregate_deadline_never_starts_queued_fifth_host(monkeypatch):
    from anvil_serving.observability import fleet_workload_collector as module

    monkeypatch.setattr(module, "_AGGREGATE_SECONDS", 0.04)
    monkeypatch.setattr(module, "_NODE_SECONDS", 1.0)
    entered = threading.Condition()
    release = threading.Event()
    calls = []

    def blocked(host, query, now):
        with entered:
            calls.append(host)
            entered.notify_all()
        assert release.wait(1)
        return _node(host, now)

    collector = FleetWorkloadCollector(
        {f"node-{letter}": blocked for letter in "abcde"}
    )
    try:
        result = collector.collect(WorkloadQuery(), NOW)
        assert result.status is ResultStatus.UNAVAILABLE
        with entered:
            assert len(calls) == 4
        assert "node-e" not in calls
    finally:
        _stop(collector, (release,))


def test_throwing_reader_isolated_and_host_filter_skips_other_callbacks():
    private = "private-reader-failure"
    calls = []

    def failed(*args):
        raise RuntimeError(private)

    collector = FleetWorkloadCollector(
        {
            "node-a": failed,
            "node-b": lambda host, query, now: calls.append(host) or _node(host, now),
        }
    )
    try:
        result = collector.collect(WorkloadQuery(host="node-b"), NOW)
    finally:
        _stop(collector)
    assert calls == ["node-b"]
    assert _by_host(result)["node-a"].status is ResultStatus.COMPLETE
    assert _by_host(result)["node-b"].status is ResultStatus.COMPLETE
    assert private not in str(result)


def test_invalid_arguments_and_invalid_clock_run_no_callbacks():
    calls = []
    collector = FleetWorkloadCollector(
        {"node-a": lambda *args: calls.append(args)}, monotonic=lambda: True
    )
    try:
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.UNAVAILABLE
        with pytest.raises(WorkloadError):
            collector.collect(object(), NOW)
        with pytest.raises(WorkloadError):
            collector.collect(WorkloadQuery(), datetime(2026, 9, 5))
    finally:
        _stop(collector)
    assert calls == []


def test_failed_worker_start_abandons_generation_and_later_recovers(monkeypatch):
    calls = []
    collector = FleetWorkloadCollector(
        {"node-a": lambda host, query, now: calls.append(host) or _node(host, now)}
    )
    original = collector._start_worker_locked
    monkeypatch.setattr(collector, "_start_worker_locked", lambda worker_id: False)
    try:
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.UNAVAILABLE
        assert collector._active is None and collector._slots == {}
        monkeypatch.setattr(collector, "_start_worker_locked", original)
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.COMPLETE
        assert calls == ["node-a"]
    finally:
        _stop(collector)


def test_clocks_callbacks_and_canonical_merge_are_outside_coordination_lock(monkeypatch):
    from anvil_serving.observability import fleet_workload_collector as module

    collector = None
    observations = []
    original = module.build_fleet_workloads

    def clock():
        assert collector is not None
        assert not collector._condition._is_owned()
        observations.append("clock")
        return time.monotonic()

    def reader(host, query, now):
        assert collector is not None and not collector._condition._is_owned()
        observations.append("reader")
        return _node(host, now)

    def builder(*args, **kwargs):
        assert collector is None or not collector._condition._is_owned()
        observations.append("builder")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "build_fleet_workloads", builder)
    collector = FleetWorkloadCollector({"node-a": reader}, monotonic=clock)
    try:
        result = collector.collect(WorkloadQuery(), NOW)
    finally:
        _stop(collector)
    assert result.status is ResultStatus.COMPLETE
    assert {"clock", "reader", "builder"} <= set(observations)


def test_collector_matches_pure_builder_for_completed_results():
    readers = {
        host: (lambda host, query, now: _node(host, now))
        for host in ("node-c", "node-a", "node-b")
    }
    collector = FleetWorkloadCollector(readers)
    try:
        actual = collector.collect(WorkloadQuery(), NOW)
    finally:
        _stop(collector)
    expected = build_fleet_workloads(
        tuple(readers),
        WorkloadQuery(),
        NOW,
        {host: _node(host) for host in readers},
    )
    assert actual == expected
