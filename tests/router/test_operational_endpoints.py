"""HTTP contracts for router metadata, telemetry, trace, and metrics endpoints."""
from __future__ import annotations

import json

from anvil_serving.router.availability import AvailabilityResult
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.decision_log import AttemptRecord, DecisionRecord
from anvil_serving.router.front_door import (
    MODEL_CAPABILITIES_ENDPOINT,
    MODEL_FINGERPRINTS_ENDPOINT,
    PROMETHEUS_ENDPOINT,
    REQUEST_TRACE_PREFIX,
    ROUTER_STATS_ENDPOINT,
    ROUTER_STATUS_ENDPOINT,
)
from anvil_serving.router.model_capacity import MetricsSnapshot
from anvil_serving.router.serve import RoutingBackend
from tests.router.helpers import StaticBackend
from tests.router.helpers import http_get as _http_get
from tests.router.helpers import server_context

TOKEN = "router-operations-token"


class _Availability:
    def check(self, tier):
        return AvailabilityResult(
            True,
            "ready",
            "identity_passed",
            expected_model=tier.model,
            observed_model=tier.model,
        )


def _config() -> RouterConfig:
    tier = Tier(
        id="primary-local",
        base_url="http://127.0.0.1:30002/v1",
        model="qwen35-122b-a10b-nvfp4",
        dialect="openai",
        context_limit=262_144,
        privacy="local",
        tool_support=True,
        auth_env="ANVIL_TEST_KEY",
        engine="vllm",
        quantization="nvfp4",
        model_identity=True,
        max_concurrency=1,
        params={
            "capacity": {
                "kv_cache_capacity_tokens": 571_950,
                "image_limit": 1,
                "video_limit": 0,
            },
            "capabilities": {
                "modalities": ["text", "image"],
                "thinking": {
                    "supported": True,
                    "default": "enabled",
                    "caller_override": True,
                },
                "images_per_request": 1,
                "video_per_request": 0,
            },
            "fingerprint": {
                "model_revision": "98915d837c4e7c87ac8296d02e89de19b3207e6d",
                "engine_version": "vllm-0.22.1-7b9cb5b7.dev-ngc26.06",
            },
        },
    )
    return RouterConfig(
        tiers=(tier,),
        model_routes={"llm.primary": "primary-local"},
    )


def _metrics(_tier):
    return MetricsSnapshot(
        "available",
        {
            "requests_running": 1.0,
            "requests_waiting": 0.0,
            "kv_cache_usage_fraction": 0.25,
        },
    )


def _routing() -> RoutingBackend:
    routing = RoutingBackend(
        _config(),
        {"primary-local": StaticBackend(["ok"])},
        availability=_Availability(),
        capacity_metrics=_metrics,
    )
    routing._decision_log.record(DecisionRecord(
        kind="chat",
        requested_tier="primary-local",
        attempts=(AttemptRecord(
            "primary-local", True, "served", 100, 20, "served"
        ),),
        served_tier="primary-local",
        total_prompt_tokens=100,
        total_completion_tokens=20,
        route="llm.primary",
        request_id="req_123",
        latency_ms=50,
    ))
    return routing


def _server():
    return server_context(_routing(), token=TOKEN)


def _get(host, port, path, *, token=TOKEN):
    return _http_get(host, port, path, token=token)


def test_metadata_status_stats_trace_and_metrics_are_authenticated():
    paths = (
        MODEL_CAPABILITIES_ENDPOINT,
        MODEL_FINGERPRINTS_ENDPOINT,
        ROUTER_STATUS_ENDPOINT,
        ROUTER_STATS_ENDPOINT,
        REQUEST_TRACE_PREFIX + "req_123",
        PROMETHEUS_ENDPOINT,
    )
    with _server() as (host, port):
        for path in paths:
            status, _, _ = _get(host, port, path, token=None)
            assert status == 401


def test_model_metadata_and_router_status_shapes():
    with _server() as (host, port):
        c_status, c_headers, c_raw = _get(
            host, port, MODEL_CAPABILITIES_ENDPOINT + "?model=LLM.PRIMARY"
        )
        f_status, _, f_raw = _get(host, port, MODEL_FINGERPRINTS_ENDPOINT)
        s_status, _, s_raw = _get(host, port, ROUTER_STATUS_ENDPOINT)
    assert c_status == f_status == s_status == 200
    assert c_headers["Cache-Control"] == "no-store"
    capabilities = json.loads(c_raw)["data"][0]
    assert capabilities["modalities"] == ["image", "text"]
    assert capabilities["limits"]["images_per_request"] == 1
    fingerprint = json.loads(f_raw)["data"][0]
    assert fingerprint["served_identity"]["observed"] == (
        "qwen35-122b-a10b-nvfp4"
    )
    status = json.loads(s_raw)
    assert status["package_version"]
    assert status["model_aliases"] == ["llm.primary"]
    assert len(status["config_sha256"]) == 64


def test_stats_trace_and_prometheus_share_safe_decision_metadata():
    with _server() as (host, port):
        stats_status, _, stats_raw = _get(
            host, port, ROUTER_STATS_ENDPOINT + "?model=llm.primary"
        )
        trace_status, _, trace_raw = _get(
            host, port, REQUEST_TRACE_PREFIX + "req_123"
        )
        metrics_status, metrics_headers, metrics_raw = _get(
            host, port, PROMETHEUS_ENDPOINT + "?model=llm.primary"
        )
    assert stats_status == trace_status == metrics_status == 200
    stats = json.loads(stats_raw)
    assert stats["scope"] == "current_decision_log_buffer"
    assert stats["requests"] == {"total": 1, "succeeded": 1, "failed": 0}
    trace = json.loads(trace_raw)
    assert trace["record"]["request_id"] == "req_123"
    assert trace["record"]["total_prompt_tokens"] == 100
    assert metrics_headers["Content-Type"] == (
        "text/plain; version=0.0.4; charset=utf-8"
    )
    metrics = metrics_raw.decode("utf-8")
    assert "# TYPE anvil_router_decision_buffer_requests gauge" in metrics
    assert (
        'anvil_router_model_kv_cache_usage_fraction'
        '{model="llm.primary",tier="primary-local"} 0.25'
    ) in metrics


def test_unknown_models_bad_queries_and_missing_requests_are_clean_errors():
    with _server() as (host, port):
        model_status, _, model_raw = _get(
            host, port, MODEL_CAPABILITIES_ENDPOINT + "?model=missing"
        )
        stats_status, _, stats_raw = _get(
            host, port, ROUTER_STATS_ENDPOINT + "?window=15m"
        )
        trace_status, _, trace_raw = _get(
            host, port, REQUEST_TRACE_PREFIX + "missing"
        )
    assert model_status == 404
    assert json.loads(model_raw)["error"]["type"] == "model_not_found"
    assert stats_status == 400
    assert json.loads(stats_raw)["error"]["type"] == "invalid_request"
    assert trace_status == 404
    assert json.loads(trace_raw)["error"]["type"] == "not_found"


def test_health_advertises_all_read_only_operational_routes():
    with _server() as (host, port):
        status, _, raw = _get(host, port, "/health")
    assert status == 200
    routes = json.loads(raw)["routes"]
    for route in (
        MODEL_CAPABILITIES_ENDPOINT,
        MODEL_FINGERPRINTS_ENDPOINT,
        ROUTER_STATUS_ENDPOINT,
        ROUTER_STATS_ENDPOINT,
        "/v1/requests/{request_id}",
        PROMETHEUS_ENDPOINT,
    ):
        assert route in routes
