"""Synthetic contracts for the bounded read-only metrics facade."""

import copy
from collections import deque
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from anvil_serving.observability.dashboard.metrics_client import (
    MetricsClient, MetricsError,
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


def test_duplicate_metric_scope_is_rejected(client):
    data = vector()
    data["result"].append(data["result"][0])
    with patch.object(client, "_request", return_value=data):
        assert client._instant(spec(client), "tokens/s")["value"] is None


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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True); thread.start()
    client = MetricsClient(prometheus_url="http://127.0.0.1:" + str(server.server_port), inventory=INVENTORY)
    yield client, state
    client.close(); server.shutdown(); server.server_close(); thread.join()


def two_gpu_inventory():
    inventory = copy.deepcopy(INVENTORY)
    inventory["hosts"][0]["gpus"].append({
        "id": "gpu-b", "uuid": "GPU-00000000-0000-0000-0000-000000000002",
        "label": "GPU B", "role": "compute-secondary",
    })
    inventory["serves"][0]["gpu_ids"] = ["gpu-a", "gpu-b"]
    return inventory


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
