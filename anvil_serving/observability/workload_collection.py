"""Pure, bounded composition of canonical workload source results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .workloads import (
    AGGREGATE_LIMIT,
    MAX_COUNT,
    MAX_FUTURE_SECONDS,
    MAX_SOURCES_PER_NODE,
    SOURCE_LIMIT,
    NodeResult,
    ObservationQuality,
    Progress,
    ResultStatus,
    SourceAuthority,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadPhase,
    WorkloadState,
    SourceResult,
    normalize_workload_timestamp,
    select_records,
    validate_source_records,
)

_OWNERS = tuple(sorted(WorkloadOwner, key=lambda owner: owner.value))


@dataclass(frozen=True)
class _Entry:
    source_index: int
    record_index: int
    record: WorkloadRecord


def _refuse(message: str) -> WorkloadError:
    return WorkloadError(WorkloadErrorCode.INVALID, message)


def _fixed_error(code: WorkloadErrorCode, message: str) -> WorkloadError:
    return WorkloadError(code, message)


def _validated_host(host: object) -> str:
    if type(host) is not str:
        raise _refuse("invalid node workload host")
    try:
        WorkloadQuery(host=host)
    except WorkloadError:
        raise _refuse("invalid node workload host") from None
    return host


def _validated_query(query: object) -> WorkloadQuery:
    if type(query) is not WorkloadQuery:
        raise _refuse("invalid node workload query")
    if query.owner is not None and type(query.owner) is not WorkloadOwner:
        raise _refuse("invalid node workload query")
    if query.kind is not None and type(query.kind) is not WorkloadKind:
        raise _refuse("invalid node workload query")
    if query.state is not None and type(query.state) is not WorkloadState:
        raise _refuse("invalid node workload query")
    if query.host is not None and type(query.host) is not str:
        raise _refuse("invalid node workload query")
    if type(query.active_only) is not bool:
        raise _refuse("invalid node workload query")
    if type(query.recent_seconds) is not int:
        raise _refuse("invalid node workload query")
    if type(query.limit) is not int:
        raise _refuse("invalid node workload query")
    try:
        return WorkloadQuery(
            owner=query.owner,
            kind=query.kind,
            state=query.state,
            host=query.host,
            active_only=query.active_only,
            recent_seconds=query.recent_seconds,
            limit=query.limit,
        )
    except WorkloadError:
        raise _refuse("invalid node workload query") from None


def _validated_now(now: object) -> datetime:
    if type(now) is not datetime:
        raise _refuse("invalid node workload collection time")
    try:
        return normalize_workload_timestamp(now)
    except WorkloadError:
        raise _refuse("invalid node workload collection time") from None


def _validated_progress(progress: object) -> Progress | None:
    if progress is None:
        return None
    if type(progress) is not Progress:
        raise _refuse("invalid workload source result")
    if type(progress.completed) is not int or (
        progress.total is not None and type(progress.total) is not int
    ):
        raise _refuse("invalid workload source result")
    if type(progress.unit) is not str:
        raise _refuse("invalid workload source result")
    return Progress(progress.completed, progress.total, progress.unit)


def _validated_record(record: object) -> WorkloadRecord:
    if type(record) is not WorkloadRecord:
        raise _refuse("invalid workload source result")
    exact_fields = (
        (record.id, str),
        (record.host, str),
        (record.owner, WorkloadOwner),
        (record.kind, WorkloadKind),
        (record.state, WorkloadState),
        (record.phase, WorkloadPhase),
        (record.source_authority, SourceAuthority),
        (record.observation_quality, ObservationQuality),
        (record.created_at, datetime),
        (record.updated_at, datetime),
        (record.source_timestamp, datetime),
    )
    if any(type(value) is not expected for value, expected in exact_fields):
        raise _refuse("invalid workload source result")
    if record.outcome is not None and type(record.outcome) is not WorkloadOutcome:
        raise _refuse("invalid workload source result")
    try:
        return WorkloadRecord(
            id=record.id,
            kind=record.kind,
            owner=record.owner,
            host=record.host,
            state=record.state,
            phase=record.phase,
            outcome=record.outcome,
            created_at=record.created_at,
            updated_at=record.updated_at,
            source_timestamp=record.source_timestamp,
            source_authority=record.source_authority,
            observation_quality=record.observation_quality,
            progress=_validated_progress(record.progress),
        )
    except WorkloadError:
        raise _refuse("invalid workload source result") from None


def _validated_truncation(truncation: object) -> Truncation:
    if type(truncation) is not Truncation:
        raise _refuse("invalid workload source result")
    if type(truncation.returned) is not int:
        raise _refuse("invalid workload source result")
    if truncation.omitted is not None and type(truncation.omitted) is not int:
        raise _refuse("invalid workload source result")
    try:
        return Truncation(truncation.returned, truncation.omitted)
    except WorkloadError:
        raise _refuse("invalid workload source result") from None


def _unavailable(owner: WorkloadOwner, now: datetime) -> SourceResult:
    return SourceResult(
        owner=owner,
        status=ResultStatus.UNAVAILABLE,
        collection_timestamp=now,
        records=(),
        truncation=Truncation(0, None),
        error=WorkloadErrorCode.UNAVAILABLE,
    )


def _invalid_source(
    owner: WorkloadOwner, now: datetime, code: WorkloadErrorCode
) -> SourceResult:
    return SourceResult(
        owner=owner,
        status=ResultStatus.UNAVAILABLE,
        collection_timestamp=now,
        records=(),
        truncation=Truncation(0, None),
        error=code,
    )


def _validated_source(
    owner: WorkloadOwner,
    source: object,
    *,
    host: str,
    query: WorkloadQuery,
    now: datetime,
) -> SourceResult:
    if type(source) is not SourceResult:
        raise _refuse("invalid workload source result")
    if type(source.owner) is not WorkloadOwner or source.owner is not owner:
        raise _refuse("invalid workload source result")
    if type(source.status) is not ResultStatus:
        raise _refuse("invalid workload source result")
    if type(source.collection_timestamp) is not datetime:
        raise _refuse("invalid workload source result")
    if type(source.records) is not tuple or len(source.records) > SOURCE_LIMIT:
        raise _refuse("invalid workload source result")
    if source.error is not None and type(source.error) is not WorkloadErrorCode:
        raise _refuse("invalid workload source result")
    error = source.error
    collection_timestamp = normalize_workload_timestamp(source.collection_timestamp)
    records = tuple(_validated_record(record) for record in source.records)
    truncation = _validated_truncation(source.truncation)
    reconstructed = SourceResult(
        owner=source.owner,
        status=source.status,
        collection_timestamp=collection_timestamp,
        records=records,
        truncation=truncation,
        error=error,
    )
    validate_source_records(
        records,
        owner=owner,
        host=host,
        collection_timestamp=collection_timestamp,
    )
    selected, selection = select_records(records, query, now=now)
    if selected != records or selection.omitted != 0:
        raise _refuse("workload source result does not satisfy the query")
    if collection_timestamp > now + timedelta(seconds=MAX_FUTURE_SECONDS):
        raise _fixed_error(
            WorkloadErrorCode.FUTURE, "workload source timestamp is in the future"
        )
    return reconstructed


def _validated_sources_shape(sources: object) -> dict[WorkloadOwner, object]:
    if type(sources) is not dict or len(sources) > MAX_SOURCES_PER_NODE:
        raise _refuse("invalid workload source mapping")
    if any(type(owner) is not WorkloadOwner for owner in sources):
        raise _refuse("invalid workload source mapping")
    return sources


def _increment_omitted(omitted: int | None, removed: int) -> int | None:
    if omitted is None:
        return None
    value = omitted + removed
    return value if value <= MAX_COUNT else None


def _reduce_sources(
    sources: tuple[SourceResult, ...], query: WorkloadQuery
) -> tuple[SourceResult, ...]:
    entries = [
        _Entry(source_index, record_index, record)
        for source_index, source in enumerate(sources)
        for record_index, record in enumerate(source.records)
    ]
    entries.sort(key=lambda entry: entry.record.id)
    entries.sort(key=lambda entry: entry.record.updated_at, reverse=True)
    keep = {
        (entry.source_index, entry.record_index)
        for entry in entries[: min(query.limit, AGGREGATE_LIMIT)]
    }
    reduced: list[SourceResult] = []
    for source_index, source in enumerate(sources):
        records = tuple(
            record
            for record_index, record in enumerate(source.records)
            if (source_index, record_index) in keep
        )
        removed = len(source.records) - len(records)
        status = source.status
        if removed and status is ResultStatus.COMPLETE:
            status = ResultStatus.PARTIAL
        reduced.append(
            SourceResult(
                owner=source.owner,
                status=status,
                collection_timestamp=source.collection_timestamp,
                records=records,
                truncation=Truncation(
                    len(records),
                    _increment_omitted(source.truncation.omitted, removed),
                ),
                error=source.error,
            )
        )
    return tuple(reduced)


def _node_status(sources: tuple[SourceResult, ...]) -> ResultStatus:
    if all(source.status is ResultStatus.UNAVAILABLE for source in sources):
        return ResultStatus.UNAVAILABLE
    if any(source.status is not ResultStatus.COMPLETE for source in sources):
        return ResultStatus.PARTIAL
    return ResultStatus.COMPLETE


def build_node_workloads(
    host: str,
    query: WorkloadQuery,
    now: datetime,
    sources: dict[WorkloadOwner, SourceResult | None],
) -> NodeResult:
    """Validate and merge at most six canonical workload source results."""

    checked_host = _validated_host(host)
    checked_query = _validated_query(query)
    checked_now = _validated_now(now)
    checked_sources = _validated_sources_shape(sources)

    validated: list[SourceResult] = []
    for owner in _OWNERS:
        source = checked_sources.get(owner)
        if source is None:
            validated.append(_unavailable(owner, checked_now))
            continue
        try:
            validated.append(
                _validated_source(
                    owner,
                    source,
                    host=checked_host,
                    query=checked_query,
                    now=checked_now,
                )
            )
        except WorkloadError as exc:
            code = (
                WorkloadErrorCode.FUTURE
                if exc.code is WorkloadErrorCode.FUTURE
                else WorkloadErrorCode.INVALID
            )
            validated.append(_invalid_source(owner, checked_now, code))
        except Exception:
            validated.append(
                _invalid_source(owner, checked_now, WorkloadErrorCode.INVALID)
            )

    reduced = _reduce_sources(tuple(validated), checked_query)
    return NodeResult(
        host=checked_host,
        status=_node_status(reduced),
        collection_timestamp=checked_now,
        sources=reduced,
    )


__all__ = ["build_node_workloads"]
