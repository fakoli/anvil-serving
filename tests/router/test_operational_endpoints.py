"""HTTP contracts for router metadata, telemetry, trace, and metrics endpoints."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest

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
from anvil_serving.router.serve import build_server
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


@contextmanager
def _running(httpd):
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[:2]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _built_workload_server(tmp_path, *, host="node-a", clock=None):
    config = tmp_path / "router.toml"
    policy = tmp_path / "policy.json"
    config.write_text(
        f'''[server]
auth_env = "ROUTER_TOKEN"
workload_host = "{host}"
[[router.tiers]]
id = "primary"
base_url = "http://127.0.0.1:30002/v1"
model = "test-model"
dialect = "openai"
context_limit = 4096
privacy = "local"
tool_support = true
auth_env = "UPSTREAM_TOKEN"
[router.model_routes]
llm.primary = "primary"
''',
        encoding="utf-8",
    )
    policy.write_text(
        '{"schema_version":1,"clients":[{"id":"reader","scopes":["workloads:read"],"credential_env":"WORKLOAD_TOKEN"}]}',
        encoding="utf-8",
    )
    return build_server(
        str(config),
        port=0,
        backends={"primary": StaticBackend(["ok"])},
        env={"ROUTER_TOKEN": TOKEN, "WORKLOAD_TOKEN": "workloads-token-12345"},
        authorization_policy=str(policy),
        workload_clock=clock,
    )


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


def test_built_server_workloads_endpoint_uses_its_exact_shared_registry(tmp_path):
    collected = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    server = _built_workload_server(tmp_path, clock=lambda: collected)
    registry = server.anvil_workloads
    assert registry is server.anvil_routing._workload_registry
    calls = []
    original = registry.source_result

    def source_result(host, query, now):
        calls.append((host, query, now))
        return original(host, query, now)

    registry.source_result = source_result
    with _running(server) as (host, port):
        denied, _, _ = _get(host, port, "/v1/workloads", token=TOKEN)
        status, headers, raw = _get(
            host, port,
            "/v1/workloads?active_only=false&limit=1&recent_seconds=10",
            token="workloads-token-12345",
        )
    assert denied == 403
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert len(calls) == 1
    call_host, query, call_time = calls[0]
    assert call_host == "node-a"
    assert query.active_only is False
    assert query.limit == 1
    assert query.recent_seconds == 10
    assert call_time == collected
    payload = json.loads(raw)
    assert set(payload) == {
        "schema", "host", "status", "collection_timestamp", "sources"
    }
    assert payload["host"] == "node-a"
    assert payload["sources"][0]["records"] == []


@pytest.mark.parametrize("filters,age_seconds,returned,omitted", [
    ({"active_only": True, "recent_seconds": 60, "limit": 1}, 10, 1, 1),
    ({"state": "checking", "active_only": False, "recent_seconds": 86400, "limit": 1000}, 10, 2, 0),
    ({"state": "terminal", "active_only": False, "recent_seconds": 1, "limit": 1}, 10, 0, 0),
    ({"active_only": True, "recent_seconds": 86400, "limit": 1000}, 60, 0, 0),
])
def test_workload_router_cli_controller_and_mcp_canonical_parity(tmp_path, monkeypatch, capsys, filters, age_seconds, returned, omitted):
    from anvil_serving import cli, mcp
    from anvil_serving.control_plane.controller import server as controller_server
    from anvil_serving.observability.fleet_workload_collection import build_fleet_workloads
    from anvil_serving.observability.workload_collection import build_node_workloads
    from anvil_serving.observability.workloads import (
        WorkloadOwner, node_result_from_dict, parse_workload_query,
    )
    from tests.control_plane.test_controller_fleet_workloads import SCOPED, _post, _server as fleet_server

    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
    clock = [now - timedelta(seconds=age_seconds)]
    router = _built_workload_server(tmp_path, clock=lambda: clock[0])
    registry = router.anvil_workloads
    for index in (1, 2):
        assert registry.begin(f"req_{index:032x}").activate()
    clock[0] = now
    arguments = {"owner": "router", "kind": "router-request", "host": "node-a", **filters}
    query = parse_workload_query(arguments)
    wire_query = urlencode({key: str(value).lower() if type(value) is bool else value for key, value in arguments.items()})

    class Fleet:
        def collect(self, query, now):
            source = registry.source_result("node-a", query, now)
            node = build_node_workloads("node-a", query, now, {WorkloadOwner.ROUTER: source})
            return build_fleet_workloads(("node-a",), query, now, {"node-a": node})

        def close(self):
            pass

    monkeypatch.setattr(controller_server, "build_workload_readers", lambda *_a, **_kw: {WorkloadOwner.ROUTER: registry.source_result})
    monkeypatch.setattr(cli, "_workload_now", lambda: now)
    monkeypatch.setenv("PARITY_ROUTER_TOKEN", "workloads-token-12345")
    monkeypatch.setenv("PARITY_FLEET_TOKEN", SCOPED)
    cli_filters = []
    for key, value in arguments.items():
        if key == "active_only":
            if value:
                cli_filters.append("--active-only")
        else:
            cli_filters.extend(["--" + key.replace("_", "-"), str(value)])
    with _running(router) as (host, port), fleet_server(tmp_path, monkeypatch, collector=Fleet(), workload_clock=lambda: now) as (controller, _, _):
        code, _, raw = _get(host, port, "/v1/workloads?" + wire_query, token="workloads-token-12345")
        assert code == 200
        expected = json.loads(raw)
        parsed = node_result_from_dict(expected)
        assert parsed.sources[0].truncation.returned == returned
        assert parsed.sources[0].truncation.omitted == omitted
        assert parsed.collection_timestamp == now
        for record in parsed.sources[0].records:
            assert record.created_at == now - timedelta(seconds=age_seconds)
            assert not record.freshness(now).is_stale

        assert cli.main(["router", "workloads", "--json", "--router-url", f"http://127.0.0.1:{port}/v1",
                         "--auth-env", "PARITY_ROUTER_TOKEN", "--expected-node", "node-a", *cli_filters]) == 0
        assert json.loads(capsys.readouterr().out)["data"] == expected
        status, node = _post(controller, "/tools/call", {"name": "node_workloads", "arguments": arguments})
        assert status == 200 and node["ok"] is True
        controller_node = node["data"]
        assert {key: controller_node[key] for key in ("schema", "host", "collection_timestamp")} == {
            key: expected[key] for key in ("schema", "host", "collection_timestamp")
        }
        assert [source for source in controller_node["sources"] if source["owner"] == "router"] == expected["sources"]
        # The controller reports all six authorities. Undeclared readers remain
        # unavailable even under an owner filter; they are not silently idle.
        assert len(controller_node["sources"]) == 6
        assert all(source["status"] == "unavailable" and source["truncation"] == {"returned": 0, "omitted": None}
                   for source in controller_node["sources"] if source["owner"] != "router")
        status, fleet = _post(controller, "/tools/call", {"name": "fleet_workloads", "arguments": arguments})
        assert status == 200 and fleet["ok"] is True
        assert fleet["data"]["nodes"] == [controller_node]
        assert fleet["data"]["truncation"] == {"returned": returned, "omitted": None}
        status, rpc = _post(controller, "/mcp", {
            "jsonrpc": "2.0", "id": "parity", "method": "tools/call", "params": {
                "name": "fleet_workloads", "arguments": arguments, "_meta": {
                    "io.modelcontextprotocol/protocolVersion": mcp.PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {"name": "parity-test", "version": "1"},
                },
            },
        }, headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": mcp.PROTOCOL_VERSION,
                    "Mcp-Method": "tools/call", "Mcp-Name": "fleet_workloads"})
        assert status == 200 and rpc["result"]["structuredContent"] == fleet
        assert cli.main(["fleet", "workloads", "--json", "--controller-url", f"http://127.0.0.1:{controller.server_address[1]}",
                         "--auth-env", "PARITY_FLEET_TOKEN", "--expected-node", "node-a", *cli_filters]) == 0
        output = json.loads(capsys.readouterr().out)
        assert output["data"] == fleet["data"] and output["context"] is None
        # Free text is not an extension point on any of these workload surfaces.
        bad_status, _, bad_raw = _get(host, port, "/v1/workloads?" + wire_query + "&callback=private-marker", token="workloads-token-12345")
        assert bad_status == 400 and b"private-marker" not in bad_raw
        _, bad = _post(controller, "/tools/call", {"name": "fleet_workloads", "arguments": {**arguments, "callback": "private-marker"}})
        assert bad["ok"] is False and bad["error"]["code"] == "invalid_workload_query"
        assert cli.main(["router", "workloads", "--json", "--callback", "private-marker"]) == 2
        assert "private-marker" not in capsys.readouterr().out
        assert parsed == node_result_from_dict(expected) and query.host == "node-a"
