"""Bounded router workload registry and terminal projection tests."""
from __future__ import annotations

import dataclasses
import json
import threading
import io
import urllib.error
from email.message import Message as Headers
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.workloads import (
    ObservationQuality,
    ResultStatus,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadState,
    format_workload_timestamp,
    parse_workload_timestamp,
    workload_record_to_dict,
)
import anvil_serving.router.workloads as workload_registry
from anvil_serving.router.decision_log import (
    AttemptRecord,
    DecisionLog,
    DecisionLogWriter,
    DecisionRecord,
)
from anvil_serving.router.workloads import RouterWorkloadRegistry
from anvil_serving.router.serve import build_server
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.backends.relay import RelayBackend, RelayTimeoutError, _urlopen_transport
from tests.router.helpers import make_tier
from tests.router.test_streaming_relay import FakeStreamTransport

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
    assert result.truncation.omitted is None

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
    assert admitted.truncation.omitted is None

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


def test_future_candidates_are_quarantined_without_hiding_healthy_peers() -> None:
    collected = NOW + timedelta(seconds=1)
    log = DecisionLog()
    allowed = collected + timedelta(seconds=30)
    refused = allowed + timedelta(microseconds=1)
    for index, timestamp in ((2, allowed), (3, refused)):
        encoded = format_workload_timestamp(timestamp)
        log.record(dataclasses.replace(
            _decision(index),
            workload_created_at=encoded,
            workload_updated_at=encoded,
            workload_outcome="success",
        ))
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    token = _activated(registry, 1)

    result = registry.source_result("node-a", WorkloadQuery(), collected)

    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.FUTURE
    assert {record.state for record in result.records} == {
        WorkloadState.CHECKING, WorkloadState.TERMINAL,
    }
    assert all(record.updated_at != refused for record in result.records)
    token.finish()


def test_future_active_clock_is_quarantined_at_the_same_boundary() -> None:
    clock = _Clock(NOW + timedelta(seconds=30))
    registry = RouterWorkloadRegistry(DecisionLog(), clock=clock)
    token = _activated(registry)

    result = registry.source_result("node-a", WorkloadQuery(), NOW)

    assert result.records == ()
    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.FUTURE
    token.finish()


def test_invalid_terminal_candidate_is_partial_without_hiding_active_peer() -> None:
    log = DecisionLog()
    malformed = dataclasses.replace(
        _decision(2),
        workload_created_at=format_workload_timestamp(NOW),
        workload_updated_at=format_workload_timestamp(NOW),
        workload_outcome="private-invalid-outcome",
    )
    # Simulate hostile or legacy in-memory material that bypassed record-time
    # sanitization. Projection must still quarantine each candidate itself.
    log._records.append(malformed)
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    token = _activated(registry, 1)

    result = registry.source_result(
        "node-a", WorkloadQuery(), NOW + timedelta(seconds=1)
    )

    assert len(result.records) == 1
    assert result.records[0].state is WorkloadState.CHECKING
    assert result.status is ResultStatus.PARTIAL
    assert result.error is WorkloadErrorCode.INVALID
    assert "private-invalid-outcome" not in repr(result)
    token.finish()


def test_stale_anonymous_active_counts_have_unknown_omissions() -> None:
    registry = RouterWorkloadRegistry(DecisionLog(), clock=_Clock(), max_active=1)
    represented = _activated(registry, 1)
    anonymous = _activated(registry, 2)
    stale_now = NOW + timedelta(seconds=31)

    for query in (WorkloadQuery(), WorkloadQuery(active_only=True)):
        result = registry.source_result("node-a", query, stale_now)
        assert result.records == ()
        assert result.status is ResultStatus.PARTIAL
        assert result.truncation.omitted is None

    for query in (
        WorkloadQuery(owner=WorkloadOwner.CONTROLLER),
        WorkloadQuery(kind=WorkloadKind.MEDIA_JOB),
        WorkloadQuery(host="node-b"),
        WorkloadQuery(state=WorkloadState.TERMINAL),
    ):
        result = registry.source_result("node-a", query, stale_now)
        assert result.records == ()
        assert result.status is ResultStatus.COMPLETE
        assert result.truncation.omitted == 0
    represented.finish()
    anonymous.finish()


