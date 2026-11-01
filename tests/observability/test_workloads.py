from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone, tzinfo


from anvil_serving.observability.workloads import (
    FleetResult,
    NodeResult,
    ObservationQuality,
    Progress,
    ResultStatus,
    SourceAuthority,
    SourceResult,
    Truncation,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadState,
    select_records,
    workload_id,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _record(
    native_id: str = "job-1",
    *,
    owner: WorkloadOwner = WorkloadOwner.ROUTER,
    host: str = "node-a",
    state: WorkloadState | None = None,
    phase: WorkloadPhase | None = None,
    outcome: WorkloadOutcome | None = None,
    updated_at: datetime = NOW,
    source_timestamp: datetime = NOW,
    quality: ObservationQuality | None = None,
) -> WorkloadRecord:
    kind = {
        WorkloadOwner.ROUTER: WorkloadKind.ROUTER_REQUEST,
        WorkloadOwner.CONTROLLER: WorkloadKind.CONTROLLER_OPERATION,
        WorkloadOwner.BENCHMARK: WorkloadKind.BENCHMARK_JOB,
        WorkloadOwner.MEDIA: WorkloadKind.MEDIA_JOB,
        WorkloadOwner.RECIPE: WorkloadKind.RECIPE_SERVE,
        WorkloadOwner.MANIFEST: WorkloadKind.RECIPE_SERVE,
    }[owner]
    authority = {
        WorkloadOwner.ROUTER: SourceAuthority.ROUTER_MEMORY,
        WorkloadOwner.CONTROLLER: SourceAuthority.CONTROLLER_STORE,
        WorkloadOwner.BENCHMARK: SourceAuthority.BENCHMARK_STORE,
        WorkloadOwner.MEDIA: SourceAuthority.MEDIA_STORE,
        WorkloadOwner.RECIPE: SourceAuthority.MANAGED_STATUS,
        WorkloadOwner.MANIFEST: SourceAuthority.MANAGED_STATUS,
    }[owner]
    if quality is None:
        quality = (
            ObservationQuality.OBSERVED_RUNNING
            if owner in {WorkloadOwner.RECIPE, WorkloadOwner.MANIFEST}
            else ObservationQuality.RECORDED
        )
    if state is None:
        state = (
            WorkloadState.STREAMING
            if owner is WorkloadOwner.ROUTER
            else WorkloadState.RUNNING
        )
    if phase is None:
        phase = (
            WorkloadPhase.STREAMING
            if owner is WorkloadOwner.ROUTER
            else WorkloadPhase.RUNNING
        )
    return WorkloadRecord(
        id=workload_id(host, kind, owner, native_id),
        kind=kind,
        owner=owner,
        host=host,
        state=state,
        phase=phase,
        outcome=outcome,
        created_at=updated_at - timedelta(seconds=1),
        updated_at=updated_at,
        source_timestamp=source_timestamp,
        source_authority=authority,
        observation_quality=quality,
        progress=Progress(completed=1, total=2),
    )


def _source(
    owner: WorkloadOwner = WorkloadOwner.ROUTER,
    records: tuple[WorkloadRecord, ...] | None = None,
    *,
    collected: datetime = NOW,
    status: ResultStatus = ResultStatus.COMPLETE,
    error: WorkloadErrorCode | None = None,
    omitted: int | None = 0,
) -> SourceResult:
    records = (_record(owner=owner),) if records is None else records
    return SourceResult(
        owner, status, collected, records, Truncation(len(records), omitted), error
    )


def _node(
    host: str = "node-a", sources: tuple[SourceResult, ...] | None = None
) -> NodeResult:
    sources = (_source(),) if sources is None else sources
    statuses = [source.status for source in sources]
    status = (
        ResultStatus.UNAVAILABLE
        if all(value is ResultStatus.UNAVAILABLE for value in statuses)
        else ResultStatus.PARTIAL
        if any(value is not ResultStatus.COMPLETE for value in statuses)
        else ResultStatus.COMPLETE
    )
    return NodeResult(host, status, NOW, sources)


def _fleet(
    nodes: tuple[NodeResult, ...] | None = None, omitted: int | None = 0
) -> FleetResult:
    nodes = (_node(),) if nodes is None else nodes
    statuses = [node.status for node in nodes]
    status = (
        ResultStatus.COMPLETE
        if not statuses or all(value is ResultStatus.COMPLETE for value in statuses)
        else ResultStatus.UNAVAILABLE
        if all(value is ResultStatus.UNAVAILABLE for value in statuses)
        else ResultStatus.PARTIAL
    )
    if omitted != 0 and status is ResultStatus.COMPLETE:
        status = ResultStatus.PARTIAL
    returned = sum(len(source.records) for node in nodes for source in node.sources)
    return FleetResult(status, NOW, nodes, Truncation(returned, omitted))


def test_query_limit_is_honored_at_source_and_aggregate_scope() -> None:
    records = tuple(
        _record(str(index), updated_at=NOW - timedelta(seconds=index))
        for index in range(10)
    )
    for aggregate in (False, True):
        returned, truncation = select_records(
            records, WorkloadQuery(limit=3), now=NOW, aggregate=aggregate
        )
        assert len(returned) == 3
        assert truncation == Truncation(3, 7)


class _BrokenTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise RuntimeError("secret timezone detail")


class _OneShotIterator:
    consumed = False

    def __iter__(self) -> Iterator[tuple[str, object]]:
        self.consumed = True
        yield ("limit", 1)


class _BombSequence(Sequence[Sequence[object]]):
    def __len__(self) -> int:
        return 8

    def __getitem__(self, index: int) -> Sequence[object]:
        raise AssertionError("must bound before reading entries")


class _HostileRecords(Sequence[WorkloadRecord]):
    def __init__(self, fail_len: bool) -> None:
        self.fail_len = fail_len

    def __len__(self) -> int:
        if self.fail_len:
            raise RuntimeError("credential-shaped-secret")
        return 1

    def __getitem__(self, index: int) -> WorkloadRecord:
        raise RuntimeError("credential-shaped-secret")
