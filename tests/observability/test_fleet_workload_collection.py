from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.fleet_workload_collection import (
    build_fleet_workloads,
    normalize_node_workloads,
)
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    AGGREGATE_LIMIT,
    MAX_COUNT,
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
    workload_id,
)


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
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


def _record(host, owner, index, *, updated=None):
    updated = updated or NOW - timedelta(microseconds=index)
    state = WorkloadState.CHECKING if owner is WorkloadOwner.ROUTER else WorkloadState.RUNNING
    phase = WorkloadPhase.CHECKING if state is WorkloadState.CHECKING else WorkloadPhase.RUNNING
    return WorkloadRecord(
        id=workload_id(host, KINDS[owner], owner, f"{owner.value}-{index}"),
        kind=KINDS[owner], owner=owner, host=host, state=state, phase=phase,
        outcome=None, created_at=updated - timedelta(microseconds=1), updated_at=updated,
        source_timestamp=updated, source_authority=AUTHORITIES[owner],
        observation_quality=(ObservationQuality.RECORDED if owner not in {WorkloadOwner.RECIPE, WorkloadOwner.MANIFEST} else ObservationQuality.OBSERVED_RUNNING),
    )


def _source(owner, records=(), *, omitted=0, collected=NOW, status=ResultStatus.COMPLETE):
    return SourceResult(owner, status, collected, tuple(records), Truncation(len(records), omitted))


def _node(host, sources):
    return build_node_workloads(host, WorkloadQuery(limit=1000), NOW, sources)


def test_two_full_nodes_reduce_incrementally_to_one_global_cap_and_round_trip():
    nodes = {}
    all_records = []
    for host_index, host in enumerate(("node-a", "node-b")):
        sources = {}
        for owner_index, owner in enumerate(OWNERS):
            count = 200 if owner_index < 4 else 100
            records = tuple(
                _record(host, owner, index, updated=NOW - timedelta(microseconds=host_index * 1000 + owner_index * 200 + index))
                for index in range(count)
            )
            sources[owner] = _source(owner, records)
            all_records.extend(records)
        nodes[host] = _node(host, sources)
    result = build_fleet_workloads(("node-b", "node-a"), WorkloadQuery(limit=1000), NOW, nodes)
    all_records.sort(key=lambda record: record.id)
    all_records.sort(key=lambda record: record.updated_at, reverse=True)
    expected = {record.id for record in all_records[:AGGREGATE_LIMIT]}
    actual = {record.id for node in result.nodes for source in node.sources for record in source.records}
    assert tuple(node.host for node in result.nodes) == ("node-a", "node-b")
    assert result.truncation.returned == AGGREGATE_LIMIT
    assert actual == expected
    assert result.truncation.omitted == AGGREGATE_LIMIT
    assert fleet_result_from_dict(fleet_result_to_dict(result)) == result


def test_global_ties_are_stable_and_preserve_source_relative_order():
    timestamp = NOW - timedelta(seconds=1)
    node_a = _node("node-a", {WorkloadOwner.ROUTER: _source(WorkloadOwner.ROUTER, (_record("node-a", WorkloadOwner.ROUTER, 2, updated=timestamp), _record("node-a", WorkloadOwner.ROUTER, 1, updated=timestamp)))})
    node_b = _node("node-b", {WorkloadOwner.ROUTER: _source(WorkloadOwner.ROUTER, (_record("node-b", WorkloadOwner.ROUTER, 1, updated=timestamp),))})
    result = build_fleet_workloads(("node-b", "node-a"), WorkloadQuery(limit=2), NOW, {"node-a": node_a, "node-b": node_b})
    records = [record for node in result.nodes for source in node.sources for record in source.records]
    assert [record.id for record in records] == sorted(record.id for record in records)
    node_a_records = next(source.records for source in result.nodes[0].sources if source.owner is WorkloadOwner.ROUTER)
    assert node_a_records == tuple(record for record in node_a.sources[-1].records if record in node_a_records)


def test_excluded_host_never_inspects_its_node_value_and_empty_fleet_is_complete():
    class Sentinel:
        def __getattribute__(self, name):
            raise AssertionError(name)

    excluded = build_fleet_workloads(("node-a",), WorkloadQuery(host="node-b"), NOW, {"node-a": Sentinel()})
    assert excluded.status is ResultStatus.COMPLETE
    assert all(source.status is ResultStatus.COMPLETE for source in excluded.nodes[0].sources)
    empty = build_fleet_workloads((), WorkloadQuery(), NOW, {})
    assert empty == FleetResult(ResultStatus.COMPLETE, NOW, (), Truncation(0, 0))


