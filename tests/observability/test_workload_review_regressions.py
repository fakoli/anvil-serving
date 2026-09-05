"""Independent temporal boundary probes from the consolidated source review."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.fleet_workload_collection import (
    build_fleet_workloads,
    normalize_node_workloads,
)
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    FleetResult, NodeResult, ObservationQuality, ResultStatus, SourceAuthority,
    SourceResult, Truncation, WorkloadError, WorkloadErrorCode, WorkloadKind,
    WorkloadOwner, WorkloadPhase, WorkloadQuery, WorkloadRecord, WorkloadState,
    fleet_result_from_dict, fleet_result_to_dict, node_result_from_dict,
    node_result_to_dict, workload_record_from_dict, workload_record_to_dict,
)

NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
SKEW = timedelta(seconds=30)


def _record(at=NOW, *, owner=WorkloadOwner.BENCHMARK):
    return WorkloadRecord(
        id=("a" if owner is WorkloadOwner.BENCHMARK else "b") * 64,
        kind=(WorkloadKind.BENCHMARK_JOB if owner is WorkloadOwner.BENCHMARK
              else WorkloadKind.CONTROLLER_OPERATION),
        owner=owner, host="node-a", state=WorkloadState.RUNNING,
        phase=WorkloadPhase.RUNNING, outcome=None,
        created_at=at, updated_at=at, source_timestamp=at,
        source_authority=(SourceAuthority.BENCHMARK_STORE
                          if owner is WorkloadOwner.BENCHMARK
                          else SourceAuthority.CONTROLLER_STORE),
        observation_quality=ObservationQuality.RECORDED,
    )


def _source(record, collected=NOW):
    return SourceResult(record.owner, ResultStatus.COMPLETE, collected,
                        (record,), Truncation(1, 0))


@pytest.mark.parametrize("offset", [timedelta(microseconds=1), timedelta(seconds=20)])
def test_updated_after_source_is_not_a_canonical_record(offset):
    record = _record()
    with pytest.raises(WorkloadError) as error:
        replace(record, updated_at=NOW + offset)
    assert error.value.code is WorkloadErrorCode.INVALID
    wire = workload_record_to_dict(record)
    wire["updated_at"] = (NOW + offset).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with pytest.raises(WorkloadError):
        workload_record_from_dict(wire)


@pytest.mark.parametrize("excess", [timedelta(0), timedelta(microseconds=1), SKEW])
def test_node_receipt_bound_is_not_added_to_source_clock(excess):
    bad = _source(_record(NOW + SKEW + excess), NOW + SKEW)
    good = _source(_record(owner=WorkloadOwner.CONTROLLER))
    result = build_node_workloads("node-a", WorkloadQuery(), NOW, {
        WorkloadOwner.BENCHMARK: bad, WorkloadOwner.CONTROLLER: good,
    })
    sources = {source.owner: source for source in result.sources}
    assert sources[WorkloadOwner.CONTROLLER] == good
    observed = sources[WorkloadOwner.BENCHMARK]
    if not excess:
        assert observed == bad
    else:
        assert observed.status is ResultStatus.UNAVAILABLE
        assert observed.error is WorkloadErrorCode.FUTURE
        assert observed.records == ()


def test_node_decoder_rejects_descendant_skew_against_node_collection():
    source = _source(_record(NOW + SKEW), NOW + SKEW)
    node = NodeResult("node-a", ResultStatus.COMPLETE, NOW, (source,))
    wire = node_result_to_dict(node)
    row = wire["sources"][0]["records"][0]
    for key in ("created_at", "updated_at", "source_timestamp"):
        row[key] = "2026-09-05T12:00:30.000001Z"
    with pytest.raises(WorkloadError) as error:
        node_result_from_dict(wire)
    assert error.value.code is WorkloadErrorCode.FUTURE


@pytest.mark.parametrize("excess", [timedelta(0), timedelta(microseconds=1), SKEW])
def test_fleet_receipt_preserves_peer_but_does_not_stack_node_skew(excess):
    source = _source(_record(NOW + SKEW + excess), NOW + SKEW)
    good = _source(_record(owner=WorkloadOwner.CONTROLLER))
    remote = NodeResult("node-a", ResultStatus.COMPLETE, NOW + SKEW, (source, good))
    normalized = normalize_node_workloads("node-a", WorkloadQuery(), NOW, remote)
    fleet = build_fleet_workloads(("node-a",), WorkloadQuery(), NOW, {"node-a": remote})
    for node in (normalized, fleet.nodes[0]):
        sources = {item.owner: item for item in node.sources}
        assert sources[WorkloadOwner.CONTROLLER] == good
        observed = sources[WorkloadOwner.BENCHMARK]
        if not excess:
            assert observed == source
        else:
            assert observed.error is WorkloadErrorCode.FUTURE
            assert not observed.records


@pytest.mark.parametrize("target", ["source_collection", "record"])
def test_fleet_decoder_checks_every_descendant_against_fleet_collection(target):
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


def test_fleet_source_time_failure_is_isolated_before_final_decode():
    good = _source(_record(owner=WorkloadOwner.CONTROLLER))
    future = _source(_record(), NOW + SKEW + timedelta(microseconds=1))
    remote = NodeResult("node-a", ResultStatus.COMPLETE, NOW + SKEW, (future, good))
    fleet = build_fleet_workloads(("node-a",), WorkloadQuery(), NOW, {"node-a": remote})
    assert fleet.truncation.returned == 1
    assert fleet.nodes[0].sources[0].error is WorkloadErrorCode.FUTURE
    assert fleet_result_from_dict(fleet_result_to_dict(fleet)) == fleet


@pytest.mark.parametrize("owner", [WorkloadOwner.RECIPE, WorkloadOwner.MANIFEST])
def test_managed_component_clock_keeps_its_exact_observation_skew(owner):
    record = replace(
        _record(), owner=owner, kind=WorkloadKind.RECIPE_SERVE,
        source_authority=SourceAuthority.MANAGED_STATUS,
        observation_quality=ObservationQuality.OBSERVED_RUNNING,
        updated_at=NOW + SKEW,
    )
    assert _source(record).records == (record,)
    with pytest.raises(WorkloadError) as error:
        replace(record, updated_at=NOW + SKEW + timedelta(microseconds=1))
    assert error.value.code is WorkloadErrorCode.FUTURE
