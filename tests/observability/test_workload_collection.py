from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    MAX_COUNT,
    NodeResult,
    ObservationQuality,
    Progress,
    ResultStatus,
    SourceAuthority,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadState,
    node_result_from_dict,
    node_result_to_dict,
    workload_id,
)

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
HOST = "node-a"
OWNERS = tuple(sorted(WorkloadOwner, key=lambda owner: owner.value))
KINDS = {
    WorkloadOwner.ROUTER: WorkloadKind.ROUTER_REQUEST,
    WorkloadOwner.CONTROLLER: WorkloadKind.CONTROLLER_OPERATION,
    WorkloadOwner.BENCHMARK: WorkloadKind.BENCHMARK_JOB,
    WorkloadOwner.MEDIA: WorkloadKind.MEDIA_JOB,
    WorkloadOwner.RECIPE: WorkloadKind.RECIPE_SERVE,
    WorkloadOwner.MANIFEST: WorkloadKind.RECIPE_SERVE,
}
AUTHORITIES = {
    WorkloadOwner.ROUTER: SourceAuthority.ROUTER_MEMORY,
    WorkloadOwner.CONTROLLER: SourceAuthority.CONTROLLER_STORE,
    WorkloadOwner.BENCHMARK: SourceAuthority.BENCHMARK_STORE,
    WorkloadOwner.MEDIA: SourceAuthority.MEDIA_STORE,
    WorkloadOwner.RECIPE: SourceAuthority.MANAGED_STATUS,
    WorkloadOwner.MANIFEST: SourceAuthority.MANAGED_STATUS,
}


def _record(
    owner: WorkloadOwner,
    index: int,
    *,
    updated_at: datetime | None = None,
    state: WorkloadState | None = None,
) -> WorkloadRecord:
    updated = updated_at or NOW - timedelta(seconds=index)
    native = f"{owner.value}-{index:04d}"
    actual_state = state or (
        WorkloadState.CHECKING
        if owner is WorkloadOwner.ROUTER
        else WorkloadState.RUNNING
    )
    phase = {
        WorkloadState.CHECKING: WorkloadPhase.CHECKING,
        WorkloadState.RUNNING: WorkloadPhase.RUNNING,
        WorkloadState.ABSENT: WorkloadPhase.ABSENT,
    }[actual_state]
    return WorkloadRecord(
        id=workload_id(HOST, KINDS[owner], owner, native),
        kind=KINDS[owner],
        owner=owner,
        host=HOST,
        state=actual_state,
        phase=phase,
        outcome=None,
        created_at=updated - timedelta(seconds=1),
        updated_at=updated,
        source_timestamp=updated,
        source_authority=AUTHORITIES[owner],
        observation_quality=(
            ObservationQuality.ABSENT
            if actual_state is WorkloadState.ABSENT
            else (
                ObservationQuality.OBSERVED_RUNNING
                if owner in {WorkloadOwner.RECIPE, WorkloadOwner.MANIFEST}
                else ObservationQuality.RECORDED
            )
        ),
    )


def _source(
    owner: WorkloadOwner,
    records: tuple[WorkloadRecord, ...] = (),
    *,
    status: ResultStatus = ResultStatus.COMPLETE,
    omitted: int | None = 0,
    collected_at: datetime = NOW,
) -> SourceResult:
    return SourceResult(
        owner=owner,
        status=status,
        collection_timestamp=collected_at,
        records=records,
        truncation=Truncation(len(records), omitted),
    )


def test_composes_all_sources_in_lexical_order_and_round_trips() -> None:
    source = _source(WorkloadOwner.ROUTER, (_record(WorkloadOwner.ROUTER, 0),))

    result = build_node_workloads(HOST, WorkloadQuery(), NOW, {source.owner: source})

    assert result.status is ResultStatus.PARTIAL
    assert tuple(item.owner for item in result.sources) == OWNERS
    assert next(item for item in result.sources if item.owner is source.owner) == source
    missing = tuple(item for item in result.sources if item.owner is not source.owner)
    assert all(item.status is ResultStatus.UNAVAILABLE for item in missing)
    assert all(item.error is WorkloadErrorCode.UNAVAILABLE for item in missing)
    assert node_result_from_dict(node_result_to_dict(result)) == result


def test_all_missing_sources_are_unavailable() -> None:
    result = build_node_workloads(HOST, WorkloadQuery(), NOW, {})

    assert result.status is ResultStatus.UNAVAILABLE
    assert len(result.sources) == 6
    assert all(source.collection_timestamp == NOW for source in result.sources)


