"""Terminal, content-free request measurements for direct router relays."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from anvil_serving.router.config import load
from anvil_serving.router.internal import InternalRequest, Message
from anvil_serving.router.router_telemetry import aggregate_stats
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
