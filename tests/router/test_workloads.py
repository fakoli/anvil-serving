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
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadState,
    format_workload_timestamp,
)
from anvil_serving.router.decision_log import (
    AttemptRecord,
    DecisionLog,
    DecisionLogWriter,
    DecisionRecord,
)
from anvil_serving.router_diagnostics import diagnose_record
from anvil_serving.router.workloads import RouterWorkloadRegistry
from anvil_serving.router.serve import build_server
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.backends.relay import RelayTimeoutError, _urlopen_transport

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


def test_registry_cap_and_concurrent_recent_reads_remain_bounded() -> None:
    log = DecisionLog(max_records=None)
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    tokens = [_activated(registry, index) for index in range(1, 1026)]
    assert registry.active_count == 1024
    assert registry.unrepresented_count == 1
    for index in range(513):
        encoded = format_workload_timestamp(NOW)
        log.record(dataclasses.replace(
            _decision(index + 2000),
            workload_created_at=encoded,
            workload_updated_at=encoded,
            workload_outcome=WorkloadOutcome.SUCCESS.value,
        ))

    start = threading.Barrier(3)
    failures: list[BaseException] = []

    def read() -> None:
        try:
            start.wait()
            for _ in range(2):
                result = registry.source_result(
                    "node-a", WorkloadQuery(), NOW + timedelta(seconds=1)
                )
                assert len(result.records) <= 200
                assert len(log.recent()) == 512
        except BaseException as exc:
            failures.append(exc)

    readers = [threading.Thread(target=read) for _ in range(2)]
    for reader in readers:
        reader.start()
    start.wait()
    for reader in readers:
        # This verifies bounded results and concurrency, not five-second throughput.
        reader.join(timeout=30)
        assert not reader.is_alive()
    assert failures == []
    for token in tokens:
        token.finish()
    assert registry.active_count == registry.unrepresented_count == 0


def test_terminal_replaces_caller_identity_before_memory_and_jsonl_sink(tmp_path) -> None:
    path = tmp_path / "decisions.jsonl"
    log = DecisionLog(sink=DecisionLogWriter(path))
    registry = RouterWorkloadRegistry(log, clock=_Clock())
    token = _activated(registry, 9)
    caller_marker = "caller-request-private-marker"
    decision = dataclasses.replace(_decision(9), gateway_request_id=caller_marker)
    assert token.propose_terminal(decision, WorkloadOutcome.REJECTED)
    assert token.finish()

    assert log.last is not None
    rendered = repr(log.last) + path.read_text(encoding="utf-8")
    assert caller_marker not in rendered
    assert log.last.gateway_request_id == _gateway(9)
    assert log.last.workload_outcome == WorkloadOutcome.REJECTED.value


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
        if (
            self.failure == "headers"
            or (self.failure == "body" and self.writes > 1)
            or (self.failure == "timeout_once" and self.writes == 2)
        ):
            if self.failure == "timeout_once":
                raise TimeoutError("private-write-timeout")
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


def test_stream_delivery_timeout_overrides_success_with_timeout_terminal(tmp_path):
    backend = _RuntimeBackend()
    server = _runtime(tmp_path, backend)
    try:
        raw = _post(server, stream=True, failure="timeout_once")
        assert b"HTTP/1.1 " in raw
        log = server.anvil_routing._decision_log
        assert len(log) == 1
        assert log.last.workload_outcome == "timeout"
        diagnosis = diagnose_record(log.summary()["records"][0])
        assert diagnosis["outcome"] == diagnosis["delivery_outcome"] == "timeout"
        assert server.anvil_workloads.active_count == 0
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