def test_six_by_two_hundred_keeps_exact_newest_thousand() -> None:
    sources: dict[WorkloadOwner, SourceResult] = {}
    expected: list[WorkloadRecord] = []
    for owner_index, owner in enumerate(OWNERS):
        records = tuple(
            _record(
                owner,
                index,
                updated_at=NOW - timedelta(microseconds=owner_index * 200 + index),
            )
            for index in range(200)
        )
        sources[owner] = _source(owner, records)
        expected.extend(records)
    expected.sort(key=lambda record: record.id)
    expected.sort(key=lambda record: record.updated_at, reverse=True)
    expected_ids = {record.id for record in expected[:1000]}

    result = build_node_workloads(
        HOST, WorkloadQuery(limit=1000), NOW, sources
    )

    returned = tuple(record for source in result.sources for record in source.records)
    assert len(returned) == 1000
    assert {record.id for record in returned} == expected_ids
    assert result.status is ResultStatus.PARTIAL
    assert sum(source.truncation.omitted or 0 for source in result.sources) == 200
    assert all(
        source.status is ResultStatus.PARTIAL
        for source in result.sources
        if source.truncation.omitted
    )


def test_lower_query_limit_redistributes_with_exact_omissions() -> None:
    sources = {
        WorkloadOwner.ROUTER: _source(
            WorkloadOwner.ROUTER,
            tuple(_record(WorkloadOwner.ROUTER, index) for index in range(2)),
        ),
        WorkloadOwner.RECIPE: _source(
            WorkloadOwner.RECIPE,
            tuple(
                _record(
                    WorkloadOwner.RECIPE,
                    index,
                    updated_at=NOW - timedelta(microseconds=index + 1),
                )
                for index in range(2)
            ),
        ),
    }

    result = build_node_workloads(HOST, WorkloadQuery(limit=2), NOW, sources)

    by_owner = {source.owner: source for source in result.sources}
    assert sum(len(source.records) for source in result.sources) == 2
    assert by_owner[WorkloadOwner.ROUTER].truncation.omitted == 1
    assert by_owner[WorkloadOwner.RECIPE].truncation.omitted == 1


def test_unknown_and_overflow_omissions_remain_honest() -> None:
    partial = _source(
        WorkloadOwner.ROUTER,
        (_record(WorkloadOwner.ROUTER, 0),),
        status=ResultStatus.PARTIAL,
        omitted=None,
    )
    overflow = _source(
        WorkloadOwner.RECIPE,
        (
            _record(
                WorkloadOwner.RECIPE,
                0,
                updated_at=NOW - timedelta(microseconds=1),
            ),
        ),
        status=ResultStatus.PARTIAL,
        omitted=MAX_COUNT,
    )

    result = build_node_workloads(
        HOST, WorkloadQuery(limit=1), NOW, {partial.owner: partial, overflow.owner: overflow}
    )
    by_owner = {source.owner: source for source in result.sources}

    assert by_owner[WorkloadOwner.ROUTER].truncation.omitted is None
    assert by_owner[WorkloadOwner.RECIPE].truncation.omitted is None


def test_equal_timestamp_and_id_ties_keep_owner_order() -> None:
    shared_id = workload_id(
        HOST, WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER, "shared"
    )
    entries = {}
    for owner in (WorkloadOwner.BENCHMARK, WorkloadOwner.CONTROLLER):
        record = replace(_record(owner, 0), id=shared_id)
        entries[owner] = _source(owner, (record,))

    result = build_node_workloads(HOST, WorkloadQuery(limit=1), NOW, entries)
    kept = [source.owner for source in result.sources if source.records]

    assert kept == [WorkloadOwner.BENCHMARK]


@pytest.mark.parametrize(
    "mutate,code",
    [
        (
            lambda source: replace(
                source,
                collection_timestamp=NOW + timedelta(seconds=30, microseconds=1),
            ),
            WorkloadErrorCode.FUTURE,
        ),
        (
                lambda source: replace(
                    source,
                    records=(
                        replace(source.records[0], host="wrong-node"),
                        *source.records[1:],
                    ),
            ),
            WorkloadErrorCode.INVALID,
        ),
        (
            lambda source: replace(
                source,
                records=tuple(reversed(source.records)),
            ),
            WorkloadErrorCode.INVALID,
        ),
    ],
)
def test_bad_source_is_owner_local_and_healthy_peer_survives(mutate, code) -> None:
    router = _source(
        WorkloadOwner.ROUTER,
        (_record(WorkloadOwner.ROUTER, 0), _record(WorkloadOwner.ROUTER, 1)),
    )
    recipe = _source(WorkloadOwner.RECIPE, (_record(WorkloadOwner.RECIPE, 0),))

    result = build_node_workloads(
        HOST,
        WorkloadQuery(),
        NOW,
        {router.owner: mutate(router), recipe.owner: recipe},
    )
    by_owner = {source.owner: source for source in result.sources}

    assert by_owner[WorkloadOwner.ROUTER].status is ResultStatus.UNAVAILABLE
    assert by_owner[WorkloadOwner.ROUTER].error is code
    assert by_owner[WorkloadOwner.RECIPE] == recipe


