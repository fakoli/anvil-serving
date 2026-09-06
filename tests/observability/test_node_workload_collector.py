import datetime as dt
import threading
import time

import pytest

from anvil_serving.observability.node_workload_collector import NodeWorkloadCollector
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadOwner,
    WorkloadQuery,
)


NOW = dt.datetime(2026, 9, 5, tzinfo=dt.timezone.utc)


def _source(owner, now=NOW):
    return SourceResult(owner, ResultStatus.COMPLETE, now, (), Truncation(0, 0))


def _by_owner(result):
    return {source.owner: source for source in result.sources}


def _stop(collector, releases=()):
    collector.close()
    for release in releases:
        release.set()
    for worker in collector._workers.values():
        worker.join(1)
        assert not worker.is_alive()


def test_healthy_owner_survives_blocked_owner_deadline(monkeypatch):
    from anvil_serving.observability import node_workload_collector as module

    monkeypatch.setattr(module, "_COLLECTION_SECONDS", 0.05)
    entered, release = threading.Event(), threading.Event()

    def blocked(*args):
        entered.set()
        assert release.wait(1)
        return _source(WorkloadOwner.ROUTER)

    collector = NodeWorkloadCollector(
        "node-a",
        {WorkloadOwner.ROUTER: blocked, WorkloadOwner.MEDIA: lambda *args: _source(WorkloadOwner.MEDIA)},
    )
    try:
        result = collector.collect(WorkloadQuery(), NOW)
        assert entered.is_set()
        sources = _by_owner(result)
        assert sources[WorkloadOwner.MEDIA].status is ResultStatus.COMPLETE
        assert sources[WorkloadOwner.ROUTER].error is WorkloadErrorCode.UNAVAILABLE
        assert result.status is ResultStatus.PARTIAL
    finally:
        _stop(collector, (release,))


def test_concurrent_collect_returns_fallback_without_duplicate_callbacks(monkeypatch):
    from anvil_serving.observability import node_workload_collector as module

    monkeypatch.setattr(module, "_COLLECTION_SECONDS", 0.2)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(*args):
        calls.append(args[2])
        entered.set()
        assert release.wait(1)
        return _source(WorkloadOwner.ROUTER, args[2])

    collector = NodeWorkloadCollector("node-a", {WorkloadOwner.ROUTER: blocked})
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(collector.collect(WorkloadQuery(), NOW)))
    thread.start()
    try:
        assert entered.wait(1)
        fallback = collector.collect(WorkloadQuery(), NOW)
        assert _by_owner(fallback)[WorkloadOwner.ROUTER].status is ResultStatus.UNAVAILABLE
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(1)
        _stop(collector)
    assert not thread.is_alive() and len(outcome) == 1


def test_busy_owner_does_not_block_idle_owner_or_contaminate_later_query(monkeypatch):
    from anvil_serving.observability import node_workload_collector as module

    monkeypatch.setattr(module, "_COLLECTION_SECONDS", 0.05)
    entered, release = threading.Event(), threading.Event()
    media_calls = []

    def blocked(*args):
        entered.set()
        assert release.wait(1)
        return _source(WorkloadOwner.ROUTER, args[2])

    def media(*args):
        media_calls.append(args[2])
        return _source(WorkloadOwner.MEDIA, args[2])

    collector = NodeWorkloadCollector(
        "node-a", {WorkloadOwner.ROUTER: blocked, WorkloadOwner.MEDIA: media}
    )
    try:
        first = collector.collect(WorkloadQuery(), NOW)
        assert entered.is_set()
        second_now = NOW + dt.timedelta(seconds=1)
        second = collector.collect(WorkloadQuery(), second_now)
        sources = _by_owner(second)
        assert sources[WorkloadOwner.ROUTER].status is ResultStatus.UNAVAILABLE
        assert sources[WorkloadOwner.MEDIA].collection_timestamp == second_now
        assert media_calls == [NOW, second_now]
        assert _by_owner(first)[WorkloadOwner.MEDIA].status is ResultStatus.COMPLETE
    finally:
        _stop(collector, (release,))


