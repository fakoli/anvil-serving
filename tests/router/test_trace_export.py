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
    span = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert len(span["traceId"]) == 32 and len(bytes.fromhex(span["traceId"])) == 16
    assert len(span["spanId"]) == 16 and len(bytes.fromhex(span["spanId"])) == 8
    attributes = span["attributes"]
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


@pytest.mark.parametrize("failure", ["media", "bind", None])
def test_server_owns_exporter_on_failed_and_successful_startup(tmp_path, monkeypatch, failure):
    import socket

    from anvil_serving.router import serve
    from anvil_serving.router.config import ConfigError
    from tests.router.helpers import StaticBackend

    config = tmp_path / "router.toml"
    text = '\n[server]\nauth_env = "TEST_ROUTER_TOKEN"\ntrace_export_url = "http://127.0.0.1:4318/v1/traces"\n'
    if failure == "media":
        text += 'media_principal = "harness"\nmedia_scopes = ["media:read"]\nmedia_public_origin = "http://127.0.0.1:8080"\n'
    text += '\n[router.model_routes]\n"llm.primary" = "primary-local"\n[[router.tiers]]\nid = "primary-local"\nbase_url = "http://127.0.0.1:30000/v1"\nmodel = "m"\ndialect = "openai"\ncontext_limit = 4096\nprivacy = "local"\ntool_support = true\nauth_env = "TEST_BACKEND_TOKEN"\n'
    config.write_text(text)
    exporters = []

    def create_exporter(url):
        exporter = TraceExporter(url)
        exporters.append(exporter)
        return exporter

    monkeypatch.setattr(serve, "TraceExporter", create_exporter)
    try:
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            kwargs = dict(env={"TEST_ROUTER_TOKEN": "synthetic-" + "token"}, backends={"primary-local": StaticBackend(["ok"])},
                          port=occupied.getsockname()[1] if failure == "bind" else 0)
            if failure:
                with pytest.raises(
                    ConfigError if failure == "media" else OSError,
                    match="media gateway is enabled" if failure == "media" else None,
                ):
                    serve.build_server(str(config), **kwargs)
            else:
                server = serve.build_server(str(config), **kwargs)
                try:
                    assert exporters[0]._worker.is_alive()
                finally:
                    server.server_close()
            assert len(exporters) == 1
            assert not exporters[0]._worker.is_alive()
    finally:
        for exporter in exporters:
            exporter.close()