def test_none_invalid_wrong_host_and_future_header_have_fixed_six_source_fallbacks():
    good = _node("node-a", {WorkloadOwner.MEDIA: _source(WorkloadOwner.MEDIA)})
    wrong = replace(good, host="node-b")
    future = replace(good, collection_timestamp=NOW + timedelta(seconds=30, microseconds=1))
    for node, code in ((None, WorkloadErrorCode.UNAVAILABLE), (wrong, WorkloadErrorCode.INVALID), (future, WorkloadErrorCode.FUTURE)):
        normalized = normalize_node_workloads("node-a", WorkloadQuery(), NOW, node)
        assert len(normalized.sources) == 6
        assert all(source.error is code for source in normalized.sources)


@pytest.mark.parametrize("mutation", ("status", "duplicate-owner", "oversized"))
def test_forged_node_headers_are_one_fixed_invalid_node(mutation):
    source = _source(WorkloadOwner.MEDIA)
    node = NodeResult("node-a", ResultStatus.COMPLETE, NOW, (source,))
    if mutation == "status":
        object.__setattr__(node, "status", ResultStatus.PARTIAL)
    elif mutation == "duplicate-owner":
        object.__setattr__(node, "sources", (source, source))
    else:
        object.__setattr__(node, "sources", (source,) * 7)
    normalized = normalize_node_workloads("node-a", WorkloadQuery(), NOW, node)
    assert normalized.status is ResultStatus.UNAVAILABLE
    assert all(source.error is WorkloadErrorCode.INVALID for source in normalized.sources)


def test_source_local_failure_uses_stale_node_time_and_preserves_peer():
    stale = NOW - timedelta(days=1)
    good = _source(WorkloadOwner.MEDIA, (_record("node-a", WorkloadOwner.MEDIA, 0, updated=stale),), collected=stale)
    bad = _source(WorkloadOwner.ROUTER, (_record("node-a", WorkloadOwner.ROUTER, 0, updated=stale),), collected=stale)
    node = NodeResult("node-a", ResultStatus.COMPLETE, stale, (bad, good))
    object.__setattr__(bad.records[0], "host", "wrong-node")
    result = normalize_node_workloads("node-a", WorkloadQuery(), NOW, node)
    by_owner = {source.owner: source for source in result.sources}
    assert by_owner[WorkloadOwner.ROUTER].error is WorkloadErrorCode.INVALID
    assert by_owner[WorkloadOwner.ROUTER].collection_timestamp == stale
    assert by_owner[WorkloadOwner.MEDIA].collection_timestamp == stale


def test_unknown_and_overflow_omissions_propagate_after_repeated_evictions():
    first_source = _source(WorkloadOwner.ROUTER, (_record("node-a", WorkloadOwner.ROUTER, 0),), omitted=None, status=ResultStatus.PARTIAL)
    second_source = _source(WorkloadOwner.ROUTER, (_record("node-b", WorkloadOwner.ROUTER, 0, updated=NOW + timedelta(microseconds=1)),), omitted=MAX_COUNT, status=ResultStatus.PARTIAL)
    first = _node("node-a", {WorkloadOwner.ROUTER: first_source})
    second = _node("node-b", {WorkloadOwner.ROUTER: second_source})
    result = build_fleet_workloads(("node-a", "node-b"), WorkloadQuery(limit=1), NOW, {"node-a": first, "node-b": second})
    assert result.truncation.omitted is None
    source = next(item for item in result.nodes[0].sources if item.owner is WorkloadOwner.ROUTER)
    assert source.truncation.omitted is None


def test_invalid_outer_input_refuses_before_node_values_are_touched():
    class Sentinel:
        def __getattribute__(self, name):
            raise AssertionError(name)

    with pytest.raises(WorkloadError):
        build_fleet_workloads(("node-a",), WorkloadQuery(), NOW, {"wrong": Sentinel()})
    with pytest.raises(WorkloadError):
        build_fleet_workloads(("node-a", "node-a"), WorkloadQuery(), NOW, {"node-a": Sentinel()})
