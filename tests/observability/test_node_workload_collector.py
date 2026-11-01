import datetime as dt
import threading


from anvil_serving.observability.node_workload_collector import NodeWorkloadCollector
from anvil_serving.observability.workloads import (
    ResultStatus,
    SourceResult,
    Truncation,
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
