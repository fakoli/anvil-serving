"""Synthetic contracts for the bounded read-only metrics facade."""

from concurrent.futures import ThreadPoolExecutor
import copy
from collections import deque
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from anvil_serving.observability.dashboard.metrics_client import (
    MAX_POINTS, MAX_SERIES, MetricsClient, MetricsError, validate_inventory,
)


UUID = "GPU-00000000-0000-0000-0000-000000000001"
INVENTORY = {
    "hosts": [{"id": "host-a", "display_name": "Host A", "platform": "linux",
               "metric_host": "metric-host-a", "gpus": [
                   {"id": "gpu-a", "uuid": UUID, "label": "GPU A", "role": "compute"}]}],
    "serves": [{"id": "serve-a", "host_id": "host-a", "display_name": "Serve A",
                "model": "fixture/model-a", "engine": "sglang", "metric_serve": "metric-serve-a",
                "gpu_ids": ["gpu-a"], "aliases": ["primary"],
                "controller": {"url": "https://controller.example.invalid/private", "auth_env": "FIXTURE_IDENTITY"}}],
}


@pytest.fixture
def client():
    value = MetricsClient(prometheus_url="http://127.0.0.1:9090", inventory=INVENTORY,
                          grafana_url="https://grafana.example.invalid/graphs", clock=lambda: 1000000)
    yield value
    value.close()


def vector(value=2, timestamp=999990):
    values = []
    if value is not None:
        values.append({"metric": {"observatory_field": "value"}, "value": [1000000, str(value)]})
    if timestamp is not None:
        values.append({"metric": {"observatory_field": "timestamp"}, "value": [1000000, str(timestamp)]})
    return {"resultType": "vector", "result": values}


def spec(client):
    return client._specs("generation", list(client._hosts.values()), list(client._serves.values()))[0]


def test_config_mapping_is_private_and_owner_state_is_not_inferred(client):
    with patch.object(client, "_request", return_value=vector()):
        snapshot = client.snapshot()
    encoded = json.dumps(snapshot)
    for private in ("controller.example.invalid", "FIXTURE_IDENTITY", "metric-host-a", "metric-serve-a"):
        assert private not in encoded
    host, serve = snapshot["hosts"][0], snapshot["serves"][0]
    assert host["resources"]["cpu_utilization"]["value"] == 2
    assert host["gpus"][0]["uuid"] == UUID
    assert host["gpus"][0]["owners"] == []
    assert host["ownership_status"] == serve["ownership_status"] == "unknown"
    assert serve["runtime_state"] == serve["readiness"] == "unknown"
    assert serve["observed_model"] is None
    assert serve["metrics"]["errors"]["value"] is None
    assert serve["metrics"]["errors"]["status"] == "unsupported"
    assert host["resources"]["network"] == {"chart": "host_network", "scope": "per-interface"}


def test_fresh_zero_missing_stale_and_last_known_are_distinct(client):
    sample = spec(client)
    with patch.object(client, "_request", return_value=vector(0)):
        fresh = client._instant(sample, "tokens/s")
    assert fresh["value"] == 0 and fresh["status"] == "fresh"
    assert fresh["source_timestamp"] == 999990
    with patch.object(client, "_request", return_value=vector(7, 999000)):
        stale = client._instant(sample, "tokens/s")
    assert stale["value"] is None and stale["status"] == "stale"
    assert stale["last_known_value"] == 0
    with patch.object(client, "_request", return_value=vector(None, None)):
        missing = client._instant(("other", *sample[1:]), "tokens/s")
    assert missing["value"] is None and missing["last_known_value"] is None


@pytest.mark.parametrize("value,timestamp", [("NaN", 999990), ("+Inf", 999990), (1, 1000001), (1, None)])
def test_invalid_or_unknown_source_timestamp_does_not_publish_current_value(client, value, timestamp):
    with patch.object(client, "_request", return_value=vector(value, timestamp)):
        assert client._instant(spec(client), "tokens/s")["value"] is None


def test_duplicate_metric_scope_is_rejected(client):
    data = vector()
    data["result"].append(data["result"][0])
    with patch.object(client, "_request", return_value=data):
        assert client._instant(spec(client), "tokens/s")["value"] is None


