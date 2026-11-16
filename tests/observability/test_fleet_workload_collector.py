"""Hermetic tests for bounded persistent fleet workload collection."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from anvil_serving.observability.fleet_workload_collector import FleetWorkloadCollector
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
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


def _stop(collector: FleetWorkloadCollector, releases=()):
    collector.close()
    for release in releases:
        release.set()
    for worker in collector._workers.values():
        worker.join(1)
        assert not worker.is_alive()


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
