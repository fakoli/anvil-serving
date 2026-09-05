"""Bounded router workload registry and terminal projection tests."""
from __future__ import annotations

import dataclasses
import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.workloads import (
    ObservationQuality,
    ResultStatus,
    WorkloadError,
    WorkloadOutcome,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadState,
    format_workload_timestamp,
    parse_workload_timestamp,
    workload_record_to_dict,
)
from anvil_serving.router.decision_log import (
    AttemptRecord,
    DecisionLog,
    DecisionLogWriter,
    DecisionRecord,
)
from anvil_serving.router.workloads import RouterWorkloadRegistry

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


class _Clock:
    def __init__(self, when: datetime = NOW) -> None:
        self.when = when
        self.error: Exception | None = None
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            if self.error is not None:
                raise self.error
            self.when += timedelta(microseconds=1)
            return self.when


def _gateway(index: int) -> str:
    return f"req_{index:032x}"


def _decision(index: int, **kwargs: object) -> DecisionRecord:
    values: dict[str, object] = {
        "kind": "chat",
        "requested_tier": "primary-local",
        "attempts": (
            AttemptRecord("primary-local", True, "served", 1, 1, "served"),
        ),
        "served_tier": "primary-local",
        "total_prompt_tokens": 1,
        "total_completion_tokens": 1,
        "route": "llm.primary",
        "gateway_request_id": _gateway(index),
    }
    values.update(kwargs)
    return DecisionRecord(**values)  # type: ignore[arg-type]


def _activated(
    registry: RouterWorkloadRegistry, index: int = 1
):
    token = registry.begin(_gateway(index))
    assert token.activate()
    return token


def test_token_is_inert_until_valid_activation_and_phases_are_ordered() -> None:
    registry = RouterWorkloadRegistry(DecisionLog(), clock=_Clock())
    invalid = registry.begin("caller/private/value")
    assert not invalid.activate()
    assert not invalid.advance(WorkloadState.ADMITTED)
    assert registry.active_count == 0

    token = registry.begin(_gateway(1))
    assert registry.active_count == 0
    assert token.activate()
    assert token.activate()
    assert not token.advance(WorkloadState.DISPATCHED)
    assert not token.advance("admitted")  # type: ignore[arg-type]
    assert token.advance(WorkloadState.ADMITTED)
    assert token.advance(WorkloadState.ADMITTED)
    assert token.advance(WorkloadState.DISPATCHED)
    assert token.advance(WorkloadState.STREAMING)
    assert not token.advance(WorkloadState.ADMITTED)

    result = registry.source_result("node-a", WorkloadQuery(), NOW + timedelta(seconds=1))
    assert result.status is ResultStatus.COMPLETE
    assert [(record.state, record.phase) for record in result.records] == [
        (WorkloadState.STREAMING, WorkloadPhase.STREAMING)
    ]
    assert result.records[0].observation_quality is ObservationQuality.RECORDED


def test_terminal_is_proposed_then_committed_once_with_delivery_override() -> None:
    log = DecisionLog()
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    token = _activated(registry)
    assert token.advance(WorkloadState.ADMITTED)
    assert token.propose_terminal(_decision(1), WorkloadOutcome.SUCCESS)
    assert len(log) == 0

    assert token.finish(WorkloadOutcome.DISCONNECTED)
    assert not token.finish(WorkloadOutcome.SUCCESS)
    assert registry.active_count == 0
    assert len(log) == 1
    assert log.last is not None
    assert log.last.workload_outcome == "disconnected"

    result = registry.source_result("node-a", WorkloadQuery(), NOW + timedelta(seconds=1))
    assert len(result.records) == 1
    record = result.records[0]
    assert record.state is WorkloadState.TERMINAL
    assert record.phase is WorkloadPhase.FAILED
    assert record.outcome is WorkloadOutcome.DISCONNECTED