def test_blocked_saturated_finish_never_double_counts_terminal() -> None:
    sink_entered = threading.Event()
    release_sink = threading.Event()

    def sink(_record: DecisionRecord) -> None:
        sink_entered.set()
        assert release_sink.wait(timeout=5)

    registry = RouterWorkloadRegistry(
        DecisionLog(sink=sink), clock=_Clock(), max_active=1
    )
    represented = _activated(registry, 1)
    anonymous = _activated(registry, 2)
    assert anonymous.propose_terminal(_decision(2), WorkloadOutcome.SUCCESS)
    thread = threading.Thread(target=anonymous.finish)
    thread.start()
    assert sink_entered.wait(timeout=5)

    result = registry.source_result(
        "node-a", WorkloadQuery(), NOW + timedelta(seconds=1)
    )
    assert len(result.records) == 2
    assert {record.state for record in result.records} == {
        WorkloadState.CHECKING, WorkloadState.TERMINAL,
    }
    assert result.truncation.omitted is None
    assert registry.unrepresented_count == 0
    assert registry.source_result(
        "node-a",
        WorkloadQuery(state=WorkloadState.ADMITTED),
        NOW + timedelta(seconds=1),
    ).truncation.omitted == 0
    assert registry.source_result(
        "node-a",
        WorkloadQuery(state=WorkloadState.TERMINAL),
        NOW + timedelta(seconds=1),
    ).truncation.omitted is None

    release_sink.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    final = registry.source_result(
        "node-a", WorkloadQuery(), NOW + timedelta(seconds=1)
    )
    assert len(final.records) == 2
    assert final.truncation.omitted == 0
    represented.finish()


def test_finalizing_counter_clears_when_recording_fails() -> None:
    registry = RouterWorkloadRegistry(
        _RaisingDecisionLog(), clock=_Clock(), max_active=1
    )
    represented = _activated(registry, 1)
    anonymous = _activated(registry, 2)
    assert anonymous.propose_terminal(_decision(2), WorkloadOutcome.ERROR)

    assert anonymous.finish()
    assert registry.unrepresented_count == 0
    result = registry.source_result(
        "node-a", WorkloadQuery(), NOW + timedelta(seconds=1)
    )
    assert len(result.records) == 1
    assert result.truncation.omitted == 0
    assert all(count == 0 for count in registry._finalizing.values())
    assert anonymous._entry is None and anonymous._pending is None
    represented.finish()


