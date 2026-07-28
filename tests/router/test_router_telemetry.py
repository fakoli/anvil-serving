"""Unit coverage for pure, metadata-only router telemetry helpers."""
from __future__ import annotations

import pytest

from anvil_serving.router.decision_log import AttemptRecord, DecisionRecord
from anvil_serving.router.router_telemetry import (
    aggregate_stats,
    find_request,
    render_capacity_prometheus,
    render_prometheus,
)


def _record(
    *,
    route: str = "llm.primary",
    served: bool = True,
    request_id: str | None = None,
    prompt: int = 3,
    completion: int = 2,
    request_bytes: int = 10,
    response_bytes: int = 20,
    latency_ms: int = 0,
) -> DecisionRecord:
    return DecisionRecord(
        kind="chat",
        requested_tier="primary-local",
        attempts=(AttemptRecord("primary-local", served, "served", prompt, completion, "served"),),
        served_tier="primary-local" if served else None,
        total_prompt_tokens=prompt,
        total_completion_tokens=completion,
        route=route,
        request_id=request_id,
        request_bytes=request_bytes,
        response_bytes=response_bytes,
        latency_ms=latency_ms,
    )


def test_aggregate_stats_reports_only_the_current_buffer_and_exact_percentiles():
    records = [
        _record(latency_ms=0),
        _record(latency_ms=10),
        _record(latency_ms=20, served=False),
        _record(latency_ms=30),
        _record(latency_ms=40),
    ]

    stats = aggregate_stats(records)

    assert stats["scope"] == "current_decision_log_buffer"
    assert stats["available"] == 5
    assert stats["requests"] == {"total": 5, "succeeded": 4, "failed": 1}
    assert stats["tokens"] == {"prompt": 15, "completion": 10, "total": 25}
    assert stats["bytes"] == {"request": 50, "response": 100, "total": 150}
    assert stats["latency_ms"] == {"samples": 4, "average": 25.0, "p50": 20, "p95": 40}
    assert stats["routes"]["llm.primary"]["requests"]["total"] == 5


def test_aggregate_stats_filters_by_normalized_alias_then_limits_recent_matches():
    records = [
        _record(route="llm.primary", latency_ms=10),
        _record(route="llm.voice", latency_ms=20),
        _record(route="llm.primary", latency_ms=30),
    ]

    stats = aggregate_stats(records, {"model": [" LLM.PRIMARY "], "limit": ["1"]})

    assert stats["model"] == "llm.primary"
    assert stats["matching"] == 2
    assert stats["count"] == 1
    assert stats["latency_ms"]["p50"] == 30
    assert set(stats["routes"]) == {"llm.primary"}


@pytest.mark.parametrize(
    "query",
    [
        {"window": ["15m"]},
        {"model": ["one", "two"]},
        {"limit": ["0"]},
        {"limit": ["not-a-number"]},
    ],
)
def test_aggregate_stats_rejects_unsupported_or_invalid_queries(query):
    with pytest.raises(ValueError):
        aggregate_stats((), query)


def test_find_request_returns_safe_summary_and_not_found_for_invalid_or_missing_ids():
    records = [
        _record(request_id="req_1", route="unsafe\nroute"),
        _record(request_id="req_1", route="llm.primary", latency_ms=12),
    ]

    trace = find_request(records, "req_1")

    assert trace["scope"] == "current_decision_log_buffer"
    assert trace["record"]["route"] == "llm.primary"
    assert trace["record"]["request_id"] == "req_1"
    with pytest.raises(KeyError):
        find_request(records, "bad id")
    with pytest.raises(KeyError):
        find_request(records, "req_missing")


def test_prometheus_is_gauge_only_and_escapes_safe_model_labels():
    payload = render_prometheus((_record(route='model"\\name', latency_ms=15),))

    assert "# TYPE anvil_router_decision_buffer_requests gauge" in payload
    assert "anvil_router_decision_buffer_requests{model=\"\",outcome=\"succeeded\"} 1" in payload
    assert 'model="model\\"\\\\name"' in payload
    assert "# TYPE anvil_router_decision_buffer_latency_ms_p95 gauge" in payload
    assert "anvil_router_decision_buffer_latency_ms_p95{model=\"\"} 15" in payload
    assert "unsafe\\nroute" not in payload


def test_capacity_prometheus_renders_only_present_numeric_gauges():
    payload = render_capacity_prometheus({
        "data": [{
            "id": "primary-local",
            "aliases": ["llm.primary"],
            "loaded": True,
            "capacity": {
                "context_limit_tokens": 262_144,
                "kv_cache_capacity_tokens": 571_950,
            },
            "multimodal": {"image_limit": 1},
            "live": {
                "requests_running": 1.0,
                "requests_waiting": 0.0,
                "kv_cache_usage_fraction": 0.25,
            },
        }],
    })
    assert 'anvil_router_model_loaded{model="llm.primary",tier="primary-local"} 1' in payload
    assert "anvil_router_model_context_limit_tokens" in payload
    assert "anvil_router_model_kv_cache_usage_fraction" in payload
