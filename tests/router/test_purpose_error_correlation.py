"""Real HTTP purpose failures retain their status and terminal correlation."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

import pytest

from anvil_serving.router.config import PurposeModel
from anvil_serving.router.decision_log import DecisionLog
from anvil_serving.router.purpose import PurposeError, PurposeRouter
from anvil_serving.router_diagnostics import diagnose_record
from tests.router.helpers import StaticBackend
from tests.router.test_gateway_correlation import _post, _raw_server, _server


@pytest.mark.parametrize("status", [400, 413, 415, 422])
@pytest.mark.parametrize("kind,path", [
    ("embedding", "/v1/embeddings"), ("rerank", "/v1/rerank"),
])
def test_upstream_client_error_keeps_status_and_one_terminal_record(
    status, kind, path, capsys,
):
    seen_ids = []
    marker = "PRIVATE_UPSTREAM_ERROR_TEXT"

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            seen_ids.append(self.headers.get("X-Request-Id"))
            payload = json.dumps({"error": marker}).encode()
            self.send_response(status, marker)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    log = DecisionLog()
    with _raw_server(Upstream) as (host, port):
        purpose = PurposeRouter([
            PurposeModel("purpose-test", kind, "test-model", f"http://{host}:{port}/v1"),
        ], decision_log=log)
        with _server(StaticBackend(["ok"]), purpose=purpose) as address:
            body = {"model": "test-model", "input": "synthetic"} if kind == "embedding" else {
                "model": "test-model", "query": "synthetic", "documents": ["document"],
            }
            actual_status, headers, response = _post(*address, path, body)

    assert actual_status == status
    gateway_id = headers["X-Anvil-Request-Id"]
    assert seen_ids == [gateway_id]
    (record,) = log.records
    assert record.gateway_request_id == gateway_id
    assert record.attempts[0].outcome == "error"
    assert record.attempts[0].reason == f"upstream_rejected_{status}"
    assert marker not in response.decode() + repr(record) + capsys.readouterr().err


def test_unexpected_transport_failure_is_sanitized_and_recorded(capsys):
    def transport(*args, **kwargs):
        raise RuntimeError("PRIVATE_TRANSPORT_ERROR")

    log = DecisionLog()
    router = PurposeRouter([
        PurposeModel("purpose-test", "embedding", "test-model", "http://127.0.0.1:1/v1"),
    ], transport=transport, decision_log=log)
    gateway_id = "req_0123456789abcdef0123456789abcdef"
    with pytest.raises(PurposeError) as caught:
        router.dispatch("embedding", {"model": "test-model", "input": "synthetic"},
                        correlation={"gateway_request_id": gateway_id})
    assert caught.value.status == 502
    (record,) = log.records
    assert record.gateway_request_id == gateway_id
    assert record.attempts[0].reason == "backend_error_RuntimeError"
    assert "PRIVATE_TRANSPORT_ERROR" not in str(caught.value) + repr(record) + capsys.readouterr().err


@pytest.mark.parametrize("kind", ["embedding", "rerank"])
def test_unmeasured_purpose_latency_is_not_reported_as_measured_zero(kind):
    diagnosis = diagnose_record({"kind": kind, "latency_ms": 0})
    assert diagnosis["timing"]["latency_ms"] is None