def test_unexpected_selector_failure_is_fixed_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = RouterWorkloadRegistry(DecisionLog(), clock=_Clock())
    token = _activated(registry)
    secret = "private-selector-detail"

    def explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr(workload_registry, "_select_source_records", explode)
    result = registry.source_result(
        "node-a", WorkloadQuery(), NOW + timedelta(seconds=1)
    )

    assert result.status is ResultStatus.UNAVAILABLE
    assert result.error is WorkloadErrorCode.UNAVAILABLE
    assert result.records == () and result.truncation.omitted is None
    assert secret not in repr(result)
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
    # Public legacy keys are a contract, not every future dataclass member.
    assert set(payload) == {
        "kind", "requested_tier", "attempts", "served_tier",
        "total_prompt_tokens", "total_completion_tokens", "route", "request_id",
        "gateway_request_id", "workbench_run_id", "task_id", "request_bytes",
        "response_bytes", "latency_ms", "readiness_check_ms", "upstream_duration_ms",
        "time_to_first_content_ms", "finish_reason", "prompt_tokens_source",
        "completion_tokens_source", "output_limit_requested", "output_limit_applied",
        "output_limit_clamped", "unix_ts",
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


def _runtime(tmp_path, backend, **kwargs):
    path = tmp_path / "runtime.toml"
    path.write_text('''
[server]
auth_env = "EXAMPLE_ROUTER_TOKEN"
[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:31001/v1"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "EXAMPLE_UPSTREAM_TOKEN"
model = "test-model"
[router.model_routes]
llm.primary = "primary"
''', encoding="utf-8")
    return build_server(str(path), port=0, backends={"primary": backend},
                        env={"EXAMPLE_ROUTER_TOKEN": "synthetic-router-token"},
                        workload_clock=_Clock(), **kwargs)


class _RuntimeBackend:
    def __init__(self, mode="success"):
        self.mode = mode
        self.calls = 0
        self.closed = 0

    def generate(self, request):
        self.calls += 1
        if self.mode == "eager":
            raise RuntimeError("private-eager-detail")
        if self.mode == "eager_timeout":
            raise TimeoutError("private-timeout-detail")
        owner = self

        class Stream:
            index = 0

            def __iter__(self):
                return self

            def __next__(self):
                self.index += 1
                if owner.mode == "timeout":
                    raise urllib.error.URLError(TimeoutError("private-timeout-detail"))
                if owner.mode == "cancelled":
                    raise KeyboardInterrupt("private-cancel-detail")
                if self.index > 1:
                    if owner.mode == "upstream_error":
                        raise RuntimeError("private-upstream-detail")
                    raise StopIteration
                return "private-response"

            def close(self):
                owner.closed += 1
        return Stream()


class _DeliveryWriter(io.BytesIO):
    def __init__(self, server, failure=None, stream=False, require_deferred=True):
        super().__init__()
        self.server = server
        self.failure = failure
        self.stream = stream
        self.writes = 0
        self.require_deferred = require_deferred

    def write(self, data):
        self.writes += 1
        if self.require_deferred and self.server.anvil_workloads is not None:
            assert len(self.server.anvil_routing._decision_log) == 0
        if self.failure == "headers" or (self.failure == "body" and self.writes > 1):
            raise BrokenPipeError("private-socket-detail")
        return super().write(data)

    def flush(self):
        if self.require_deferred and self.server.anvil_workloads is not None:
            assert len(self.server.anvil_routing._decision_log) == 0
        if self.failure == "flush" and (
            not self.stream or self.getvalue().endswith(b"0\r\n\r\n")
        ):
            raise ConnectionResetError("private-flush-detail")


def _post(server, *, stream=False, failure=None, model="llm.primary", auth=True,
          content="private-prompt", require_deferred=True):
    # Exercise the real handler assembled by build_server, with deterministic
    # socket-write/flush failures rather than timing-dependent TCP resets.
    handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
    handler.server = server
    handler.path = "/v1/chat/completions"
    handler.command = "POST"
    handler.requestline = "POST /v1/chat/completions HTTP/1.1"
    handler.request_version = "HTTP/1.1"
    handler.close_connection = True
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": content}],
                       "stream": stream, "request_id": "caller-private-id"}).encode()
    handler.headers = Headers()
    handler.headers["Content-Length"] = str(len(body))
    handler.headers["Content-Type"] = "application/json"
    handler.headers["X-Request-Id"] = "caller-private-id"
    if auth:
        handler.headers["Authorization"] = "Bearer synthetic-router-token"
    handler.rfile = io.BytesIO(body)
    handler.wfile = _DeliveryWriter(server, failure, stream, require_deferred)
    handler.do_POST()
    return handler.wfile.getvalue()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("mode,outcome", [
    ("success", "success"), ("eager", "error"), ("eager_timeout", "timeout"),
    ("timeout", "timeout"), ("upstream_error", "error"),
])
def test_runtime_commits_one_terminal_only_after_delivery(tmp_path, mode, outcome, stream):
    backend = _RuntimeBackend(mode)
    server = _runtime(tmp_path, backend)
    try:
        raw = _post(server, stream=stream)
        assert b"HTTP/1.1 " in raw
        registry = server.anvil_workloads
        assert registry is server.anvil_routing._workload_registry
        assert registry._decision_log is server.anvil_routing._decision_log
        assert registry.active_count == registry.unrepresented_count == 0
        log = registry._decision_log
        assert len(log) == 1
        assert log.last.workload_outcome == outcome
        assert log.last.gateway_request_id.startswith("req_")
        assert log.last.gateway_request_id != "caller-private-id"
        projected = registry.source_result("node-a", WorkloadQuery(), NOW + timedelta(seconds=1))
        assert len(projected.records) == 1
        assert projected.records[0].outcome.value == outcome
        assert "private-" not in repr(projected)
        assert backend.closed == (0 if mode.startswith("eager") else 1)
        assert server.anvil_routing._admission.snapshot("primary").active_requests == 0
    finally:
        server.server_close()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["headers", "body", "flush"])
def test_delivery_failure_overrides_backend_success(tmp_path, stream, failure):
    backend = _RuntimeBackend()
    server = _runtime(tmp_path, backend)
    try:
        with pytest.raises(OSError):
            _post(server, stream=stream, failure=failure)
        log = server.anvil_routing._decision_log
        assert len(log) == 1
        assert log.last.workload_outcome == "disconnected"
        assert server.anvil_workloads.active_count == 0
        assert backend.closed == 1
        assert server.anvil_routing._admission.snapshot("primary").active_requests == 0
    finally:
        server.server_close()


