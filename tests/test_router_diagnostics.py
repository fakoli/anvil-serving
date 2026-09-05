"""Read-only diagnostics with real HTTP and adversarial synthetic responses."""
from __future__ import annotations

import json
import http.client
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anvil_serving import cli, router_diagnostics as diagnostics
from anvil_serving.operator_output import OperatorError, TransportError, UsageError


@contextmanager
def endpoint(routes):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            status, value = routes.get(self.path, (404, {}))
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/unexpected-token-destination")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


def record(**updates):
    value = {
        "request_id": "req_123", "route": "llm.primary", "requested_tier": "primary-local",
        "served_tier": "primary-local", "attempts": [{"succeeded": True, "reason": "served"}],
        "latency_ms": 100,
        "measurements": {"time_to_first_content_ms": 80, "upstream_duration_ms": 95, "finish_reason": "length"},
        "total_prompt_tokens": 23, "total_completion_tokens": 10,
        "usage": {"prompt_tokens": 23, "completion_tokens": 10, "prompt_source": "upstream", "completion_source": "upstream"},
    }
    value.update(updates)
    return {"object": "router_request", "scope": "current_decision_log_buffer", "record": value}


def test_gets_evidence_without_replay_and_separates_current_metadata():
    routes = {
        "/v1/requests/req_123": (200, record()),
        "/v1/router/status": (200, {"object": "router_status", "package_version": "1.0.0", "uptime_seconds": 9, "config_sha256": "a" * 64}),
    }
    with endpoint(routes) as (url, calls):
        result = diagnostics.diagnose_request("req_123", router_url=url, token="test-credential")
    assert [call[:2] for call in calls] == [("GET", "/v1/requests/req_123"), ("GET", "/v1/router/status")]
    assert all(call[2] == "Bearer test-credential" for call in calls)
    assert result["request"]["observations"] == ["output_limit_reached", "startup_dominated"]
    assert result["request"]["usage"]["prompt_source"] == "upstream"
    assert result["current_router"]["metadata"]["config_sha256"] == "a" * 64
    assert "current_router_metadata_is_not_request_time_identity" in result["limitations"]
    assert "127.0.0.1" not in json.dumps(result)
    assert "test-credential" not in json.dumps(result)


def test_current_status_failure_retains_historical_request_evidence():
    with endpoint({"/v1/requests/req_123": (200, record())}) as (url, _):
        result = diagnostics.diagnose_request("req_123", router_url=url, token="test-credential")
    assert result["current_router"] == {"status": "unavailable", "metadata": None}
    assert result["request"]["outcome"] == "succeeded"


@pytest.mark.parametrize("status,expected", [(401, "router_access_denied"), (403, "router_access_denied"), (404, "request_not_found"), (500, "router_http_error"), (302, "router_http_error")])
def test_errors_are_content_free_and_redirects_never_receive_credentials(status, expected):
    with endpoint({"/v1/requests/req_123": (status, {"error": "PRIVATE-RESPONSE"})}) as (url, calls):
        with pytest.raises(OperatorError) as caught:
            diagnostics.diagnose_request("req_123", router_url=url, token="test-credential")
    assert caught.value.code == expected
    assert "PRIVATE-RESPONSE" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("value", [b"[", [], {"object": "wrong"}, record(request_id="another"), b" " * (diagnostics.MAX_RESPONSE_BYTES + 1)], ids=["json", "list", "schema", "identity", "oversized"])
def test_invalid_or_mismatched_responses_are_rejected(value):
    with endpoint({"/v1/requests/req_123": (200, value)}) as (url, calls):
        with pytest.raises(TransportError):
            diagnostics.diagnose_request("req_123", router_url=url, token="test-credential")
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["http://example.com", "http://8.8.8.8", "https://u:pass@example.com", "https://example.com/path", "https://example.com/?secret=x", "https://example.com/#x", "https://example.com:wrong", "https://example.com\\evil", " https://example.com", "http://169.254.169.254"])
def test_unsafe_origins_fail_before_network(url):
    with pytest.raises(UsageError):
        diagnostics.diagnose_request("req_123", router_url=url, token="test-credential", _open=lambda *a, **k: pytest.fail("network"))


def test_unknown_fields_are_omitted_and_invalid_measurements_are_unknown():
    payload = record(
        route="token_PRIVATE", requested_tier="http://100.64.0.10:30000", served_tier="/private/path",
        measurements={"finish_reason": ["PRIVATE-RESPONSE"], "time_to_first_content_ms": True},
        messages="PRIVATE-PROMPT", exception="PRIVATE-EXCEPTION", latency_ms=float("inf"),
        usage={"prompt_source": "PRIVATE-SOURCE", "prompt_tokens": float("nan")},
        attempts=[{"succeeded": False, "reason": "PRIVATE-ERROR"}],
    )["record"]
    result = diagnostics.diagnose_record(payload)
    text = json.dumps(result, allow_nan=False)
    assert "PRIVATE" not in text and "100.64.0.10" not in text and "/private" not in text
    assert result["timing"]["latency_ms"] is None
    assert result["usage"]["prompt_source"] == "unknown"
    assert result["next_checks"] == ["inspect_selected_upstream_logs_using_request_id"]


