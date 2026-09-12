"""Metadata-only trace export stays off the request path and content-free."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anvil_serving.router.decision_log import AttemptRecord, DecisionRecord
from anvil_serving.router.trace_export import TraceExporter, validate_export_url


def _record(**changes):
    fields = {
        "kind": "chat", "requested_tier": "primary", "served_tier": "primary",
        "route": "llm.primary", "attempts": (AttemptRecord("primary", True, "served", 4, 2, "served"),),
        "total_prompt_tokens": 4, "total_completion_tokens": 2, "unix_ts": 1.0,
        "latency_ms": 10, "session_id": "private prompt", "client_id": "client-a",
        "gateway_request_id": "req_" + "a" * 32,
    }
    fields.update(changes)
    return DecisionRecord(**fields)


@pytest.mark.parametrize("url", [
    "http://8.8.8.8/v1/traces", "https://example.com/v1/traces",
    "http://user:pass@127.0.0.1/v1/traces", "http://127.0.0.1/v1/traces?key=x",
])
def test_trace_export_url_requires_explicit_private_collector(url):
    with pytest.raises(ValueError):
        validate_export_url(url)


def test_trace_export_is_metadata_only_and_uses_no_proxy_or_auth_headers():
    received = []
    done = threading.Event()

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            done.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    exporter = TraceExporter(f"http://127.0.0.1:{server.server_port}/v1/traces")
    try:
        exporter.export(_record())
        assert done.wait(2)
    finally:
        exporter.close()
        server.shutdown()
        server.server_close()
        worker.join(3)
    path, headers, body = received[0]
    text = body.decode()
    assert path == "/v1/traces"
    assert "authorization" not in {key.lower() for key in headers}
    assert "private prompt" not in text
    payload = json.loads(body)
    attributes = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"]
    assert {item["key"] for item in attributes} >= {
        "anvil.router.route", "anvil.router.client_id", "anvil.router.gateway_request_id",
    }


def test_trace_export_queue_overflow_drops_without_blocking():
    entered = threading.Event()

    class SlowCollector(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            entered.set()
            threading.Event().wait(0.2)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowCollector)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    exporter = TraceExporter(f"http://127.0.0.1:{server.server_port}/v1/traces", queue_size=1, timeout=1)
    try:
        for _ in range(200):
            exporter.export(_record())
        assert entered.wait(1)
        assert exporter.snapshot()["dropped"] > 0
    finally:
        exporter.close()
        server.shutdown()
        server.server_close()
        worker.join(3)