def test_repeated_blocked_queries_have_at_most_six_workers_and_one_job_per_owner(monkeypatch):
    from anvil_serving.observability import node_workload_collector as module

    monkeypatch.setattr(module, "_COLLECTION_SECONDS", 0.03)
    entered = threading.Condition()
    releases = [threading.Event() for _ in WorkloadOwner]
    started = []

    def blocked(owner, release):
        def reader(*args):
            with entered:
                started.append(owner)
                entered.notify_all()
            assert release.wait(1)
            return _source(owner, args[2])
        return reader

    readers = {owner: blocked(owner, release) for owner, release in zip(WorkloadOwner, releases)}
    collector = NodeWorkloadCollector("node-a", readers)
    try:
        first = collector.collect(WorkloadQuery(), NOW)
        assert first.status is ResultStatus.UNAVAILABLE
        with entered:
            assert entered.wait_for(lambda: len(started) == len(readers), timeout=1)
        for index in range(3):
            result = collector.collect(WorkloadQuery(), NOW + dt.timedelta(seconds=index + 1))
            assert result.status is ResultStatus.UNAVAILABLE
        assert len(collector._workers) == len(readers) == 6
        assert all(job is not None and job.claimed for job in collector._jobs.values())
    finally:
        _stop(collector, releases)


def test_invalid_arguments_and_clock_fail_before_reader_execution():
    calls = []
    collector = NodeWorkloadCollector(
        "node-a", {WorkloadOwner.ROUTER: lambda *args: calls.append(args)}
    )
    try:
        with pytest.raises(WorkloadError):
            collector.collect(object(), NOW)
        with pytest.raises(WorkloadError):
            collector.collect(WorkloadQuery(), dt.datetime(2026, 9, 5))
        invalid_clock = NodeWorkloadCollector(
            "node-a", {WorkloadOwner.ROUTER: lambda *args: calls.append(args)}, monotonic=lambda: True
        )
        try:
            assert invalid_clock.collect(WorkloadQuery(), NOW).status is ResultStatus.UNAVAILABLE
        finally:
            _stop(invalid_clock)
        assert calls == []
    finally:
        _stop(collector)


def test_backwards_clock_abandons_collection_without_scheduling_readers():
    samples = iter((3.0, 2.0))

    def backwards_clock():
        return next(samples)

    collector = NodeWorkloadCollector("node-a", {}, monotonic=backwards_clock)
    try:
        result = collector.collect(WorkloadQuery(), NOW)
        assert result.status is ResultStatus.UNAVAILABLE
        assert collector._workers == {}
    finally:
        _stop(collector)


def test_callbacks_and_clock_reads_happen_outside_the_condition():
    collector = None
    checked = []

    def clock():
        assert collector is not None
        checked.append("clock")
        assert not collector._condition._is_owned()
        return time.monotonic()

    def reader(*args):
        checked.append("reader")
        assert not collector._condition._is_owned()
        return _source(WorkloadOwner.ROUTER, args[2])

    collector = NodeWorkloadCollector("node-a", {WorkloadOwner.ROUTER: reader}, monotonic=clock)
    try:
        assert _by_owner(collector.collect(WorkloadQuery(), NOW))[WorkloadOwner.ROUTER].status is ResultStatus.COMPLETE
    finally:
        _stop(collector)
    assert "clock" in checked and "reader" in checked


def test_callback_failure_and_malformed_value_are_isolated_without_private_text():
    marker = "private-token-and-url"
    collector = NodeWorkloadCollector(
        "node-a",
        {
            WorkloadOwner.ROUTER: lambda *args: (_ for _ in ()).throw(RuntimeError(marker)),
            WorkloadOwner.MEDIA: lambda *args: marker,
        },
    )
    try:
        result = collector.collect(WorkloadQuery(), NOW)
        sources = _by_owner(result)
        assert sources[WorkloadOwner.ROUTER].error is WorkloadErrorCode.UNAVAILABLE
        assert sources[WorkloadOwner.MEDIA].error is WorkloadErrorCode.INVALID
        assert marker not in str(result)
    finally:
        _stop(collector)


def test_close_abandons_late_work_and_future_collection_without_scheduling(monkeypatch):
    from anvil_serving.observability import node_workload_collector as module

    monkeypatch.setattr(module, "_COLLECTION_SECONDS", 0.2)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked(*args):
        calls.append(args)
        entered.set()
        assert release.wait(1)
        return _source(WorkloadOwner.ROUTER, args[2])

    collector = NodeWorkloadCollector("node-a", {WorkloadOwner.ROUTER: blocked})
    result = []
    thread = threading.Thread(target=lambda: result.append(collector.collect(WorkloadQuery(), NOW)))
    thread.start()
    try:
        assert entered.wait(1)
        collector.close()
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.UNAVAILABLE
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(1)
        _stop(collector)
    assert not thread.is_alive()