@pytest.mark.parametrize("mode", ["auth", "unknown", "context"])
def test_no_workload_before_auth_and_alias_but_context_rejection_is_terminal(tmp_path, mode):
    backend = _RuntimeBackend()
    server = _runtime(tmp_path, backend)
    try:
        _post(server, auth=mode != "auth", model="missing" if mode == "unknown" else "llm.primary",
              content="x" * 20000 if mode == "context" else "private-prompt")
        log = server.anvil_routing._decision_log
        assert backend.calls == 0
        assert len(log) == (1 if mode == "context" else 0)
        if mode == "context":
            assert log.last.workload_outcome == "rejected"
        assert server.anvil_workloads.active_count == 0
    finally:
        server.server_close()


def test_actual_phase_boundaries_observe_blocking_work_without_holding_registry_lock(tmp_path):
    readiness_entered, readiness_release = threading.Event(), threading.Event()
    backend_entered, backend_release = threading.Event(), threading.Event()
    iteration_entered, iteration_release = threading.Event(), threading.Event()

    class Readiness:
        def check(self, tier):
            readiness_entered.set()
            assert readiness_release.wait(5)
            return AvailabilityResult(True, "ready", "ready")

    class Backend:
        def generate(self, request):
            backend_entered.set()
            assert backend_release.wait(5)

            def empty():
                iteration_entered.set()
                assert iteration_release.wait(5)
                yield from ()  # tool-only/empty still reaches the streaming phase
            return empty()

    server = _runtime(tmp_path, Backend(), availability=Readiness())
    routing, registry = server.anvil_routing, server.anvil_workloads
    request = InternalRequest(model="llm.primary", messages=[Message("user", "private-prompt")])
    stream = routing.generate_tracked(request, gateway_request_id=_gateway(7))
    errors = []

    def run(action):
        try:
            action()
        except BaseException as exc:
            errors.append(type(exc))

    def state():
        return registry.source_result("node-a", WorkloadQuery(), NOW + timedelta(seconds=1)).records[0].state

    first = threading.Thread(target=lambda: run(stream.start))
    second = threading.Thread(target=lambda: run(lambda: list(stream)))
    try:
        first.start()
        assert readiness_entered.wait(5)
        assert state() is WorkloadState.CHECKING
        readiness_release.set()
        assert backend_entered.wait(5)
        assert state() is WorkloadState.ADMITTED
        backend_release.set()
        first.join(5)
        assert not first.is_alive()
        assert state() is WorkloadState.DISPATCHED
        second.start()
        assert iteration_entered.wait(5)
        assert state() is WorkloadState.STREAMING
        iteration_release.set()
        second.join(5)
        assert not second.is_alive()
        assert len(routing._decision_log) == 0
        stream.finish_delivery()
        assert routing._decision_log.last.workload_outcome == "success"
        assert registry.active_count == 0
        assert errors == []
    finally:
        readiness_release.set()
        backend_release.set()
        iteration_release.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)
        stream.close()
        server.server_close()


@pytest.mark.parametrize("started", [False, True])
def test_tracked_close_before_iteration_is_idempotent(tmp_path, started):
    backend = _RuntimeBackend()
    server = _runtime(tmp_path, backend)
    try:
        request = InternalRequest(model="llm.primary", messages=[Message("user", "private-prompt")])
        stream = server.anvil_routing.generate_tracked(request, gateway_request_id=_gateway(8))
        if started:
            stream.start()
        stream.close()
        stream.close()
        assert server.anvil_workloads.active_count == 0
        assert len(server.anvil_routing._decision_log) == int(started)
        assert backend.closed == int(started)
        if started:
            assert server.anvil_routing._decision_log.last.workload_outcome == "disconnected"
    finally:
        server.server_close()


