"""Recipe workload projection uses only validated, bounded observations."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.workloads import (
    ObservationQuality,
    ResultStatus,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOwner,
    WorkloadQuery,
    WorkloadState,
    source_result_to_json,
)
from anvil_serving.serve_recipes import (
    RecipeComponentResult,
    RecipeConfiguredObservation,
    RecipeContainerObservation,
    RecipeWorkloadSnapshot,
    list_recipe_workloads,
)


_NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
_DIGEST = "a" * 64
_CONTAINER = "b" * 64


def _component(records=(), *, observed_at=_NOW):
    return RecipeComponentResult(
        ResultStatus.COMPLETE, observed_at, tuple(records), 0, None
    )


def _configured(digest=_DIGEST, *, observed_at=_NOW):
    return RecipeConfiguredObservation(digest, _NOW - timedelta(minutes=1), observed_at)


def _runtime(
    container_id=_CONTAINER,
    digest=_DIGEST,
    *,
    state=WorkloadState.RUNNING,
    observed_at=_NOW,
    created_at=None,
    updated_at=None,
):
    created = created_at or _NOW - timedelta(minutes=2)
    updated = updated_at or _NOW - timedelta(seconds=1)
    return RecipeContainerObservation(
        container_id, digest, state, created, updated, observed_at
    )


def _snapshot(configuration=(), runtime=()):
    return RecipeWorkloadSnapshot(_component(configuration), _component(runtime))


def _project(snapshot, query=WorkloadQuery()):
    return list_recipe_workloads(
        "synthetic-registry.toml",
        "node-a",
        query,
        _NOW,
        snapshot_reader=lambda *_args, **_kwargs: snapshot,
    )


def test_query_truncation_changes_complete_source_to_partial_with_exact_omission():
    observations = tuple(
        _runtime(f"{index:064x}", f"{index + 1000:064x}")
        for index in range(201)
    )

    result = _project(_snapshot(runtime=observations), WorkloadQuery(limit=1000))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is None
    assert len(result.records) == result.truncation.returned == 200
    assert result.truncation.omitted == 1


def test_invalid_or_future_runtime_cannot_suppress_valid_configuration():
    future = replace(_runtime(), observed_at=_NOW + timedelta(seconds=31))

    result = _project(_snapshot((_configured(),), (future,)))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.FUTURE
    assert len(result.records) == 1
    assert result.records[0].state is WorkloadState.CONFIGURED


@pytest.mark.parametrize(
    ("bad_component", "error"),
    [
        (object(), WorkloadErrorCode.INVALID),
        (
            RecipeComponentResult(ResultStatus.COMPLETE, _NOW, (), None, None),
            WorkloadErrorCode.INVALID,
        ),
        (
            RecipeComponentResult(ResultStatus.COMPLETE, None, (), 0, None),
            WorkloadErrorCode.INVALID,
        ),
        (
            RecipeComponentResult(ResultStatus.COMPLETE, _NOW, [], 0, None),
            WorkloadErrorCode.INVALID,
        ),
        (
            RecipeComponentResult(
                ResultStatus.COMPLETE, _NOW, (), 1_000_000_001, None
            ),
            WorkloadErrorCode.INVALID,
        ),
        (
            RecipeComponentResult(
                ResultStatus.COMPLETE,
                _NOW + timedelta(seconds=31),
                (),
                0,
                None,
            ),
            WorkloadErrorCode.FUTURE,
        ),
        (
            RecipeComponentResult(
                ResultStatus.UNAVAILABLE,
                None,
                (),
                0,
                WorkloadErrorCode.UNAVAILABLE,
            ),
            WorkloadErrorCode.INVALID,
        ),
    ],
)
def test_malformed_component_preserves_independently_valid_peer(bad_component, error):
    snapshot = RecipeWorkloadSnapshot(_component((_configured(),)), bad_component)

    result = _project(snapshot)

    assert result.status is ResultStatus.PARTIAL
    assert result.error is error
    assert result.truncation.omitted is None
    assert [record.state for record in result.records] == [WorkloadState.CONFIGURED]


def test_oversized_component_is_quarantined_without_reading_its_rows():
    oversized = RecipeComponentResult(
        ResultStatus.COMPLETE,
        _NOW,
        tuple(_runtime(f"{index:064x}") for index in range(257)),
        0,
        None,
    )

    result = _project(RecipeWorkloadSnapshot(_component((_configured(),)), oversized))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert [record.state for record in result.records] == [WorkloadState.CONFIGURED]


def test_bad_individual_row_is_quarantined_without_discarding_valid_component_peer():
    runtime = RecipeComponentResult(
        ResultStatus.COMPLETE,
        _NOW,
        (_runtime(), object()),
        0,
        None,
    )

    result = _project(RecipeWorkloadSnapshot(_component(), runtime))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert result.truncation.omitted is None
    assert len(result.records) == 1
    assert result.records[0].state is WorkloadState.RUNNING


def test_bad_configured_row_is_quarantined_without_discarding_valid_peer():
    invalid = replace(_configured("c" * 64), recipe_digest="C" * 64)

    result = _project(_snapshot((_configured(), invalid)))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert len(result.records) == 1
    assert result.records[0].state is WorkloadState.CONFIGURED


@pytest.mark.parametrize(
    "bad_runtime",
    [
        replace(_runtime(), container_id="B" * 64),
        replace(_runtime(), recipe_digest="A" * 64),
        replace(_runtime(), state=WorkloadState.CONFIGURED),
        replace(_runtime(), created_at=datetime(2026, 9, 5, 11, 0, 0)),
        replace(_runtime(), updated_at=True),
        replace(_runtime(), observed_at=datetime(2026, 9, 5, 12, 0, 0)),
    ],
)
def test_malformed_runtime_row_does_not_hide_valid_configured_peer(bad_runtime):
    result = _project(_snapshot((_configured(),), (bad_runtime,)))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert result.truncation.omitted is None
    assert [record.state for record in result.records] == [WorkloadState.CONFIGURED]


def test_invalid_and_future_rows_are_quarantined_with_fixed_error_precedence():
    invalid = replace(_runtime("c" * 64, "d" * 64), recipe_digest="D" * 64)
    future = replace(
        _runtime("e" * 64, "f" * 64),
        observed_at=_NOW + timedelta(seconds=31),
    )
    result = _project(_snapshot((_configured(),), (future, invalid)))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert result.truncation.omitted is None
    assert [record.state for record in result.records] == [WorkloadState.CONFIGURED]


def test_duplicate_runtime_keeps_first_valid_identity_without_suppressing_second_digest():
    second_digest = "c" * 64
    first = _runtime(_CONTAINER, _DIGEST)
    duplicate = replace(first, recipe_digest=second_digest)
    result = _project(
        _snapshot((_configured(), _configured(second_digest)), (first, duplicate))
    )

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert {record.state for record in result.records} == {
        WorkloadState.RUNNING,
        WorkloadState.CONFIGURED,
    }


def test_unknown_runtime_digest_suppresses_nothing_and_distinct_containers_survive():
    first = _runtime(_CONTAINER, None)
    second = _runtime("c" * 64, _DIGEST)

    unknown = _project(_snapshot((_configured(),), (first,)))
    matched = _project(_snapshot((_configured(),), (first, second)))

    assert unknown.status is ResultStatus.COMPLETE
    assert {record.state for record in unknown.records} == {
        WorkloadState.CONFIGURED,
        WorkloadState.RUNNING,
    }
    assert matched.status is ResultStatus.COMPLETE
    assert len(matched.records) == 2
    assert all(record.state is WorkloadState.RUNNING for record in matched.records)


def test_duplicate_configuration_keeps_first_valid_record_and_reports_partial():
    result = _project(_snapshot((_configured(), _configured())))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert len(result.records) == 1
    assert result.records[0].state is WorkloadState.CONFIGURED


def test_component_and_row_timestamps_use_exact_thirty_second_future_boundary():
    exact_component = _component(
        (_runtime(observed_at=_NOW),),
        observed_at=_NOW - timedelta(seconds=30),
    )
    future_component = replace(
        exact_component,
        observed_at=_NOW - timedelta(seconds=30, microseconds=1),
    )

    accepted = _project(RecipeWorkloadSnapshot(_component(), exact_component))
    rejected = _project(RecipeWorkloadSnapshot(_component(), future_component))

    assert accepted.status is ResultStatus.COMPLETE
    assert len(accepted.records) == 1
    assert rejected.status is ResultStatus.PARTIAL
    assert rejected.error is WorkloadErrorCode.FUTURE
    assert rejected.records == ()


def test_lifecycle_timestamp_uses_exact_component_and_collection_future_boundary():
    exact = _runtime(
        observed_at=_NOW,
        updated_at=_NOW + timedelta(seconds=30),
    )
    future = replace(exact, container_id="c" * 64, updated_at=_NOW + timedelta(
        seconds=30, microseconds=1
    ))

    result = _project(_snapshot(runtime=(exact, future)))

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.FUTURE
    assert len(result.records) == 1


def test_freshness_boundary_is_exact_and_active_only_excludes_stale_running():
    fresh = _runtime("b" * 64, "d" * 64, observed_at=_NOW - timedelta(seconds=30))
    stale = _runtime(
        "c" * 64,
        "e" * 64,
        observed_at=_NOW - timedelta(seconds=30, microseconds=1),
    )
    snapshot = _snapshot(runtime=(fresh, stale))

    all_records = _project(snapshot)
    active = _project(snapshot, WorkloadQuery(active_only=True))

    assert {record.observation_quality for record in all_records.records} == {
        ObservationQuality.OBSERVED_RUNNING,
        ObservationQuality.STALE,
    }
    assert len(active.records) == 1
    assert active.records[0].observation_quality is ObservationQuality.OBSERVED_RUNNING


def test_filters_precede_source_cap_and_normal_omissions_are_exact():
    observations = tuple(
        _runtime(
            f"{index:064x}",
            f"{index + 1000:064x}",
            state=(
                WorkloadState.ABSENT if index < 220 else WorkloadState.RUNNING
            ),
        )
        for index in range(256)
    )
    snapshot = _snapshot(runtime=observations)

    limited = _project(snapshot, WorkloadQuery(limit=1000))
    filtered = _project(
        snapshot, WorkloadQuery(state=WorkloadState.RUNNING, limit=1000)
    )

    assert limited.status is ResultStatus.PARTIAL
    assert limited.error is None
    assert len(limited.records) == 200
    assert limited.truncation == Truncation(200, 56)
    assert filtered.status is ResultStatus.COMPLETE
    assert len(filtered.records) == 36
    assert filtered.truncation == Truncation(36, 0)
    assert all(record.state is WorkloadState.RUNNING for record in filtered.records)
    for query in (
        WorkloadQuery(owner=WorkloadOwner.MANIFEST),
        WorkloadQuery(kind=WorkloadKind.ROUTER_REQUEST),
        WorkloadQuery(host="node-b"),
    ):
        result = _project(snapshot, query)
        assert result.status is ResultStatus.COMPLETE
        assert result.records == ()
        assert result.truncation == Truncation(0, 0)


def test_incomplete_component_retains_unknown_omission_after_query_exclusion():
    partial = RecipeComponentResult(
        ResultStatus.PARTIAL,
        _NOW,
        (_configured(),),
        None,
        WorkloadErrorCode.INVALID,
    )
    snapshot = RecipeWorkloadSnapshot(partial, _component())

    result = _project(snapshot, WorkloadQuery(owner=WorkloadOwner.MANIFEST))

    assert result.status is ResultStatus.PARTIAL
    assert result.records == ()
    assert result.truncation == Truncation(0, None)
    assert result.error is WorkloadErrorCode.INVALID


def test_empty_successful_peer_and_both_failed_sources_are_distinct():
    unavailable = RecipeComponentResult(
        ResultStatus.UNAVAILABLE,
        None,
        (),
        None,
        WorkloadErrorCode.UNAVAILABLE,
    )

    partial = _project(RecipeWorkloadSnapshot(_component(), unavailable))
    failed = _project(RecipeWorkloadSnapshot(unavailable, unavailable))

    assert partial.status is ResultStatus.PARTIAL
    assert partial.error is WorkloadErrorCode.UNAVAILABLE
    assert partial.truncation.omitted is None
    assert failed.status is ResultStatus.UNAVAILABLE
    assert failed.error is WorkloadErrorCode.UNAVAILABLE
    assert failed.records == ()


def test_query_host_and_collection_time_validate_before_snapshot_read():
    calls = []

    def reader(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("snapshot reader must not run")

    tampered = WorkloadQuery()
    object.__setattr__(tampered, "limit", 0)
    invocations = (
        ("node-a", tampered, _NOW),
        ("bad host", WorkloadQuery(), _NOW),
        ("node-a", WorkloadQuery(), _NOW.replace(tzinfo=None)),
        ("node-a", object(), _NOW),
    )
    for host, query, now in invocations:
        with pytest.raises((ValueError, WorkloadError)):
            list_recipe_workloads(
                "private-registry-path", host, query, now, snapshot_reader=reader
            )
    assert calls == []


def test_reader_failure_and_malformed_snapshot_return_fixed_safe_results():
    def failed_reader(*_args, **_kwargs):
        raise RuntimeError("private/path token http://100.64.0.10:8000")

    failed = list_recipe_workloads(
        "private/path", "node-a", WorkloadQuery(), _NOW,
        snapshot_reader=failed_reader,
    )
    malformed = _project(object.__new__(RecipeWorkloadSnapshot))

    assert failed == SourceResult(
        WorkloadOwner.RECIPE,
        ResultStatus.UNAVAILABLE,
        _NOW,
        (),
        Truncation(0, None),
        WorkloadErrorCode.UNAVAILABLE,
    )
    assert malformed.status is ResultStatus.UNAVAILABLE
    assert malformed.error is WorkloadErrorCode.UNAVAILABLE
    assert "private" not in source_result_to_json(failed)
