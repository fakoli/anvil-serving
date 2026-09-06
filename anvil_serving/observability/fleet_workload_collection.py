"""Pure bounded composition of canonical node workload observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from heapq import heappush, heapreplace

from .workload_collection import build_node_workloads
from .workloads import (
    AGGREGATE_LIMIT,
    MAX_COUNT,
    MAX_FUTURE_SECONDS,
    MAX_NODES,
    NodeResult,
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadOwner,
    WorkloadQuery,
    WorkloadRecord,
    FleetResult,
    normalize_workload_timestamp,
)


_OWNERS = tuple(sorted(WorkloadOwner, key=lambda owner: owner.value))


@dataclass(frozen=True)
class _Entry:
    host: str
    owner: WorkloadOwner
    record_id: str
    updated_at: datetime
    record_index: int

    def __lt__(self, other: _Entry) -> bool:
        # The root is the worst retained record. Exact datetime comparisons
        # avoid floating-point loss and platform timestamp range differences.
        if self.updated_at != other.updated_at:
            return self.updated_at < other.updated_at
        return (self.record_id, self.host, self.owner.value, self.record_index) > (
            other.record_id, other.host, other.owner.value, other.record_index
        )


@dataclass
class _SourceBuffer:
    owner: WorkloadOwner
    status: ResultStatus
    collected: datetime
    omitted: int | None
    error: WorkloadErrorCode | None
    records: dict[int, WorkloadRecord] = field(default_factory=dict)

    def omit(self) -> None:
        self.omitted = _increment(self.omitted, 1)
        if self.status is ResultStatus.COMPLETE:
            self.status = ResultStatus.PARTIAL

    def finish(self) -> SourceResult:
        records = tuple(self.records[index] for index in sorted(self.records))
        return SourceResult(
            self.owner, self.status, self.collected, records,
            Truncation(len(records), self.omitted), self.error,
        )


def _query_copy(query: WorkloadQuery) -> WorkloadQuery:
    return WorkloadQuery(
        query.owner,
        query.kind,
        query.state,
        query.host,
        query.active_only,
        query.recent_seconds,
        query.limit,
    )


def _status(values: tuple[ResultStatus, ...]) -> ResultStatus:
    if values and all(value is ResultStatus.UNAVAILABLE for value in values):
        return ResultStatus.UNAVAILABLE
    if any(value is not ResultStatus.COMPLETE for value in values):
        return ResultStatus.PARTIAL
    return ResultStatus.COMPLETE


def _fixed_sources(
    host: str, query: WorkloadQuery, collected: datetime, code: WorkloadErrorCode | None
) -> NodeResult:
    if code is None:
        sources = {
            owner: SourceResult(owner, ResultStatus.COMPLETE, collected, (), Truncation(0, 0))
            for owner in _OWNERS
        }
    else:
        sources = {
            owner: SourceResult(
                owner,
                ResultStatus.UNAVAILABLE,
                collected,
                (),
                Truncation(0, None),
                code,
            )
            for owner in _OWNERS
        }
    return build_node_workloads(host, query, collected, sources)


def _header(node: object, host: str, now: datetime) -> tuple[datetime, dict[WorkloadOwner, SourceResult]]:
    if type(node) is not NodeResult:
        raise ValueError
    if (
        type(node.host) is not str
        or node.host != host
        or type(node.status) is not ResultStatus
        or type(node.collection_timestamp) is not datetime
        or type(node.sources) is not tuple
        or not 1 <= len(node.sources) <= len(_OWNERS)
    ):
        raise ValueError
    collected = normalize_workload_timestamp(node.collection_timestamp)
    if collected - now > timedelta(seconds=MAX_FUTURE_SECONDS):
        raise WorkloadError(WorkloadErrorCode.FUTURE, "future node workload timestamp")
    sources: dict[WorkloadOwner, SourceResult] = {}
    for source in node.sources:
        if type(source) is not SourceResult or type(source.owner) is not WorkloadOwner:
            raise ValueError
        if source.owner in sources or type(source.status) is not ResultStatus:
            raise ValueError
        sources[source.owner] = source
    if node.status is not _status(tuple(source.status for source in node.sources)):
        raise ValueError
    return collected, sources


def _unchanged_failure(source: SourceResult, original: SourceResult | None) -> bool:
    """Recognize an already-canonical failure without touching unchecked records."""
    if original is None or original.status is not ResultStatus.UNAVAILABLE:
        return False
    try:
        truncation = original.truncation
        return (
            type(original.records) is tuple
            and not original.records
            and type(truncation) is Truncation
            and type(truncation.returned) is int
            and truncation.returned == source.truncation.returned
            and (truncation.omitted is None or type(truncation.omitted) is int)
            and truncation.omitted == source.truncation.omitted
            and original.error is source.error
            and type(original.collection_timestamp) is datetime
            and original.collection_timestamp == source.collection_timestamp
        )
    except Exception:
        return False


def normalize_node_workloads(
    host: str, query: WorkloadQuery, now: datetime, node: NodeResult | None
) -> NodeResult:
    """Detach one supplied node or reduce it to fixed canonical fallback data."""
    baseline = build_node_workloads(host, query, now, {})
    checked_query = _query_copy(query)
    collected = baseline.collection_timestamp
    if checked_query.host is not None and checked_query.host != host:
        return _fixed_sources(host, checked_query, collected, None)
    if node is None:
        return _fixed_sources(host, checked_query, collected, WorkloadErrorCode.UNAVAILABLE)
    try:
        node_time, sources = _header(node, host, collected)
    except WorkloadError as exc:
        code = WorkloadErrorCode.FUTURE if exc.code is WorkloadErrorCode.FUTURE else WorkloadErrorCode.INVALID
        return _fixed_sources(host, checked_query, collected, code)
    except Exception:
        return _fixed_sources(host, checked_query, collected, WorkloadErrorCode.INVALID)

    # The remote node clock is provenance, never authority for receipt skew or
    # query recency. Validate once at receipt time before restoring provenance.
    checked = build_node_workloads(host, checked_query, collected, sources)
    restored: list[SourceResult] = []
    for source in checked.sources:
        original = sources.get(source.owner)
        rejected = (
            source.status is ResultStatus.UNAVAILABLE
            and not _unchanged_failure(source, original)
        )
        ahead_of_node = source.collection_timestamp - node_time > timedelta(seconds=MAX_FUTURE_SECONDS)
        if rejected or ahead_of_node:
            source = SourceResult(
                source.owner, ResultStatus.UNAVAILABLE, node_time, (), Truncation(0, None),
                source.error if rejected else WorkloadErrorCode.FUTURE,
            )
        restored.append(source)
    entries = tuple(restored)
    return NodeResult(host, _status(tuple(source.status for source in entries)), node_time, entries)


def _increment(omitted: int | None, count: int) -> int | None:
    if omitted is None:
        return None
    value = omitted + count
    return value if value <= MAX_COUNT else None


def _fleet_omitted(nodes: tuple[NodeResult, ...]) -> int | None:
    total = 0
    for node in nodes:
        for source in node.sources:
            if source.truncation.omitted is None:
                return None
            total += source.truncation.omitted
            if total > MAX_COUNT:
                return None
    return total


def build_fleet_workloads(
    hosts: tuple[str, ...], query: WorkloadQuery, now: datetime,
    nodes: dict[str, NodeResult | None],
) -> FleetResult:
    """Compose declared nodes using bounded newest-first global selection."""
    validation = build_node_workloads("validation", query, now, {})
    checked_query = _query_copy(query)
    checked_now = validation.collection_timestamp
    if type(hosts) is not tuple or len(hosts) > MAX_NODES:
        raise WorkloadError(WorkloadErrorCode.INVALID, "invalid fleet workload hosts")
    checked_hosts: set[str] = set()
    for host in hosts:
        build_node_workloads(host, checked_query, checked_now, {})
        if host in checked_hosts:
            raise WorkloadError(WorkloadErrorCode.INVALID, "invalid fleet workload hosts")
        checked_hosts.add(host)
    if type(nodes) is not dict or len(nodes) > len(checked_hosts):
        raise WorkloadError(WorkloadErrorCode.INVALID, "invalid fleet workload nodes")
    if any(type(host) is not str or host not in checked_hosts for host in nodes):
        raise WorkloadError(WorkloadErrorCode.INVALID, "invalid fleet workload nodes")

    node_metadata: list[tuple[str, datetime, tuple[WorkloadOwner, ...]]] = []
    buffers: dict[tuple[str, WorkloadOwner], _SourceBuffer] = {}
    selected: list[_Entry] = []
    cap = min(checked_query.limit, AGGREGATE_LIMIT)
    for host in sorted(checked_hosts):
        current = normalize_node_workloads(host, checked_query, checked_now, nodes.get(host))
        node_metadata.append((host, current.collection_timestamp, tuple(source.owner for source in current.sources)))
        for source in current.sources:
            buffer = _SourceBuffer(
                source.owner, source.status, source.collection_timestamp,
                source.truncation.omitted, source.error,
            )
            buffers[host, source.owner] = buffer
            for index, record in enumerate(source.records):
                entry = _Entry(host, source.owner, record.id, record.updated_at, index)
                if len(selected) < cap:
                    heappush(selected, entry)
                elif selected[0] < entry:
                    removed = heapreplace(selected, entry)
                    loser = buffers[removed.host, removed.owner]
                    del loser.records[removed.record_index]
                    loser.omit()
                else:
                    buffer.omit()
                    continue
                buffer.records[index] = record

    # No buffer retains a supplied node/source or its discarded records. Build
    # canonical summaries once, after the bounded global selection is final.
    final_nodes: list[NodeResult] = []
    for host, collected, owners in node_metadata:
        sources = tuple(buffers[host, owner].finish() for owner in owners)
        final_nodes.append(NodeResult(host, _status(tuple(source.status for source in sources)), collected, sources))
    summaries = tuple(final_nodes)

    returned = sum(len(source.records) for node in summaries for source in node.sources)
    omitted = _fleet_omitted(summaries)
    status = _status(tuple(node.status for node in summaries)) if summaries else ResultStatus.COMPLETE
    if status is ResultStatus.COMPLETE and omitted != 0:
        status = ResultStatus.PARTIAL
    return FleetResult(status, checked_now, summaries, Truncation(returned, omitted))


__all__ = ["build_fleet_workloads", "normalize_node_workloads"]
