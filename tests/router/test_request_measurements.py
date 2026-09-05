"""Terminal, content-free request measurements for direct router relays."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.config import load
from anvil_serving.router.decision_log import AttemptRecord, DecisionRecord
from anvil_serving.router.internal import InternalRequest, Message, StructuredResult
from anvil_serving.router.router_telemetry import aggregate_stats, find_request, render_prometheus
from anvil_serving.router.serve import RoutingBackend


_CONFIG = Path(__file__).resolve().parents[2] / "configs" / "example.toml"


class _Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def _request(*, raw=None, max_tokens=None):
    return InternalRequest(
        model="llm.primary",
        messages=(Message("user", "private prompt must not enter telemetry"),),
        raw={} if raw is None else raw,
        max_tokens=max_tokens,
    )


def _routing(backend, *, max_output_tokens=None):
    config = load(_CONFIG)
    if max_output_tokens is not None:
        config = replace(
            config,
            tiers=tuple(
                replace(tier, max_output_tokens=max_output_tokens)
                if tier.id == "primary-local" else tier
                for tier in config.tiers
            ),
        )
    return RoutingBackend(config, {"primary-local": backend})


def test_terminal_measurements_capture_delayed_content_and_real_usage(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr("anvil_serving.router.serve.time.monotonic", clock)

    class Backend:
        def generate(self, request):
            yield ""  # role/protocol framing is not content
            clock.now = 0.120
            yield "private completion fragment"
            clock.now = 0.180

        @staticmethod
        def get_last_structured():
            return StructuredResult(
                finish_reason="end_turn",
                usage={"input_tokens": 11, "output_tokens": 7},
            )

    routing = _routing(Backend())
    assert list(routing.generate(_request())) == ["", "private completion fragment"]

    record = routing._decision_log.last
    assert record is not None
    assert record.latency_ms == 180
    assert record.readiness_check_ms == 0
    assert record.upstream_duration_ms == 180
    assert record.time_to_first_content_ms == 120
    assert record.finish_reason == "stop"
    assert record.total_prompt_tokens == 11
    assert record.total_completion_tokens == 7
    assert record.prompt_tokens_source == "upstream"
    assert record.completion_tokens_source == "upstream"
    assert "private prompt" not in repr(record)
    assert "private completion" not in repr(record)

    stats = aggregate_stats(routing._decision_log.records)
    assert stats["time_to_first_content_ms"] == {
        "samples": 1, "average": 120.0, "p50": 120, "p95": 120,
    }
    assert stats["finish_reason_counts"] == {
        "stop": 1, "length": 0, "tool_calls": 0,
        "content_filter": 0, "unknown": 0,
    }
    assert "time_to_first_content_ms" in render_prometheus(routing._decision_log.records)


def test_tool_only_stream_has_no_content_ttft_and_estimates_only_missing_usage():
    class Backend:
        def generate(self, request):
            if False:
                yield "unreachable"

        @staticmethod
        def get_last_structured():
            return StructuredResult(
                finish_reason="tool_use",
                tool_calls=[{
                    "name": "private_tool_name",
                    "arguments": {"secret": "private tool result"},
                }],
                usage={"input_tokens": 13},
            )

    routing = _routing(Backend())
    assert list(routing.generate(_request())) == []

    record = routing._decision_log.last
    assert record is not None
    assert record.time_to_first_content_ms is None
    assert record.finish_reason == "tool_calls"
    assert record.total_prompt_tokens == 13
    assert record.prompt_tokens_source == "upstream"
    assert record.total_completion_tokens == 0
    assert record.completion_tokens_source == "estimated"
    assert "private_tool_name" not in repr(record)
    assert "private tool result" not in repr(record)


def test_unknown_finish_is_null_and_upstream_failure_has_no_fake_terminal_values(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr("anvil_serving.router.serve.time.monotonic", clock)

    class UnknownBackend:
        def generate(self, request):
            yield "x"

        @staticmethod
        def get_last_structured():
            return StructuredResult(finish_reason="provider_secret_reason")

    unknown = _routing(UnknownBackend())
    assert list(unknown.generate(_request())) == ["x"]
    unknown_record = unknown._decision_log.last
    assert unknown_record is not None
    assert unknown_record.finish_reason is None
    assert "provider_secret_reason" not in repr(unknown_record)

    class FailingBackend:
        @staticmethod
        def generate(request):
            clock.now = 0.040
            raise RuntimeError("https://private.example.test/error")

    failed = _routing(FailingBackend())
    with pytest.raises(RuntimeError):
        failed.generate(_request())
    failure = failed._decision_log.last
    assert failure is not None
    assert failure.served_tier is None
    assert failure.upstream_duration_ms == 40
    assert failure.time_to_first_content_ms is None
    assert failure.finish_reason is None
    assert failure.completion_tokens_source == "unknown"
    assert "private.example.test" not in repr(failure)


def test_close_before_start_records_one_terminal_cancellation():
    class Backend:
        def generate(self, request):
            yield "not consumed"

    routing = _routing(Backend())
    stream = routing.generate(_request())
    stream.close()

    assert len(routing._decision_log.records) == 1
    record = routing._decision_log.last
    assert record is not None
    assert record.attempts[0].reason == "client_disconnected"
    assert record.served_tier is None
    assert record.time_to_first_content_ms is None
    assert record.upstream_duration_ms is None
    assert record.completion_tokens_source == "unknown"


def test_upstream_duration_includes_eager_setup_after_dispatch(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr("anvil_serving.router.serve.time.monotonic", clock)

    class EagerBackend:
        @staticmethod
        def generate(request):
            clock.now = 0.030
            return iter(("x",))

    routing = _routing(EagerBackend())
    assert list(routing.generate(_request())) == ["x"]
    record = routing._decision_log.last
    assert record is not None
    assert record.upstream_duration_ms == 30


def test_failure_and_cancellation_after_content_keep_first_content_measurement(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr("anvil_serving.router.serve.time.monotonic", clock)

    class FailingBackend:
        def generate(self, request):
            clock.now = 0.020
            yield "content"
            clock.now = 0.050
            raise RuntimeError("private upstream detail")

    failed = _routing(FailingBackend())
    with pytest.raises(RuntimeError):
        list(failed.generate(_request()))
    failure = failed._decision_log.last
    assert failure is not None
    assert failure.time_to_first_content_ms == 20
    assert failure.upstream_duration_ms == 50
    assert failure.total_completion_tokens > 0
    assert failure.completion_tokens_source == "estimated"
    assert aggregate_stats(failed._decision_log.records)["usage_sources"]["completion"] == {
        "upstream": 0, "estimated": 1, "unknown": 0,
    }

    clock.now = 0.0

    class StreamingBackend:
        def generate(self, request):
            clock.now = 0.015
            yield "content"

    cancelled = _routing(StreamingBackend())
    stream = cancelled.generate(_request())
    assert next(stream) == "content"
    stream.close()
    cancellation = cancelled._decision_log.last
    assert cancellation is not None
    assert cancellation.time_to_first_content_ms == 15
    assert cancellation.upstream_duration_ms == 15
    assert cancellation.total_completion_tokens > 0
    assert cancellation.completion_tokens_source == "estimated"


def test_safe_projection_rejects_malformed_optional_measurements():
    record = DecisionRecord(
        kind="chat", requested_tier="primary-local",
        attempts=(AttemptRecord("primary-local", True, "served", 1, 1, "served"),),
        served_tier="primary-local", total_prompt_tokens=1,
        total_completion_tokens=1, route="llm.primary",
        readiness_check_ms=float("inf"), upstream_duration_ms="3.5",
        time_to_first_content_ms=1_000_000_000_000_001,
        finish_reason=["raw provider finish"], prompt_tokens_source={},
        completion_tokens_source=["upstream"],
    )
    summary = find_request((replace(record, request_id="legacy_1"),), "legacy_1")["record"]

    assert summary["measurements"] == {
        "readiness_check_ms": None,
        "upstream_duration_ms": None,
        "time_to_first_content_ms": None,
        "finish_reason": None,
    }
    assert summary["usage"]["prompt_source"] is None
    assert summary["usage"]["completion_source"] is None
    counts = aggregate_stats((record,))["finish_reason_counts"]
    assert counts == {
        "stop": 0, "length": 0, "tool_calls": 0,
        "content_filter": 0, "unknown": 1,
    }


def test_output_limit_projection_and_generated_id_lookup_are_bounded():
    class Backend:
        def generate(self, request):
            yield "ok"

    request = _request(raw={"max_tokens": 64}, max_tokens=64)
    routing = _routing(Backend(), max_output_tokens=32)
    assert list(routing.generate(request)) == ["ok"]
    record = routing._decision_log.last
    assert record is not None
    assert (record.output_limit_requested, record.output_limit_applied, record.output_limit_clamped) == (64, 32, True)

    gateway_id = "req_0123456789abcdef0123456789abcdef"
    caller_id = "req_ffffffffffffffffffffffffffffffff"
    log_record = DecisionRecord(
        kind="chat", requested_tier="primary-local",
        attempts=(AttemptRecord("primary-local", True, "served", 1, 1, "served"),),
        served_tier="primary-local", total_prompt_tokens=1,
        total_completion_tokens=1, route="llm.primary",
        request_id=caller_id, gateway_request_id=gateway_id,
    )
    trace = find_request((log_record,), gateway_id)
    assert trace["record"]["gateway_request_id"] == gateway_id
    assert trace["record"]["request_id"] == caller_id
    with pytest.raises(KeyError):
        find_request((log_record,), caller_id)


def test_cancellation_before_first_next_releases_concurrency_and_eager_resource():
    closed = []

    class Stream:
        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def close(self):
            closed.append(True)

    class Backend:
        def generate(self, request):
            return Stream()

    config = load(_CONFIG)
    config = replace(config, tiers=tuple(replace(t, max_concurrency=1) for t in config.tiers))
    routing = RoutingBackend(config, {"primary-local": Backend()})
    stream = routing.generate(_request())
    limiter = routing._backends["primary-local"]
    assert not limiter._sem.acquire(blocking=False)
    stream.close()
    stream.close()
    assert closed == [True]
    assert limiter._sem.acquire(blocking=False)
    limiter._sem.release()
    assert list(routing.generate(_request())) == []
    assert routing._admission.snapshot("primary-local").active_requests == 0
    assert len(routing._decision_log.records) == 2


def test_terminal_metadata_failure_records_once_and_releases_admission():
    class Backend:
        def generate(self, request):
            yield "content"

        def get_last_structured(self):
            raise RuntimeError("PRIVATE-METADATA-ERROR")

    routing = _routing(Backend())
    stream = routing.generate(_request())
    with pytest.raises(RuntimeError):
        list(stream)
    stream.close()
    assert routing._admission.snapshot("primary-local").active_requests == 0
    assert len(routing._decision_log.records) == 1
    record = routing._decision_log.last
    assert record.attempts[0].reason == "completion_error_RuntimeError"
    assert record.served_tier is None
    assert record.time_to_first_content_ms is not None
    assert "PRIVATE-METADATA" not in repr(record)


def test_invalid_backend_delta_fails_with_one_terminal_record_and_releases_limiter():
    class Backend:
        def generate(self, request):
            yield "observed text"
            yield {"content": "PRIVATE-MALFORMED-DELTA"}

    config = load(_CONFIG)
    config = replace(config, tiers=tuple(replace(t, max_concurrency=1) for t in config.tiers))
    routing = RoutingBackend(config, {"primary-local": Backend()})
    stream = routing.generate(_request())
    with pytest.raises(TypeError, match="backend must yield text fragments"):
        list(stream)
    stream.close()
    assert len(routing._decision_log.records) == 1
    record = routing._decision_log.last
    assert record.attempts[0].reason == "backend_error_TypeError"
    assert record.total_completion_tokens > 0
    assert "PRIVATE" not in repr(record)
    assert routing._admission.snapshot("primary-local").active_requests == 0
    semaphore = routing._backends["primary-local"]._sem
    assert semaphore.acquire(blocking=False)
    semaphore.release()


@pytest.mark.parametrize("cap", [None, 16])
def test_caller_cannot_forge_output_clamp(cap):
    class Backend:
        def generate(self, request):
            yield "content"

    routing = _routing(Backend(), max_output_tokens=cap)
    request = _request(max_tokens=8, raw={
        "max_tokens": 8,
        "_anvil_output_clamp": {"requested": 999999, "applied": 1},
    })
    assert list(routing.generate(request)) == ["content"]
    assert "_anvil_output_clamp" not in request.raw
    record = routing._decision_log.last
    assert record.attempts[0].reason == "served"
    assert (record.output_limit_requested, record.output_limit_applied, record.output_limit_clamped) == (8, 8, False)


def test_real_streaming_relay_keeps_usage_reported_in_separate_events():
    import io
    import json
    from anvil_serving.router.backends.relay import RelayBackend

    class Response(io.BytesIO):
        headers = {"Content-Type": "text/event-stream"}

    events = [
        {"choices": [{"delta": {"content": "ok"}}], "usage": {"prompt_tokens": 11}},
        {"choices": [], "usage": {"completion_tokens": 7}},
        {"choices": [], "usage": {"prompt_tokens": True, "completion_tokens": -1, "prompt_tokens_details": {"cached_tokens": 3}}},
    ]
    wire = "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"
    config = load(_CONFIG)
    relay = RelayBackend(config.tier("primary-local"), env={}, stream_transport=lambda *a, **k: Response(wire.encode()))
    routing = RoutingBackend(config, {"primary-local": relay})
    request = _request()
    request.stream = True
    assert list(routing.generate(request)) == ["ok"]
    record = routing._decision_log.last
    assert (record.total_prompt_tokens, record.total_completion_tokens) == (11, 7)
    assert record.prompt_tokens_source == record.completion_tokens_source == "upstream"
    assert relay.get_last_structured().usage["cache_read_input_tokens"] == 3
