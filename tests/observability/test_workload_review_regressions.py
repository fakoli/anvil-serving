from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.fleet_workload_collection import build_fleet_workloads
from anvil_serving.observability.workloads import (
    FleetResult,
    NodeResult,
    ObservationQuality,
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
    fleet_result_from_dict,
    fleet_result_to_dict,
    node_result_from_dict,
    node_result_to_dict,
    workload_record_from_dict,
    workload_record_to_dict,
)

NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
SKEW = timedelta(seconds=30)


def _record(at: datetime = NOW, *, owner: WorkloadOwner = WorkloadOwner.BENCHMARK) -> WorkloadRecord:
    return WorkloadRecord(
        id=("a" if owner is WorkloadOwner.BENCHMARK else "b") * 64,
        kind=WorkloadKind.BENCHMARK_JOB if owner is WorkloadOwner.BENCHMARK else WorkloadKind.CONTROLLER_OPERATION,
        owner=owner,
        host="node-a",
        state=WorkloadState.RUNNING,
        phase=WorkloadPhase.RUNNING,
        outcome=None,
        created_at=at,
        updated_at=at,
        source_timestamp=at,
        source_authority=SourceAuthority.BENCHMARK_STORE if owner is WorkloadOwner.BENCHMARK else SourceAuthority.CONTROLLER_STORE,
        observation_quality=ObservationQuality.RECORDED,
    )


def _source(record: WorkloadRecord, collected: datetime = NOW) -> SourceResult:
    return SourceResult(record.owner, ResultStatus.COMPLETE, collected, (record,), Truncation(1, 0))


@pytest.mark.parametrize("offset", [pytest.param(timedelta(microseconds=1), id="0:00:00.000001")])
def test_updated_after_source_is_not_a_canonical_record(offset: timedelta) -> None:
    record = _record()
    with pytest.raises(WorkloadError) as error:
        replace(record, updated_at=NOW + offset)
    assert error.value.code is WorkloadErrorCode.INVALID
    wire = workload_record_to_dict(record)
    wire["updated_at"] = (NOW + offset).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with pytest.raises(WorkloadError):
        workload_record_from_dict(wire)


def test_node_decoder_rejects_descendant_skew_against_node_collection() -> None:
    source = _source(_record(NOW + SKEW), NOW + SKEW)
    node = NodeResult("node-a", ResultStatus.COMPLETE, NOW, (source,))
    wire = node_result_to_dict(node)
    row = wire["sources"][0]["records"][0]
    for key in ("created_at", "updated_at", "source_timestamp"):
        row[key] = "2026-09-05T12:00:30.000001Z"
    with pytest.raises(WorkloadError) as error:
        node_result_from_dict(wire)
    assert error.value.code is WorkloadErrorCode.FUTURE


@pytest.mark.parametrize("target", [pytest.param("record", id="record")])
def test_fleet_decoder_checks_every_descendant_against_fleet_collection(target: str) -> None:
    source = _source(_record(NOW + SKEW), NOW + SKEW)
    node = NodeResult("node-a", ResultStatus.COMPLETE, NOW + SKEW, (source,))
    fleet = FleetResult(ResultStatus.COMPLETE, NOW, (node,), Truncation(1, 0))
    wire = fleet_result_to_dict(fleet)
    nested = wire["nodes"][0]["sources"][0]
    if target == "source_collection":
        nested["collection_timestamp"] = "2026-09-05T12:00:30.000001Z"
    else:
        for key in ("created_at", "updated_at", "source_timestamp"):
            nested["records"][0][key] = "2026-09-05T12:00:30.000001Z"
    with pytest.raises(WorkloadError) as error:
        fleet_result_from_dict(wire)
    assert error.value.code is WorkloadErrorCode.FUTURE


def test_fleet_source_time_failure_is_isolated_before_final_decode() -> None:
    good = _source(_record(owner=WorkloadOwner.CONTROLLER))
    future = _source(_record(), NOW + SKEW + timedelta(microseconds=1))
    remote = NodeResult("node-a", ResultStatus.COMPLETE, NOW + SKEW, (future, good))
    fleet = build_fleet_workloads(("node-a",), WorkloadQuery(), NOW, {"node-a": remote})
    assert fleet.truncation.returned == 1
    assert fleet.nodes[0].sources[0].error is WorkloadErrorCode.FUTURE
    assert fleet_result_from_dict(fleet_result_to_dict(fleet)) == fleet