def test_ttft_requires_exposed_native_measurements_to_report_insufficient_data(client):
    serve = client._serves["serve-a"]
    ttft = client._specs("ttft", list(client._hosts.values()), [serve])[0]
    def exposed(_, parameters):
        return vector(1) if "fakoli_metric_coverage" in parameters["query"] else vector(None, None)
    with patch.object(client, "_request", side_effect=exposed):
        reading = client._serve_metric("ttft", serve, ttft, "seconds")
    assert reading["value"] is None and reading["status"] == "insufficient_data"
    with patch.object(client, "_request", return_value=vector(None, None)):
        reading = client._serve_metric("ttft", serve, ttft, "seconds")
    assert reading["status"] == "unavailable"


def test_llamacpp_kv_is_explicitly_unsupported(client):
    serve = dict(client._serves["serve-a"], engine="llamacpp")
    with patch.object(client, "_request") as request:
        reading = client._serve_metric("kv_cache", serve, spec(client), "ratio")
    request.assert_not_called()
    assert reading["status"] == "unsupported" and reading["value"] is None


def test_chart_bounds_gaps_and_nonfinite_points(client):
    def response(path, params):
        assert path == "/api/v1/query_range"
        start, end, step = (int(params[k]) for k in ("start", "end", "step"))
        assert end - start <= 604800
        return {"resultType": "matrix", "result": [{"metric": {"instance": "https://private.example.invalid"},
                "values": [[start, "0"], [start + step, "NaN"], [end, "3"]]}]}
    with patch.object(client, "_request", side_effect=response):
        chart = client.chart("generation", serve_id="serve-a", window="7d")
    assert chart["status"] == "ok"
    assert len(chart["series"]) <= MAX_SERIES
    assert sum(len(s["points"]) for s in chart["series"]) <= MAX_POINTS
    assert chart["series"][0]["points"][0][1] == 0
    assert chart["series"][0]["points"][1][1] is None
    assert chart["series"][0]["points"][-1][1] == 3
    assert "private.example.invalid" not in json.dumps(chart)
    assert chart["grafana_url"].startswith("https://grafana.example.invalid/graphs/d/fakoli-inference?")


def test_unqualified_errors_do_not_make_up_a_metric_or_query(client):
    with patch.object(client, "_request") as request:
        chart = client.chart("errors", serve_id="serve-a")
    request.assert_not_called()
    assert chart["series"] == [] and chart["status"] == "unknown"


def test_requests_cannot_supply_promql_urls_or_mismatched_scope(client):
    with patch.object(client, "_request") as request:
        for args in [("up",), ("generation", "http://127.0.0.1"), ("generation", None, "other"),
                     ("generation", None, None, "30d")]:
            with pytest.raises(MetricsError):
                client.chart(*args)
    request.assert_not_called()


def test_physical_gpu_selection_preserves_spare_cards_and_scoped_serve(client):
    extra = {"id": "gpu-b", "uuid": "GPU-00000000-0000-0000-0000-000000000002", "label": "GPU B", "role": "spare"}
    client._hosts["host-a"]["gpus"].append(extra)
    queries = []
    def response(_, params):
        queries.append(params["query"])
        return {"resultType": "matrix", "result": []}
    with patch.object(client, "_request", side_effect=response):
        client.chart("gpu_memory", host_id="host-a")
    assert len(queries) == 2 and any(extra["uuid"] in q for q in queries)
    queries.clear()
    with patch.object(client, "_request", side_effect=response):
        client.chart("gpu_memory", serve_id="serve-a")
    assert len(queries) == 1 and UUID in queries[0]


def test_allowlisted_queries_use_source_freshness_and_exact_identity(client):
    query = spec(client)[2]
    for part in ['host="metric-host-a"', 'service="metric-serve-a"', 'model="fixture/model-a"',
                 'engine="sglang"', 'exporter="engine"', "timestamp(", "<= 90", "== 1"]:
        assert part in query
    gpu = client._specs("gpu_memory", list(client._hosts.values()), [])[0][2]
    assert UUID in gpu and "fakoli:gpu_last_success_timestamp_seconds" in gpu
    assert "sum(" not in gpu