def test_concurrent_finish_has_one_append_and_no_active_record() -> None:
    log = DecisionLog()
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    token = _activated(registry)
    assert token.propose_terminal(_decision(1), WorkloadOutcome.SUCCESS)
    barrier = threading.Barrier(16)
    results: list[bool] = []

    def finish() -> None:
        barrier.wait()
        results.append(token.finish())

    threads = [threading.Thread(target=finish) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    assert results.count(False) == 15
    assert len(log) == 1
    assert registry.active_count == 0


def test_saturation_is_exact_until_finish_and_terminal_still_projects() -> None:
    log = DecisionLog()
    registry = RouterWorkloadRegistry(log, clock=_Clock(), max_active=2)
    tokens = [_activated(registry, index) for index in range(1, 4)]
    assert registry.active_count == 2
    assert registry.unrepresented_count == 1

    result = registry.source_result("node-a", WorkloadQuery(), NOW + timedelta(seconds=1))
    assert len(result.records) == 2
    assert result.status is ResultStatus.PARTIAL
    assert result.truncation.omitted == 1

    assert tokens[2].advance(WorkloadState.ADMITTED)
    checking = registry.source_result(
        "node-a", WorkloadQuery(state=WorkloadState.CHECKING),
        NOW + timedelta(seconds=1),
    )
    admitted = registry.source_result(
        "node-a", WorkloadQuery(state=WorkloadState.ADMITTED),
        NOW + timedelta(seconds=1),
    )
    assert checking.truncation.omitted == 0
    assert admitted.truncation.omitted == 1

    assert tokens[2].propose_terminal(_decision(3), WorkloadOutcome.REJECTED)
    assert tokens[2].finish()
    assert registry.unrepresented_count == 0
    terminal = registry.source_result(
        "node-a", WorkloadQuery(), NOW + timedelta(seconds=1)
    )
    assert len(terminal.records) == 3
    assert any(record.outcome is WorkloadOutcome.REJECTED for record in terminal.records)
    for token in tokens[:2]:
        token.finish()


def test_terminal_supersedes_same_identity_while_sink_holds_finish() -> None:
    sink_entered = threading.Event()
    release_sink = threading.Event()

    def sink(_record: DecisionRecord) -> None:
        sink_entered.set()
        assert release_sink.wait(timeout=5)

    log = DecisionLog(sink=sink)
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    token = _activated(registry)
    assert token.propose_terminal(_decision(1), WorkloadOutcome.SUCCESS)
    thread = threading.Thread(target=token.finish)
    thread.start()
    assert sink_entered.wait(timeout=5)

    result = registry.source_result("node-a", WorkloadQuery(), NOW + timedelta(seconds=1))
    assert len(result.records) == 1
    assert result.records[0].state is WorkloadState.TERMINAL
    release_sink.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert registry.active_count == 0


class _RaisingDecisionLog(DecisionLog):
    def record(self, record: DecisionRecord) -> None:
        raise RuntimeError("private sink detail")


def test_clock_and_log_failures_disable_observation_and_cleanup() -> None:
    clock = _Clock()
    registry = RouterWorkloadRegistry(_RaisingDecisionLog(), clock=clock)
    token = _activated(registry)
    assert token.propose_terminal(_decision(1), WorkloadOutcome.ERROR)
    assert token.finish()
    assert registry.active_count == 0

    clock = _Clock()
    registry = RouterWorkloadRegistry(DecisionLog(), clock=clock)
    token = _activated(registry)
    clock.error = RuntimeError("private clock detail")
    assert not token.advance(WorkloadState.ADMITTED)
    assert registry.active_count == 0
    assert token.finish()


def test_recent_is_bounded_without_changing_full_snapshot() -> None:
    log = DecisionLog(max_records=None)
    for index in range(600):
        log.record(_decision(index))
    assert len(log.records) == 600
    recent = log.recent()
    assert len(recent) == 512
    assert recent[0].gateway_request_id == _gateway(88)
    assert recent[-1].gateway_request_id == _gateway(599)
    for value in (True, 0, 513, "1"):
        with pytest.raises(ValueError, match="integer from 1 to 512"):
            log.recent(value)  # type: ignore[arg-type]


def test_projection_marks_history_beyond_bounded_scan_as_unknown_omission() -> None:
    log = DecisionLog(max_records=None)
    created = format_workload_timestamp(NOW)
    updated = format_workload_timestamp(NOW + timedelta(microseconds=1))
    for index in range(513):
        log.record(dataclasses.replace(
            _decision(index),
            workload_created_at=created,
            workload_updated_at=updated,
            workload_outcome="success",
        ))
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    result = registry.source_result(
        "node-a", WorkloadQuery(), NOW + timedelta(seconds=1)
    )
    assert len(result.records) == 200
    assert result.status is ResultStatus.PARTIAL
    assert result.truncation.omitted is None


def test_malformed_workload_metadata_is_dropped_before_memory_and_sink(
    tmp_path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    writer = DecisionLogWriter(str(path))
    log = DecisionLog(sink=writer)
    secret = "private-input-material"
    log.record(dataclasses.replace(
        _decision(1),
        workload_created_at="2026-99-99T00:00:00.000000Z",
        workload_updated_at="2026-12-01T00:00:00.000000Z",
        workload_outcome=secret,
    ))
    assert log.last is not None
    assert log.last.workload_created_at is None
    assert log.last.workload_updated_at is None
    assert log.last.workload_outcome is None
    payload = path.read_text(encoding="utf-8")
    assert secret not in payload
    assert "workload_created_at" not in payload


def test_ordinary_writer_shape_is_unchanged_when_workload_fields_are_absent(
    tmp_path,
) -> None:
    path = tmp_path / "decisions.jsonl"
    record = _decision(1)
    DecisionLogWriter(str(path))(record)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == set(dataclasses.asdict(record)) - {
        "workload_created_at", "workload_updated_at", "workload_outcome",
    }


def test_projection_excludes_legacy_caller_fields_and_uses_trusted_host() -> None:
    prohibited = (
        "caller-private-correlation",
        "prompt-private-content",
        "response-private-content",
        "tool-private-content",
        "endpoint-private-content",
        "credential-private-content",
        "exception-private-content",
    )
    log = DecisionLog()
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    token = _activated(registry)
    decision = _decision(
        2,
        kind=prohibited[1],
        requested_tier=prohibited[4],
        attempts=(AttemptRecord(
            prohibited[4], False, prohibited[6], 0, 0, prohibited[3]
        ),),
        served_tier=None,
        route=prohibited[2],
        request_id=prohibited[0],
        workbench_run_id=prohibited[5],
        task_id=prohibited[0],
    )
    assert token.propose_terminal(decision, WorkloadOutcome.SUCCESS)
    assert token.finish()

    first = registry.source_result("node-a", WorkloadQuery(), NOW + timedelta(seconds=1))
    second = registry.source_result("node-b", WorkloadQuery(), NOW + timedelta(seconds=1))
    assert first.records[0].id != second.records[0].id
    projection = repr(workload_record_to_dict(first.records[0]))
    assert all(value not in projection for value in prohibited)
    assert "request_id" not in projection
    assert first.records[0].host == "node-a"


def test_query_filters_all_bounded_records_before_applying_source_limit() -> None:
    registry = RouterWorkloadRegistry(
        DecisionLog(max_records=None), clock=_Clock(), max_active=1024
    )
    tokens = [_activated(registry, index) for index in range(1024)]
    for token in tokens[-24:]:
        assert token.advance(WorkloadState.ADMITTED)
    result = registry.source_result(
        "node-a",
        WorkloadQuery(state=WorkloadState.ADMITTED, limit=20),
        NOW + timedelta(seconds=1),
    )
    assert len(result.records) == 20
    assert all(record.state is WorkloadState.ADMITTED for record in result.records)
    assert result.truncation.omitted == 4
    for token in tokens:
        token.finish()


def test_public_timestamp_helpers_are_exact_and_safe() -> None:
    value = NOW.replace(microsecond=123)
    encoded = format_workload_timestamp(value)
    assert encoded == "2026-09-05T12:00:00.000123Z"
    assert parse_workload_timestamp(encoded) == value
    with pytest.raises(WorkloadError) as exc_info:
        parse_workload_timestamp("private-invalid-timestamp")
    assert "private-invalid-timestamp" not in str(exc_info.value)
