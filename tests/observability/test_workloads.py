from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Callable

import pytest

from anvil_serving.observability.workloads import (
    AGGREGATE_LIMIT,
    MAX_JSON_BYTES,
    MAX_SOURCES_PER_NODE,
    MAX_TEXT_LENGTH,
    SOURCE_LIMIT,
    FleetResult,
    Freshness,
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
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadState,
    fleet_result_from_dict,
    fleet_result_from_json,
    fleet_result_to_dict,
    fleet_result_to_json,
    map_unknown_owner_state,
    node_result_from_json,
    node_result_to_json,
    parse_workload_query,
    select_records,
    select_managed_records,
    source_result_from_dict,
    source_result_from_json,
    source_result_to_dict,
    source_result_to_json,
    validate_source_records,
    workload_id,
    workload_record_from_dict,
    workload_record_from_json,
    workload_record_to_dict,
    workload_record_to_json,
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


def test_record_source_node_and_fleet_round_trip() -> None:
    record = _record()
    source = _source(records=(record,))
    node = _node(sources=(source,))
    fleet = _fleet(nodes=(node,))
    assert workload_record_from_json(workload_record_to_json(record)) == record
    assert source_result_from_json(source_result_to_json(source)) == source
    assert node_result_from_json(node_result_to_json(node)) == node
    assert fleet_result_from_json(fleet_result_to_json(fleet)) == fleet
    assert fleet_result_from_dict(fleet_result_to_dict(fleet)) == fleet


@pytest.mark.parametrize("owner", list(WorkloadOwner))
def test_explicit_unknown_state_is_representable_for_every_owner(
    owner: WorkloadOwner,
) -> None:
    state, phase, outcome = map_unknown_owner_state(owner)
    quality = (
        ObservationQuality.INSPECTION_ERROR
        if owner in {WorkloadOwner.RECIPE, WorkloadOwner.MANIFEST}
        else ObservationQuality.RECORDED
    )
    record = _record(
        "unknown", owner=owner, state=state, phase=phase, outcome=outcome, quality=quality
    )
    assert workload_record_from_dict(workload_record_to_dict(record)) == record


@pytest.mark.parametrize(
    ("owner", "state", "phase", "outcome", "quality"),
    [
        (WorkloadOwner.ROUTER, WorkloadState.RUNNING, WorkloadPhase.RUNNING, None, ObservationQuality.STALE),
        (WorkloadOwner.ROUTER, WorkloadState.CONFIGURED, WorkloadPhase.CONFIGURED, None, ObservationQuality.RECORDED),
        (WorkloadOwner.CONTROLLER, WorkloadState.STREAMING, WorkloadPhase.STREAMING, None, ObservationQuality.RECORDED),
        (WorkloadOwner.RECIPE, WorkloadState.QUEUED, WorkloadPhase.QUEUED, None, ObservationQuality.STALE),
        (WorkloadOwner.RECIPE, WorkloadState.RUNNING, WorkloadPhase.RUNNING, None, ObservationQuality.CONFIGURED),
        (WorkloadOwner.RECIPE, WorkloadState.CONFIGURED, WorkloadPhase.CONFIGURED, None, ObservationQuality.HEALTHY_IDENTITY),
        (WorkloadOwner.RECIPE, WorkloadState.UNAVAILABLE, WorkloadPhase.UNAVAILABLE, WorkloadOutcome.UNAVAILABLE, ObservationQuality.STALE),
        (WorkloadOwner.ROUTER, WorkloadState.RUNNING, WorkloadPhase.COMPLETED, WorkloadOutcome.SUCCESS, ObservationQuality.RECORDED),
    ],
)
def test_invalid_owner_state_phase_outcome_quality_combinations_rejected(
    owner: WorkloadOwner,
    state: WorkloadState,
    phase: WorkloadPhase,
    outcome: WorkloadOutcome | None,
    quality: ObservationQuality,
) -> None:
    with pytest.raises(WorkloadError, match="incompatible"):
        _record(owner=owner, state=state, phase=phase, outcome=outcome, quality=quality)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"age_seconds": -1, "is_stale": False},
        {"age_seconds": float("inf"), "is_stale": True},
        {"age_seconds": True, "is_stale": False},
        {"age_seconds": 31, "is_stale": False},
        {"age_seconds": 1, "is_stale": "no"},
        {"age_seconds": 1, "is_stale": False, "stale_after_seconds": True},
    ],
)
def test_freshness_rejects_invalid_or_inconsistent_values(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(WorkloadError, match="freshness"):
        Freshness(**kwargs)  # type: ignore[arg-type]


def test_explicit_managed_stale_is_visible_by_default_but_not_active_only() -> None:
    record = _record(
        owner=WorkloadOwner.RECIPE,
        quality=ObservationQuality.STALE,
        source_timestamp=NOW - timedelta(seconds=5),
    )
    returned, _ = select_records((record,), WorkloadQuery(), now=NOW)
    active, _ = select_records((record,), WorkloadQuery(active_only=True), now=NOW)
    assert returned == (record,)
    assert active == ()


def test_source_rejects_duplicate_ids_wrong_types_truncation_and_future_times() -> None:
    record = _record()
    cases: list[Callable[[], object]] = [
        lambda: SourceResult(WorkloadOwner.ROUTER, ResultStatus.COMPLETE, NOW, (record, record), Truncation(2, 0)),
        lambda: SourceResult(WorkloadOwner.ROUTER, ResultStatus.COMPLETE, NOW, ("bad",), Truncation(1, 0)),  # type: ignore[arg-type]
        lambda: SourceResult(WorkloadOwner.ROUTER, ResultStatus.COMPLETE, NOW, (record,), Truncation(0, 1)),
        lambda: _source(records=(_record(updated_at=NOW + timedelta(seconds=31)),)),
        lambda: _source(records=(_record(source_timestamp=NOW + timedelta(seconds=31)),)),
    ]
    for build in cases:
        with pytest.raises(WorkloadError):
            build()


def test_source_and_fleet_nested_record_caps_and_actual_returned_count() -> None:
    records = tuple(_record(str(index)) for index in range(SOURCE_LIMIT))
    with pytest.raises(WorkloadError, match="supported bound"):
        validate_source_records(
            records + (_record("overflow"),),
            owner=WorkloadOwner.ROUTER,
            host="node-a",
            collection_timestamp=NOW,
        )
    owners = (
        WorkloadOwner.ROUTER,
        WorkloadOwner.CONTROLLER,
        WorkloadOwner.BENCHMARK,
        WorkloadOwner.MEDIA,
        WorkloadOwner.RECIPE,
    )
    five_sources = tuple(
        _source(
            owner=owner,
            records=tuple(
                _record(f"{owner.value}-{index}", owner=owner)
                for index in range(SOURCE_LIMIT)
            ),
        )
        for owner in owners
    )
    node = _node(sources=five_sources)
    fleet = _fleet(nodes=(node,))
    assert fleet.truncation.returned == AGGREGATE_LIMIT
    extra = _node(
        "node-b", (_source(records=(_record("extra", host="node-b"),)),)
    )
    with pytest.raises(WorkloadError, match="aggregate bound"):
        FleetResult(
            ResultStatus.COMPLETE,
            NOW,
            (node, extra),
            Truncation(AGGREGATE_LIMIT, 1),
        )


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


def test_managed_selection_has_a_bounded_five_hundred_twelve_row_preselection_window() -> None:
    records = tuple(
        _record(
            str(index), owner=WorkloadOwner.RECIPE,
            updated_at=NOW - timedelta(seconds=index),
            source_timestamp=NOW,
        )
        for index in range(512)
    )
    selected, truncation = select_managed_records(
        records, WorkloadQuery(limit=1000), now=NOW
    )
    assert len(selected) == SOURCE_LIMIT
    assert truncation == Truncation(SOURCE_LIMIT, 512 - SOURCE_LIMIT)
    with pytest.raises(WorkloadError):
        select_managed_records(records + (records[0],), WorkloadQuery(), now=NOW)
    with pytest.raises(WorkloadError):
        select_managed_records((_record(),), WorkloadQuery(), now=NOW)


@pytest.mark.parametrize(
    "build",
    [
        lambda: Progress(True, 1),
        lambda: Progress(1, True),
        lambda: Progress(1, 2, []),  # type: ignore[arg-type]
        lambda: Truncation(True, 0),
        lambda: Truncation(0, 1_000_000_001),
    ],
)
def test_progress_truncation_and_unit_fail_safely(
    build: Callable[[], object],
) -> None:
    with pytest.raises(WorkloadError):
        build()


class _BrokenTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        raise RuntimeError("secret timezone detail")


def test_custom_timezone_failures_are_safe_and_context_free() -> None:
    with pytest.raises(WorkloadError) as exc_info:
        _record(updated_at=datetime(2026, 9, 5, tzinfo=_BrokenTimezone()))
    assert exc_info.value.code is WorkloadErrorCode.INVALID
    assert "secret" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


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


def test_query_rejects_iterators_and_bounds_sequences_before_consumption() -> None:
    iterator = _OneShotIterator()
    with pytest.raises(WorkloadError, match="bounded"):
        parse_workload_query(iterator)  # type: ignore[arg-type]
    assert not iterator.consumed
    with pytest.raises(WorkloadError, match="too many"):
        parse_workload_query(_BombSequence())
    with pytest.raises(WorkloadError, match="key/value"):
        parse_workload_query([("limit",)])  # type: ignore[list-item]


def test_query_filters_and_limit_one_thousand() -> None:
    query = parse_workload_query(
        [
            ("owner", "router"),
            ("kind", "router-request"),
            ("host", "node-a"),
            ("active_only", True),
            ("state", "streaming"),
            ("recent_seconds", 10),
            ("limit", 1000),
        ]
    )
    assert query.limit == 1000
    records = (_record(), _record("other", owner=WorkloadOwner.CONTROLLER))
    assert select_records(records, query, now=NOW)[0] == (records[0],)


def test_exact_datetime_sort_preserves_microseconds_and_id_tie_break() -> None:
    first = _record("z", updated_at=NOW.replace(microsecond=1))
    second = _record("a", updated_at=NOW.replace(microsecond=2))
    tied_a = _record("a-tie", updated_at=NOW.replace(microsecond=3))
    tied_b = _record("b-tie", updated_at=NOW.replace(microsecond=3))
    returned, _ = select_records(
        (first, tied_b, second, tied_a), WorkloadQuery(), now=NOW
    )
    expected_tied = tuple(sorted((tied_a, tied_b), key=lambda item: item.id))
    assert returned == (*expected_tied, second, first)
    assert str(workload_record_to_dict(first)["updated_at"]).endswith(".000001Z")


def test_arrays_are_bounded_before_decoding_and_correct_schema_is_invalid() -> None:
    source_data = source_result_to_dict(_source())
    source_data["records"] = [None] * (SOURCE_LIMIT + 1)
    with pytest.raises(WorkloadError) as exc_info:
        source_result_from_dict(source_data)
    assert exc_info.value.code is WorkloadErrorCode.INVALID

    fleet_data = fleet_result_to_dict(_fleet())
    fleet_data["nodes"] = "not-an-array"
    with pytest.raises(WorkloadError) as exc_info:
        fleet_result_from_dict(fleet_data)
    assert exc_info.value.code is WorkloadErrorCode.INVALID

    fleet_data["schema"] = "anvil-workloads/v2"
    with pytest.raises(WorkloadError) as exc_info:
        fleet_result_from_dict(fleet_data)
    assert exc_info.value.code is WorkloadErrorCode.UNSUPPORTED


def test_invalid_fields_do_not_echo_raw_values() -> None:
    data = workload_record_to_dict(_record())
    data["outcome"] = ["private-input"]
    with pytest.raises(WorkloadError) as exc_info:
        workload_record_from_dict(data)
    assert exc_info.value.code is WorkloadErrorCode.INVALID
    assert "private-input" not in str(exc_info.value)


def test_maximum_valid_envelope_round_trips_within_common_json_cap() -> None:
    owners = (
        WorkloadOwner.ROUTER,
        WorkloadOwner.CONTROLLER,
        WorkloadOwner.BENCHMARK,
        WorkloadOwner.MEDIA,
        WorkloadOwner.RECIPE,
    )
    host = "h" * MAX_TEXT_LENGTH
    sources_list: list[SourceResult] = []
    for owner in owners:
        records = tuple(
            _record(f"{owner.value}-{index}", owner=owner, host=host)
            for index in range(SOURCE_LIMIT)
        )
        sources_list.append(_source(owner=owner, records=records))
    fleet = _fleet(nodes=(_node(host=host, sources=tuple(sources_list)),), omitted=None)
    payload = fleet_result_to_json(fleet)
    assert len(payload.encode()) <= MAX_JSON_BYTES
    assert fleet_result_from_json(payload) == fleet


def test_json_cap_is_shared_by_encode_and_decode() -> None:
    oversized = b"{" + b" " * MAX_JSON_BYTES + b"}"
    with pytest.raises(WorkloadError, match="supported bound"):
        fleet_result_from_json(oversized)


def test_json_numeric_limit_and_malformed_unicode_fail_safely() -> None:
    with pytest.raises(WorkloadError) as exc_info:
        fleet_result_from_json('{"number":' + "9" * 5000 + "}")
    assert exc_info.value.__cause__ is None
    with pytest.raises(WorkloadError) as exc_info:
        workload_id("node-a", WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER, "\ud800")
    assert exc_info.value.__cause__ is None


def test_source_status_and_six_owner_boundary() -> None:
    unavailable = _source(
        records=(), status=ResultStatus.UNAVAILABLE,
        error=WorkloadErrorCode.UNAVAILABLE, omitted=None,
    )
    assert _node(sources=(unavailable,)).status is ResultStatus.UNAVAILABLE
    sources = tuple(
        _source(owner=owner, records=(_record(owner=owner),))
        for owner in WorkloadOwner
    )
    assert len(_node(sources=sources).sources) == MAX_SOURCES_PER_NODE
    with pytest.raises(WorkloadError):
        _node(sources=sources + (sources[0],))


def test_exact_timestamp_decoder_rejects_noncanonical_timestamps() -> None:
    data = workload_record_to_dict(_record())
    for value in (
        "2026-09-05T12:00:00Z",
        "2026-09-05T12:00:00.000000+00:00",
    ):
        data["updated_at"] = value
        with pytest.raises(WorkloadError, match="microsecond-Z"):
            workload_record_from_dict(data)


def test_canonical_json_is_stable_and_labels_are_derived() -> None:
    payload = workload_record_to_json(_record())
    assert payload == json.dumps(
        json.loads(payload), sort_keys=True, separators=(",", ":")
    )
    data = json.loads(payload)
    data["label"] = "operator supplied label"
    with pytest.raises(WorkloadError, match="label"):
        workload_record_from_dict(data)


def test_canonical_identity_has_all_four_dimensions_and_no_native_text() -> None:
    identities = {
        workload_id("node-a", WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER, "native-secret"),
        workload_id("node-b", WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER, "native-secret"),
        workload_id("node-a", WorkloadKind.RECIPE_SERVE, WorkloadOwner.RECIPE, "native-secret"),
        workload_id("node-a", WorkloadKind.RECIPE_SERVE, WorkloadOwner.MANIFEST, "native-secret"),
        workload_id("node-a", WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER, "other"),
    }
    assert len(identities) == 5
    assert all(len(value) == 64 and set(value) <= set("0123456789abcdef") for value in identities)
    for bad in ("not-a-digest/private/path", "A" * 64, "a" * 63):
        with pytest.raises(WorkloadError):
            replace(_record(), id=bad)
    for bad in ("https://user:secret@10.1.2.3/private", "10.1.2.3", "../private", "node.example", "bad\n"):
        for build in (
            lambda: replace(_record(), host=bad),
            lambda: parse_workload_query({"host": bad}),
            lambda: NodeResult(bad, ResultStatus.COMPLETE, NOW, (_source(records=()),)),
        ):
            with pytest.raises(WorkloadError) as exc:
                build()
            assert bad not in str(exc.value)


def test_exact_record_fields_progress_and_absent_active_outcome() -> None:
    data = workload_record_to_dict(_record())
    assert "source_authority" in data and "authority" not in data
    assert "outcome" not in data
    assert data["progress"] == {"completed": 1, "total": 2, "unit": "items"}
    for unit in ("items", "steps", "requests"):
        assert Progress(1, 2, unit).unit == unit
    with pytest.raises(WorkloadError):
        Progress(1, 2, "samples")
    for bad in (None, "unknown"):
        with pytest.raises(WorkloadError):
            workload_record_from_dict({**data, "outcome": bad})
    with pytest.raises(WorkloadError):
        replace(_record(), outcome=WorkloadOutcome.UNKNOWN)


@pytest.mark.parametrize("phase", [WorkloadPhase.PREPARING, WorkloadPhase.SUBMITTING])
def test_media_preparation_is_running_not_queued(phase: WorkloadPhase) -> None:
    record = _record(owner=WorkloadOwner.MEDIA, state=WorkloadState.RUNNING, phase=phase)
    assert workload_record_from_json(workload_record_to_json(record)) == record
    with pytest.raises(WorkloadError):
        replace(record, state=WorkloadState.QUEUED)
    with pytest.raises(WorkloadError):
        _record(owner=WorkloadOwner.BENCHMARK, state=WorkloadState.RUNNING, phase=phase)


def test_queries_are_exact_scalars_and_state_filter_is_applied() -> None:
    for query in ({"include_recent": True}, {"kind": []}, {"host": ["node-a"]}, {"owner": None}, {"state": ["running"]}):
        with pytest.raises(WorkloadError):
            parse_workload_query(query)
    with pytest.raises(WorkloadError):
        parse_workload_query([("state", "running"), ("state", "queued")])
    running = _record(owner=WorkloadOwner.MEDIA)
    queued = _record("queued", owner=WorkloadOwner.MEDIA, state=WorkloadState.QUEUED, phase=WorkloadPhase.QUEUED)
    query = parse_workload_query({"state": "queued"})
    assert select_records((running, queued), query, now=NOW)[0] == (queued,)


def test_partiality_cannot_be_hidden_or_invented() -> None:
    for omitted in (None, 1):
        with pytest.raises(WorkloadError):
            _source(omitted=omitted)
        partial = _source(status=ResultStatus.PARTIAL, omitted=omitted)
        assert _node(sources=(partial,)).status is ResultStatus.PARTIAL
        with pytest.raises(WorkloadError):
            FleetResult(ResultStatus.COMPLETE, NOW, (_node(),), Truncation(1, omitted))
        assert _fleet(omitted=omitted).status is ResultStatus.PARTIAL
    with pytest.raises(WorkloadError):
        _source(status=ResultStatus.PARTIAL)


class _HostileRecords(Sequence[WorkloadRecord]):
    def __init__(self, fail_len: bool) -> None:
        self.fail_len = fail_len

    def __len__(self) -> int:
        if self.fail_len:
            raise RuntimeError("credential-shaped-secret")
        return 1

    def __getitem__(self, index: int) -> WorkloadRecord:
        raise RuntimeError("credential-shaped-secret")


def test_record_sequences_and_empty_helper_arguments_fail_safely() -> None:
    for records in (_HostileRecords(True), _HostileRecords(False)):
        for invoke in (
            lambda: select_records(records, WorkloadQuery(), now=NOW),
            lambda: validate_source_records(records, owner=WorkloadOwner.ROUTER, host="node-a", collection_timestamp=NOW),
        ):
            with pytest.raises(WorkloadError) as exc:
                invoke()
            assert "credential" not in str(exc.value)
            assert exc.value.__cause__ is None
    for invoke in (
        lambda: select_records((), WorkloadQuery(), now=NOW, aggregate="yes"),
        lambda: validate_source_records((), owner="router", host="node-a", collection_timestamp=NOW),
        lambda: validate_source_records((), owner=WorkloadOwner.ROUTER, host=[], collection_timestamp=NOW),
    ):
        with pytest.raises(WorkloadError):
            invoke()


def test_json_rejects_duplicate_keys_at_every_depth_and_nonfinite_constants() -> None:
    payload = workload_record_to_json(_record())
    duplicates = (
        payload.replace('"owner":"router"', '"owner":"router","owner":"router"'),
        payload.replace('"completed":1', '"completed":1,"completed":1'),
    )
    for value in (*duplicates, '{"secret":NaN}', '{"secret":Infinity}'):
        with pytest.raises(WorkloadError) as exc:
            workload_record_from_json(value)
        assert "secret" not in str(exc.value)


def test_timestamp_extremes_and_numeric_overflow_remain_typed() -> None:
    for when in (NOW.replace(year=1), datetime.max.replace(tzinfo=timezone.utc)):
        record = _record(updated_at=when, source_timestamp=when)
        source = _source(records=(record,), collected=when)
        assert source_result_from_json(source_result_to_json(source)) == source
    with pytest.raises(WorkloadError):
        Freshness(10 ** 1000, True)