@pytest.mark.parametrize("origin", ["http://public.example.invalid", "https://user:secret@example.invalid", "https://example.invalid/?token=x", "https://example.invalid/#x", "http://localhost:9090", "http://0.0.0.0:9090"])
def test_unsafe_origins_are_rejected(origin):
    with pytest.raises(MetricsError):
        MetricsClient(prometheus_url=origin, inventory=INVENTORY)


def test_inventory_rejects_unknown_injectable_duplicate_and_dangling_mapping():
    variants = []
    data = copy.deepcopy(INVENTORY); data["hosts"][0]["extra"] = True; variants.append(data)
    data = copy.deepcopy(INVENTORY); data["hosts"][0]["metric_host"] = 'x"} or up'; variants.append(data)
    data = copy.deepcopy(INVENTORY); data["serves"][0]["gpu_ids"] = ["absent"]; variants.append(data)
    data = copy.deepcopy(INVENTORY); data["hosts"].append(data["hosts"][0]); variants.append(data)
    data = copy.deepcopy(INVENTORY); data["serves"][0]["aliases"] = [{}]; variants.append(data)
    for data in variants:
        with pytest.raises(MetricsError):
            validate_inventory(data)


def test_integration_status_does_not_expose_mapping_or_origins(client):
    status = client.integration_status()
    assert status["hosts"] == ["host-a"] and status["serves"] == ["serve-a"]
    assert status["status"] == "unknown"
    assert "http" not in json.dumps(status) and "metric-host-a" not in json.dumps(status)


@pytest.fixture
def prometheus_stub():
    state = {"requests": 0, "active": 0, "maximum": 0, "redirect": False}
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            with lock:
                state["requests"] += 1
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
            try:
                time.sleep(0.03)
                if state["redirect"]:
                    self.send_response(302)
                    self.send_header("Location", "/redirect-target")
                    self.end_headers()
                    return
                body = json.dumps({"status": "success", "data": {"resultType": "vector", "result": []}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            finally:
                with lock:
                    state["active"] -= 1
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    client = MetricsClient(prometheus_url="http://127.0.0.1:" + str(server.server_port), inventory=INVENTORY)
    yield client, state
    client.close(); server.shutdown(); server.server_close(); thread.join()


def test_identical_concurrent_reads_coalesce_and_different_reads_cap_at_four(prometheus_stub):
    client, state = prometheus_stub
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: client._request("/api/v1/query", {"query": "fixture"}), range(12)))
    assert len(results) == 12 and state["requests"] == 1
    results[0]["result"].append("changed")
    assert client._request("/api/v1/query", {"query": "fixture"})["result"] == []
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda i: client._request("/api/v1/query", {"query": "fixture" + str(i)}), range(12)))
    assert state["maximum"] <= 4


def test_redirect_is_not_followed_and_errors_are_safe(prometheus_stub):
    client, state = prometheus_stub
    state["redirect"] = True
    with pytest.raises(MetricsError, match="Metrics read unavailable"):
        client._request("/api/v1/query", {"query": "fixture"})
    assert state["requests"] == 1


def test_chart_rejects_excess_series_without_exposing_payload(client):
    data = {"resultType": "matrix", "result": [{"metric": {"token": "private"}, "values": []}] * 9}
    with patch.object(client, "_request", return_value=data):
        result = client.chart("generation")
    assert result["series"] == [] and "private" not in json.dumps(result)


def two_gpu_inventory():
    inventory = copy.deepcopy(INVENTORY)
    inventory["hosts"][0]["gpus"].append({
        "id": "gpu-b", "uuid": "GPU-00000000-0000-0000-0000-000000000002",
        "label": "GPU B", "role": "compute-secondary",
    })
    inventory["serves"][0]["gpu_ids"] = ["gpu-a", "gpu-b"]
    return inventory


