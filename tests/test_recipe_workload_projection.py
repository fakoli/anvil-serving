"""Recipe workload projection uses only validated, bounded observations."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone


from anvil_serving.observability.workloads import (
    ResultStatus,
    WorkloadErrorCode,
    WorkloadQuery,
    WorkloadState,
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
