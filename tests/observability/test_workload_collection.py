from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    ObservationQuality,
    Progress,
    ResultStatus,
    SourceAuthority,
    SourceResult,
    Truncation,
    WorkloadKind,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadState,
    node_result_from_json,
    node_result_to_json,
    workload_record_to_json,
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


@pytest.mark.parametrize("completed,total", [(1, None), (1, 2), (0, 0)])
def test_canonical_progress_totals_survive_composition(completed, total) -> None:
    record = replace(_record(WorkloadOwner.ROUTER, 0), progress=Progress(completed, total))
    source = _source(record.owner, (record,))
    before = workload_record_to_json(record)

    result = build_node_workloads(HOST, WorkloadQuery(), NOW, {source.owner: source})
    observed = next(item for item in result.sources if item.owner is source.owner)

    assert observed == source
    assert observed.records[0] is not record
    assert observed.records[0].progress is not record.progress
    assert workload_record_to_json(observed.records[0]) == before
    assert node_result_from_json(node_result_to_json(result)) == result
    assert workload_record_to_json(record) == before


class _ProgressInt(int):
    pass