def test_gpu_index_and_inventory_reorder_preserve_physical_identity():
    inventory = two_gpu_inventory()
    cards = inventory["hosts"][0]["gpus"]
    expected = {cards[0]["uuid"]: ("gpu-a", "GPU A", "compute", 12),
                cards[1]["uuid"]: ("gpu-b", "GPU B", "compute-secondary", 29)}
    projections = []
    for reordered in (False, True):
        current = copy.deepcopy(inventory)
        if reordered:
            current["hosts"][0]["gpus"].reverse()
            current["serves"][0]["gpu_ids"].reverse()

        def response(_, params):
            for index, card in enumerate(cards):
                if card["uuid"] in params["query"]:
                    result = vector(expected[card["uuid"]][3])
                    # Exporter index changed, while its physical UUID did not.
                    result["result"][0]["metric"].update(
                        gpu=str(1 - index if reordered else index), gpu_uuid=card["uuid"])
                    return result
            return vector()

        reader = MetricsClient(prometheus_url="http://127.0.0.1:9090", inventory=current,
                               clock=lambda: 1000000)
        try:
            with patch.object(reader, "_request", side_effect=response):
                host = reader.snapshot()["hosts"][0]
            projections.append({gpu["uuid"]: (gpu["id"], gpu["label"], gpu["role"],
                                               gpu["memory_used"]["value"]) for gpu in host["gpus"]})
            # Telemetry cannot turn reordered devices into inferred reservations.
            assert all(gpu["owners"] == [] for gpu in host["gpus"])
            assert host["ownership_status"] == "unknown"
        finally:
            reader.close()
    assert projections == [expected, expected]


def test_tp2_serve_has_two_physical_cards_and_one_logical_throughput_source():
    inventory = two_gpu_inventory()
    cards = inventory["hosts"][0]["gpus"]
    requests = []

    def response(_, params):
        query = params["query"]
        requests.append(query)
        value = next((11 + index * 18 for index, card in enumerate(cards)
                      if card["uuid"] in query), 27)
        return {"resultType": "matrix", "result": [{"metric": {},
                "values": [[int(params["end"]), str(value)]]}]}

    reader = MetricsClient(prometheus_url="http://127.0.0.1:9090", inventory=inventory,
                           clock=lambda: 1000000)
    try:
        with patch.object(reader, "_request", side_effect=response):
            physical = reader.chart("gpu_memory", serve_id="serve-a")
            throughput = reader.chart("generation", serve_id="serve-a")
        assert {series["id"]: series["points"][-1][1] for series in physical["series"]} == {
            "host-a.gpu-a": 11, "host-a.gpu-b": 29}
        assert len(throughput["series"]) == 1
        assert throughput["series"][0]["id"] == "serve-a"
        assert throughput["series"][0]["points"][-1][1] == 27
        assert len(requests) == 3
        assert sum("fakoli:inference_output_tokens_per_second" in query for query in requests) == 1
    finally:
        reader.close()


def test_declining_router_buffer_gauge_is_never_a_throughput_counter(client):
    from anvil_serving.router.decision_log import DecisionRecord
    from anvil_serving.router.router_telemetry import render_prometheus

    gauge = "anvil_router_decision_buffer_completion_tokens"
    retained = deque(maxlen=1)
    for completion in (100, 10):
        retained.append(DecisionRecord(kind="chat", requested_tier="primary", attempts=(),
                                       served_tier="primary", total_prompt_tokens=1,
                                       total_completion_tokens=completion, route="primary"))
        rendered = render_prometheus(retained)
        assert f"# TYPE {gauge} gauge" in rendered
        assert f'{gauge}{{model=""}} {completion}' in rendered
    requests = []
    qualified_engine = False

    def response(_, params):
        query = params["query"]
        requests.append(query)
        # A source has the declining router gauge even when engine telemetry is absent.
        values = (100, 10) if gauge in query else ((18, 12) if qualified_engine else None)
        samples = [] if values is None else [{"metric": {}, "values": [
            [int(params["end"]) - int(params["step"]), str(values[0])],
            [int(params["end"]), str(values[1])]]}]
        return {"resultType": "matrix", "result": samples}

    with patch.object(client, "_request", side_effect=response):
        absent = client.chart("generation", serve_id="serve-a")
        qualified_engine = True
        measured = client.chart("generation", serve_id="serve-a")
    assert absent["series"] == [] and absent["status"] == "unknown"
    assert [point[1] for point in measured["series"][0]["points"][-2:]] == [18, 12]
    assert measured["unit"] == "tokens/s"
    assert all("anvil_router_" not in query and "rate(" not in query for query in requests)