def test_query_filter_and_limit_must_already_be_applied() -> None:
    source = _source(
        WorkloadOwner.RECIPE,
        (
            _record(WorkloadOwner.RECIPE, 0),
            _record(WorkloadOwner.RECIPE, 1, state=WorkloadState.ABSENT),
        ),
    )

    result = build_node_workloads(
        HOST, WorkloadQuery(active_only=True, limit=1), NOW, {source.owner: source}
    )
    invalid = next(item for item in result.sources if item.owner is source.owner)

    assert invalid.status is ResultStatus.UNAVAILABLE
    assert invalid.error is WorkloadErrorCode.INVALID


def test_invalid_outer_shape_refuses_before_values_are_traversed() -> None:
    class Sentinel:
        def __getattribute__(self, name):
            raise AssertionError(name)

    with pytest.raises(WorkloadError, match="invalid workload source mapping"):
        build_node_workloads(HOST, WorkloadQuery(), NOW, {"bad": Sentinel()})


def test_forged_query_is_revalidated_before_source_access() -> None:
    query = WorkloadQuery()
    object.__setattr__(query, "limit", True)

    with pytest.raises(WorkloadError, match="invalid node workload query"):
        build_node_workloads(HOST, query, NOW, {})


def test_forged_record_and_truncation_are_reconstructed() -> None:
    record = _record(WorkloadOwner.ROUTER, 0)
    object.__setattr__(record, "updated_at", "private-value")
    source = _source(WorkloadOwner.ROUTER, ())
    object.__setattr__(source, "records", (record,))
    object.__setattr__(source, "truncation", Truncation(1, 0))

    result = build_node_workloads(HOST, WorkloadQuery(), NOW, {source.owner: source})
    invalid = next(item for item in result.sources if item.owner is source.owner)

    assert invalid.error is WorkloadErrorCode.INVALID
    assert "private-value" not in repr(result)


def test_forged_progress_is_reconstructed() -> None:
    progress = Progress(1, 1)
    object.__setattr__(progress, "completed", True)
    record = replace(_record(WorkloadOwner.ROUTER, 0), progress=progress)
    source = _source(WorkloadOwner.ROUTER, (record,))

    result = build_node_workloads(HOST, WorkloadQuery(), NOW, {source.owner: source})
    invalid = next(item for item in result.sources if item.owner is source.owner)

    assert invalid.error is WorkloadErrorCode.INVALID


def test_oversized_source_is_rejected_before_record_traversal() -> None:
    class Sentinel:
        def __getattribute__(self, name):
            raise AssertionError(name)

    source = _source(WorkloadOwner.ROUTER)
    object.__setattr__(source, "records", tuple(Sentinel() for _ in range(201)))
    object.__setattr__(source, "truncation", Truncation(201, 0))

    result = build_node_workloads(HOST, WorkloadQuery(), NOW, {source.owner: source})
    invalid = next(item for item in result.sources if item.owner is source.owner)

    assert invalid.error is WorkloadErrorCode.INVALID


def test_source_collection_allows_exact_future_boundary() -> None:
    source = _source(
        WorkloadOwner.ROUTER,
        (_record(WorkloadOwner.ROUTER, 0),),
        collected_at=NOW + timedelta(seconds=30),
    )

    result = build_node_workloads(HOST, WorkloadQuery(), NOW, {source.owner: source})

    assert next(item for item in result.sources if item.owner is source.owner) == source


def test_inputs_are_unchanged() -> None:
    source = _source(WorkloadOwner.ROUTER, (_record(WorkloadOwner.ROUTER, 0),))
    sources = {source.owner: source}

    build_node_workloads(HOST, WorkloadQuery(), NOW, sources)

    assert sources == {source.owner: source}
    assert sources[source.owner] is source


def test_complete_empty_sources_produce_complete_node() -> None:
    sources = {owner: _source(owner) for owner in OWNERS}

    result = build_node_workloads(HOST, WorkloadQuery(), NOW, sources)

    assert result == NodeResult(HOST, ResultStatus.COMPLETE, NOW, tuple(sources.values()))