def test_default_transport_preserves_safe_timeout_classification(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError(TimeoutError("private-transport-detail"))

    monkeypatch.setattr("anvil_serving.router.backends.relay._direct_open", fail)
    with pytest.raises(RelayTimeoutError) as caught:
        _urlopen_transport("http://127.0.0.1/v1", data=b"", headers={}, timeout=1)
    assert str(caught.value) == "model upstream request timed out"
    assert "private-" not in str(caught.value)


@pytest.mark.parametrize("stream", [False, True])
def test_runtime_cancellation_preserves_outcome_and_releases_admission(tmp_path, stream):
    backend = _RuntimeBackend("cancelled")
    server = _runtime(tmp_path, backend)
    try:
        with pytest.raises(KeyboardInterrupt):
            _post(server, stream=stream)
        assert server.anvil_routing._decision_log.last.workload_outcome == "cancelled"
        assert len(server.anvil_routing._decision_log) == 1
        assert server.anvil_workloads.active_count == 0
        assert backend.closed == 1
        assert server.anvil_routing._admission.snapshot("primary").active_requests == 0
    finally:
        server.server_close()


@pytest.mark.parametrize("failure", ["malformed", "http", "timeout"])
def test_real_relay_failures_have_one_terminal_workload(tmp_path, failure):
    transport = FakeStreamTransport(b"data: {not-json}\n\n")

    def failing_transport(*args, **kwargs):
        if failure == "http":
            raise urllib.error.HTTPError("http://127.0.0.1/v1", 503, "private-upstream", {}, None)
        raise urllib.error.URLError(TimeoutError("private-timeout"))

    backend = RelayBackend(make_tier("openai"), env={"EXAMPLE_KEY": "synthetic"},
                           stream_transport=transport if failure == "malformed" else failing_transport)
    server = _runtime(tmp_path, backend)
    try:
        _post(server, stream=True)
        log = server.anvil_routing._decision_log
        assert len(log) == 1
        assert log.last.workload_outcome == ("timeout" if failure == "timeout" else "error")
        assert server.anvil_workloads.active_count == 0
        assert server.anvil_routing._admission.snapshot("primary").active_requests == 0
        if failure == "malformed":
            assert transport.response.closed
        projected = server.anvil_workloads.source_result("node-a", WorkloadQuery(), NOW + timedelta(seconds=1))
        assert "private-" not in repr(projected)
    finally:
        server.server_close()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failure", ["disabled", "begin", "clock"])
def test_observation_failures_preserve_response_and_ordinary_decision(tmp_path, monkeypatch, stream, failure):
    backend = _RuntimeBackend()
    server = _runtime(tmp_path, backend, observe_workloads=failure != "disabled")

    def fail(*args, **kwargs):
        raise RuntimeError("private-observation-detail")

    if failure == "begin":
        monkeypatch.setattr(server.anvil_workloads, "begin", fail)
    if failure == "clock":
        monkeypatch.setattr(server.anvil_workloads, "_clock", fail)
    try:
        raw = _post(server, stream=stream, require_deferred=False)
        assert b"HTTP/1.1 200" in raw
        assert b"private-response" in raw
        assert b"private-observation" not in raw
        log = server.anvil_routing._decision_log
        assert len(log) == 1
        assert log.last.workload_outcome is None
        assert backend.closed == 1
        assert server.anvil_routing._admission.snapshot("primary").active_requests == 0
        if server.anvil_workloads is not None:
            assert server.anvil_workloads.active_count == server.anvil_workloads.unrepresented_count == 0
    finally:
        server.server_close()


@pytest.mark.parametrize("stream", [False, True])
def test_saturated_runtime_still_delivers_and_clears_anonymous_count(tmp_path, stream):
    backend = _RuntimeBackend()
    server = _runtime(tmp_path, backend)
    registry = server.anvil_workloads
    registry._max_active = 1
    held = server.anvil_routing.generate_tracked(
        InternalRequest(model="llm.primary", messages=[Message("user", "held")]),
        gateway_request_id=_gateway(9),
    )
    try:
        held.start()
        assert registry.active_count == 1
        raw = _post(server, stream=stream)
        assert b"HTTP/1.1 200" in raw and b"private-response" in raw
        assert registry.active_count == 1
        assert registry.unrepresented_count == 0
        assert len(server.anvil_routing._decision_log) == 1
        assert server.anvil_routing._decision_log.last.workload_outcome == "success"
        assert list(held) == ["private-response"]
        held.finish_delivery()
        assert len(server.anvil_routing._decision_log) == 2
        assert registry.active_count == registry.unrepresented_count == 0
        assert backend.closed == 2
        assert server.anvil_routing._admission.snapshot("primary").active_requests == 0
    finally:
        held.close()
        server.server_close()


def test_tracked_close_after_first_delta_clears_once(tmp_path):
    backend = _RuntimeBackend()
    server = _runtime(tmp_path, backend)
    try:
        stream = server.anvil_routing.generate_tracked(
            InternalRequest(model="llm.primary", messages=[Message("user", "held")]),
            gateway_request_id=_gateway(10),
        )
        assert next(stream) == "private-response"
        stream.close()
        stream.close()
        assert server.anvil_workloads.active_count == 0
        assert len(server.anvil_routing._decision_log) == 1
        assert server.anvil_routing._decision_log.last.workload_outcome == "disconnected"
        assert backend.closed == 1
        assert server.anvil_routing._admission.snapshot("primary").active_requests == 0
    finally:
        server.server_close()