def test_timeout_and_close_discard_queued_unclaimed_callbacks(monkeypatch):
    from anvil_serving.observability import node_workload_collector as module

    monkeypatch.setattr(module, "_COLLECTION_SECONDS", 0.03)
    calls = []
    collector = NodeWorkloadCollector(
        "node-a", {WorkloadOwner.ROUTER: lambda *args: calls.append(args)}
    )
    scheduled = threading.Event()

    def do_not_start(owner):
        scheduled.set()
        return True

    monkeypatch.setattr(collector, "_start_worker_locked", do_not_start)
    try:
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.UNAVAILABLE
        assert scheduled.is_set() and calls == []
        assert collector._jobs[WorkloadOwner.ROUTER] is None

        scheduled.clear()
        outcome = []
        thread = threading.Thread(target=lambda: outcome.append(collector.collect(WorkloadQuery(), NOW)))
        thread.start()
        assert scheduled.wait(1)
        collector.close()
        thread.join(1)
        assert not thread.is_alive() and len(outcome) == 1
        assert calls == [] and collector._jobs[WorkloadOwner.ROUTER] is None
    finally:
        _stop(collector)


def test_failed_worker_start_abandons_jobs_and_later_healthy_collection_succeeds(monkeypatch):
    calls = []
    collector = NodeWorkloadCollector(
        "node-a", {WorkloadOwner.ROUTER: lambda *args: calls.append(args) or _source(WorkloadOwner.ROUTER)}
    )
    original = collector._start_worker_locked

    def failed_start(owner):
        raise RuntimeError("private-start-failure")

    monkeypatch.setattr(collector, "_start_worker_locked", failed_start)
    try:
        assert collector.collect(WorkloadQuery(), NOW).status is ResultStatus.UNAVAILABLE
        assert collector._active is None
        assert collector._jobs[WorkloadOwner.ROUTER] is None
        assert calls == []
        monkeypatch.setattr(collector, "_start_worker_locked", original)
        result = collector.collect(WorkloadQuery(), NOW)
        assert _by_owner(result)[WorkloadOwner.ROUTER].status is ResultStatus.COMPLETE
        assert len(calls) == 1
    finally:
        _stop(collector)


def test_late_invalid_clock_is_not_retained_and_next_healthy_query_is_isolated(monkeypatch):
    from anvil_serving.observability import node_workload_collector as module

    monkeypatch.setattr(module, "_COLLECTION_SECONDS", 0.03)
    entered, release, invalid_mode, invalid_seen = (
        threading.Event(),
        threading.Event(),
        threading.Event(),
        threading.Event(),
    )

    def clock():
        if invalid_mode.is_set():
            invalid_seen.set()
            return -1
        return time.monotonic()

    def router(*args):
        entered.set()
        assert release.wait(1)
        return _source(WorkloadOwner.ROUTER, args[2])

    collector = NodeWorkloadCollector(
        "node-a", {WorkloadOwner.ROUTER: router, WorkloadOwner.MEDIA: lambda *args: _source(WorkloadOwner.MEDIA, args[2])},
        monotonic=clock,
    )
    try:
        first = collector.collect(WorkloadQuery(), NOW)
        assert entered.is_set() and _by_owner(first)[WorkloadOwner.MEDIA].status is ResultStatus.COMPLETE
        invalid_mode.set()
        release.set()
        assert invalid_seen.wait(1)
        invalid_mode.clear()
        result = collector.collect(WorkloadQuery(), NOW + dt.timedelta(seconds=1))
        assert _by_owner(result)[WorkloadOwner.ROUTER].status is ResultStatus.COMPLETE
        assert collector._invalid_collections == set()
    finally:
        _stop(collector, (release,))


def test_constructor_rejects_malformed_registration_without_workers_or_io():
    with pytest.raises(ValueError):
        NodeWorkloadCollector("", {})
    with pytest.raises(ValueError):
        NodeWorkloadCollector("node-a", {"router": None})
    with pytest.raises(ValueError):
        NodeWorkloadCollector("node-a", {WorkloadOwner.ROUTER: object()})
    assert build_node_workloads("node-a", WorkloadQuery(), NOW, {}).status is ResultStatus.UNAVAILABLE
