from dataclasses import replace
from datetime import datetime, timedelta, timezone


from anvil_serving.observability.fleet_workload_collection import (
    build_fleet_workloads,
    normalize_node_workloads,
)
from anvil_serving.observability.workload_collection import build_node_workloads
from anvil_serving.observability.workloads import (
    AGGREGATE_LIMIT,
    ObservationQuality,
    ResultStatus,
    SourceAuthority,
    SourceResult,
    Truncation,
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


def test_none_invalid_wrong_host_and_future_header_have_fixed_six_source_fallbacks():
    good = _node("node-a", {WorkloadOwner.MEDIA: _source(WorkloadOwner.MEDIA)})
    wrong = replace(good, host="node-b")
    future = replace(good, collection_timestamp=NOW + timedelta(seconds=30, microseconds=1))
    for node, code in ((None, WorkloadErrorCode.UNAVAILABLE), (wrong, WorkloadErrorCode.INVALID), (future, WorkloadErrorCode.FUTURE)):
        normalized = normalize_node_workloads("node-a", WorkloadQuery(), NOW, node)
        assert len(normalized.sources) == 6
        assert all(source.error is code for source in normalized.sources)
