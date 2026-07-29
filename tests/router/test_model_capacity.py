"""Contract tests for ``GET /v1/models/capacity``."""
from __future__ import annotations

import http.client
import json
import threading
from contextlib import contextmanager

from anvil_serving.router.availability import AlwaysAvailable
from anvil_serving.router.config import RouterConfig, Tier
from anvil_serving.router.front_door import MODEL_CAPACITY_ENDPOINT, make_server
from anvil_serving.router.model_capacity import (
    MetricsSnapshot,
    build_model_capacity,
    fetch_vllm_metrics,
)
from anvil_serving.router.serve import RoutingBackend
from tests.router.helpers import StaticBackend

TOKEN = "router-test-token"


def _tier() -> Tier:
    return Tier(
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
        max_concurrency=1,
        params={
            "private_note": "never-emit-me",
            "capacity": {
                "gpu_role": "primary",
                "gpu_name": "NVIDIA RTX PRO 6000 Blackwell Workstation Edition",
                "gpu_memory_total_mib": 97_887,
                "model_memory_gib": 73.22,
                "kv_cache_capacity_tokens": 571_950,
                "scheduler_max_num_seqs": 1,
                "image_limit": 1,
                "video_limit": 1,
                "media_admission_enabled": True,
                "image_tokens_estimate": 2048,
                "video_tokens_estimate": 8192,
            },
        },
    )


def _config() -> RouterConfig:
    return RouterConfig(
        tiers=(_tier(),),
        model_routes={
            "llm.primary": "primary-local",
            "vision.general": "primary-local",
        },
    )


def _live(_tier) -> MetricsSnapshot:
    return MetricsSnapshot(
        "available",
        {
            "requests_running": 1.0,
            "requests_waiting": 2.0,
            "kv_cache_usage_fraction": 0.25,
            "preemptions_total": 3.0,
            "multimodal_cache_queries_total": 5.0,
            "multimodal_cache_hits_total": 4.0,
        },
    )


def _routing() -> RoutingBackend:
    return RoutingBackend(
        _config(),
        {"primary-local": StaticBackend(["ok"])},
        availability=AlwaysAvailable(),
        capacity_metrics=_live,
    )


@contextmanager
def _server():
    httpd = make_server("127.0.0.1", 0, _routing(), auth_token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[:2]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(host, port, path, *, token=TOKEN):
    connection = http.client.HTTPConnection(host, port, timeout=5)
    try:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_capacity_snapshot_joins_config_readiness_and_live_engine_metrics():
    result = build_model_capacity(_config(), AlwaysAvailable(), _live, {})
    (row,) = result["data"]
    assert result["object"] == "list"
    assert row["id"] == "primary-local"
    assert row["aliases"] == ["llm.primary", "vision.general"]
    assert row["model"] == "qwen35-122b-a10b-nvfp4"
    assert row["loaded"] is True
    assert row["engine"] == {"name": "vllm", "quantization": "nvfp4"}
    assert row["capacity"] == {
        "context_limit_tokens": 262_144,
        "kv_cache_capacity_tokens": 571_950,
        "full_context_concurrency": 2.182,
        "configured_max_concurrency": 1,
        "scheduler_max_num_seqs": 1,
        "model_memory_gib": 73.22,
    }
    assert row["multimodal"] == {
        "admission_enabled": True,
        "image_limit": 1,
        "video_limit": 1,
        "image_tokens_estimate": 2048,
        "video_tokens_estimate": 8192,
    }
    assert row["live"]["kv_cache_used_tokens_estimate"] == 142_988
    assert row["live"]["kv_cache_remaining_tokens_estimate"] == 428_962


def test_only_allowlisted_capacity_metadata_is_emitted():
    body = json.dumps(build_model_capacity(_config(), AlwaysAvailable(), _live, {}))
    assert "private_note" not in body
    assert "never-emit-me" not in body
    assert "127.0.0.1" not in body
    assert "30002" not in body
    assert "ANVIL_TEST_KEY" not in body


def test_one_image_scenario_is_allowed_when_image_tokens_are_supplied():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {
            "model": ["llm.primary"],
            "images": ["1"],
            "input_tokens": ["1000"],
            "image_tokens": ["2048"],
            "output_tokens": ["4096"],
        },
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["context_tokens"] == 7144
    assert scenario["within_image_limit"] is True
    assert scenario["within_context_limit"] is True
    assert scenario["allowed"] is True


def test_image_count_uses_declared_visual_token_estimate():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {"model": ["llm.primary"], "images": ["1"]},
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["within_image_limit"] is True
    assert scenario["image_tokens"] == 2048
    assert scenario["image_tokens_source"] == "configured_estimate"
    assert scenario["within_context_limit"] is True
    assert scenario["allowed"] is True


def test_two_image_scenario_is_rejected_by_the_declared_limit():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {"images": ["2"]},
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["within_image_limit"] is False
    assert scenario["within_context_limit"] is True
    assert scenario["allowed"] is False


def test_video_scenario_accounts_for_count_and_visual_tokens():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {
            "videos": ["1"],
            "input_tokens": ["1000"],
            "video_tokens": ["8192"],
            "output_tokens": ["16384"],
        },
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["videos"] == 1
    assert scenario["context_tokens"] == 25_576
    assert scenario["within_video_limit"] is True
    assert scenario["allowed"] is True


def test_video_count_uses_declared_visual_token_estimate():
    result = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {"videos": ["1"]},
    )
    scenario = result["data"][0]["scenario"]
    assert scenario["within_video_limit"] is True
    assert scenario["video_tokens"] == 8192
    assert scenario["video_tokens_source"] == "configured_estimate"
    assert scenario["within_context_limit"] is True
    assert scenario["allowed"] is True