def test_malformed_http_status_is_a_content_free_transport_error():
    import socketserver

    class Malformed(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.recv(8192)
            self.request.sendall(b"PRIVATE-INVALID-STATUS\r\n\r\n")

    server = socketserver.TCPServer(("127.0.0.1", 0), Malformed)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with pytest.raises(TransportError) as error:
            diagnostics.diagnose_request("req_123", router_url=f"http://127.0.0.1:{server.server_address[1]}", token="test-credential")
        assert error.value.code == "router_unreachable"
        assert "PRIVATE" not in str(error.value)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(3)


def test_generated_query_requires_exact_gateway_field_match():
    requested = "req_" + "f" * 32
    payload = record(request_id=requested, gateway_request_id="req_" + "a" * 32)
    with endpoint({f"/v1/requests/{requested}": (200, payload)}) as (url, calls):
        with pytest.raises(TransportError) as error:
            diagnostics.diagnose_request(requested, router_url=url, token="test-credential")
    assert error.value.code == "router_response_invalid"
    assert len(calls) == 1


def test_impossible_remote_clamp_and_phase_measurements_are_unknown():
    result = diagnostics.diagnose_record(record(
        latency_ms=10,
        measurements={"readiness_check_ms": 99, "upstream_duration_ms": 88, "time_to_first_content_ms": 77},
        output_limit={"requested": 1, "applied": 99, "clamped": True},
    )["record"])
    assert result["timing"] == {"latency_ms": 10, "readiness_check_ms": None, "upstream_duration_ms": None, "time_to_first_content_ms": None}
    assert result["output_limit"] == {"requested": None, "applied": None, "clamped": None}
    assert result["observations"] == []


def test_real_decision_projection_is_understood_without_schema_drift():
    from anvil_serving.router.decision_log import AttemptRecord, DecisionRecord, summarize_decisions

    decision = DecisionRecord(
        kind="chat", requested_tier="selected", served_tier="selected", route="llm.primary",
        attempts=(AttemptRecord("selected", True, "served", 5, 3, "served"),),
        total_prompt_tokens=5, total_completion_tokens=3,
        latency_ms=40, time_to_first_content_ms=10, upstream_duration_ms=37, finish_reason="tool_calls",
        prompt_tokens_source="upstream", completion_tokens_source="estimated",
    )
    result = diagnostics.diagnose_record(summarize_decisions([decision])["records"][0])
    assert result["timing"]["time_to_first_content_ms"] == 10
    assert result["timing"]["upstream_duration_ms"] == 37
    assert result["observations"] == ["model_requested_tool_execution", "completion_dominated"]
    assert result["usage"]["completion_source"] == "estimated"


def test_real_gateway_relay_lookup_and_diagnosis_share_generated_identity():
    from anvil_serving.router.backends.relay import RelayBackend
    from anvil_serving.router.config import RouterConfig, Tier
    from anvil_serving.router.serve import RoutingBackend
    from tests.router.helpers import server_context

    upstream_calls = []

    class Inference(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            upstream_calls.append((self.path, self.headers.get("X-Request-Id"), body))
            raw = json.dumps({
                "choices": [{"message": {"content": "Synthetic answer"}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 17, "completion_tokens": 4},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Inference)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    try:
        tier = Tier(
            id="selected", base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
            dialect="openai", context_limit=8192, privacy="local", tool_support=True,
            auth_env="EXAMPLE_KEY", model="synthetic-model", max_output_tokens=32,
        )
        routing = RoutingBackend(
            RouterConfig(tiers=(tier,), model_routes={"llm.primary": tier.id}),
            {tier.id: RelayBackend(tier, env={})},
        )
        with server_context(routing, token="test-credential") as (host, port):
            connection = http.client.HTTPConnection(host, port, timeout=5)
            try:
                connection.request("POST", "/v1/chat/completions", json.dumps({
                    "model": "llm.primary", "messages": [{"role": "user", "content": "Synthetic prompt"}],
                    "max_tokens": 64,
                }), {"Authorization": "Bearer test-credential", "Content-Type": "application/json", "X-Request-Id": "caller-run"})
                response = connection.getresponse()
                gateway_id = response.getheader("X-Anvil-Request-Id")
                response.read()
                assert response.status == 200
            finally:
                connection.close()
            report = diagnostics.diagnose_request(
                gateway_id, router_url=f"http://{host}:{port}", token="test-credential",
            )
    finally:
        upstream.shutdown()
        upstream.server_close()
        worker.join(3)

    request = report["request"]
    assert request["gateway_request_id"] == upstream_calls[0][1] == gateway_id
    assert request["request_id"] == "caller-run"
    assert request["finish_reason"] == "length"
    assert request["usage"] == {"prompt_tokens": 17, "completion_tokens": 4, "prompt_source": "upstream", "completion_source": "upstream"}
    assert request["output_limit"] == {"requested": 64, "applied": 32, "clamped": True}
    assert upstream_calls[0][0] == "/v1/chat/completions"
    assert upstream_calls[0][2]["max_tokens"] == 32
    assert len(upstream_calls) == 1  # Diagnosis never replays the inference request.
    assert "Synthetic prompt" not in json.dumps(report)
    assert "Synthetic answer" not in json.dumps(report)


def test_cli_returns_structured_error_and_exposes_declared_help(monkeypatch, capsys):
    monkeypatch.delenv("ANVIL_ROUTER_TOKEN", raising=False)
    assert cli.main(["router", "diagnose", "--request-id", "req_123", "--json"]) != 0
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["code"] == "router_credential_required"
    assert cli.main(["router", "diagnose", "--help"]) == 0
    assert "--request-id" in capsys.readouterr().out