def test_missing_visual_estimate_remains_indeterminate_not_free():
    config = _config()
    capacity = config.tiers[0].params["capacity"]
    capacity.pop("image_tokens_estimate")
    capacity.pop("video_tokens_estimate")
    scenario = build_model_capacity(
        config,
        AlwaysAvailable(),
        _live,
        {"images": ["1"], "videos": ["1"]},
    )["data"][0]["scenario"]
    assert scenario["within_context_limit"] is None
    assert scenario["allowed"] is None
    assert "configured image_tokens_estimate is required" in scenario["note"]
    assert "configured video_tokens_estimate is required" in scenario["note"]


def test_video_limit_and_context_overflow_are_rejected():
    over_count = build_model_capacity(
        _config(), AlwaysAvailable(), _live, {"videos": ["2"]}
    )["data"][0]["scenario"]
    over_context = build_model_capacity(
        _config(),
        AlwaysAvailable(),
        _live,
        {"videos": ["1"], "video_tokens": ["262144"], "output_tokens": ["1"]},
    )["data"][0]["scenario"]

    assert over_count["within_video_limit"] is False
    assert over_count["allowed"] is False
    assert over_context["within_context_limit"] is False
    assert over_context["allowed"] is False


def test_metrics_provider_fault_degrades_only_live_values():
    def broken(_tier):
        raise RuntimeError("must not escape")

    result = build_model_capacity(_config(), AlwaysAvailable(), broken, {})
    (row,) = result["data"]
    assert row["loaded"] is True
    assert row["live"]["status"] == "unavailable"
    assert row["live"]["reason"] == "metrics_provider"


def test_endpoint_is_authenticated_filterable_and_not_cached():
    with _server() as (host, port):
        no_auth, _, _ = _get(host, port, MODEL_CAPACITY_ENDPOINT, token=None)
        status, headers, raw = _get(
            host, port, MODEL_CAPACITY_ENDPOINT + "?gpu_role=primary"
        )
        missing, _, missing_raw = _get(
            host, port, MODEL_CAPACITY_ENDPOINT + "?model=unknown"
        )
    assert no_auth == 401
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert len(json.loads(raw)["data"]) == 1
    assert missing == 404
    assert json.loads(missing_raw)["error"]["type"] == "model_not_found"


def test_bad_query_is_400_and_plain_backend_returns_empty_list():
    with _server() as (host, port):
        status, _, raw = _get(
            host, port, MODEL_CAPACITY_ENDPOINT + "?images=not-an-int"
        )
    assert status == 400
    assert json.loads(raw)["error"]["type"] == "invalid_request"

    httpd = make_server("127.0.0.1", 0, StaticBackend(["ok"]))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        status, _, raw = _get(host, port, MODEL_CAPACITY_ENDPOINT, token=None)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
    assert status == 200
    assert json.loads(raw) == {"object": "list", "data": []}


def test_vllm_metrics_fetch_is_bounded_and_model_filtered():
    payload = b"""\
# HELP vllm:num_requests_running Number running
vllm:num_requests_running{model_name="other"} 99
vllm:num_requests_running{model_name="qwen35-122b-a10b-nvfp4"} 1
vllm:num_requests_waiting{model_name="qwen35-122b-a10b-nvfp4"} 2
vllm:kv_cache_usage_perc{model_name="qwen35-122b-a10b-nvfp4"} 0.5
"""

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _amount):
            return payload

    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return _Response()

    result = fetch_vllm_metrics(
        _tier(),
        env={"ANVIL_TEST_KEY": "upstream-secret"},
        opener=opener,
        timeout=0.5,
    )
    assert result.status == "available"
    assert result.values["requests_running"] == 1.0
    assert result.values["requests_waiting"] == 2.0
    assert result.values["kv_cache_usage_fraction"] == 0.5
    assert seen == {
        "url": "http://127.0.0.1:30002/metrics",
        "authorization": "Bearer upstream-secret",
        "timeout": 0.5,
    }
