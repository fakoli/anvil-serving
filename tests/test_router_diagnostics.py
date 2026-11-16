"""Read-only diagnostics with real HTTP and adversarial synthetic responses."""
from __future__ import annotations

import json
import http.client
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anvil_serving import router_diagnostics as diagnostics
from anvil_serving.operator_output import OperatorError, UsageError


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
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


@pytest.mark.parametrize("status,expected", [(401, "router_access_denied"), (403, "router_access_denied"), (404, "request_not_found"), (500, "router_http_error"), (302, "router_http_error")])
def test_errors_are_content_free_and_redirects_never_receive_credentials(status, expected):
    with endpoint({"/v1/requests/req_123": (status, {"error": "PRIVATE-RESPONSE"})}) as (url, calls):
        with pytest.raises(OperatorError) as caught:
            diagnostics.diagnose_request("req_123", router_url=url, token="test-credential")
    assert caught.value.code == expected
    assert "PRIVATE-RESPONSE" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["http://example.com", "http://8.8.8.8", "https://u:pass@example.com", "https://example.com/path", "https://example.com/?secret=x", "https://example.com/#x", "https://example.com:wrong", "https://example.com\\evil", " https://example.com", "http://169.254.169.254"])
def test_unsafe_origins_fail_before_network(url):
    with pytest.raises(UsageError):
        diagnostics.diagnose_request("req_123", router_url=url, token="test-credential", _open=lambda *a, **k: pytest.fail("network"))


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
    worker = threading.Thread(target=upstream.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
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


def _dispatch_result():
    return {
        "request": {
            "gateway_request_id": "req_123", "request_id": None, "outcome": "succeeded",
            "route": "llm.primary", "requested_tier": "primary-local",
            "timing": {}, "finish_reason": None,
            "usage": {"prompt_tokens": None, "completion_tokens": None,
                      "prompt_source": "unknown", "completion_source": "unknown"},
            "output_limit": {"clamped": False}, "next_checks": [],
        },
    }


def test_cli_loads_credential_only_from_explicitly_configured_env_file(monkeypatch, tmp_path):
    config = tmp_path / "router-diagnostics.toml"
    credential = tmp_path / "diagnostics.env"
    config.write_text(
        'router_url = "http://127.0.0.1:8111"\nauth_env = "DIAGNOSTIC_TOKEN"\n'
        'credential_env_file = "diagnostics.env"\ntimeout = 2.5\n', encoding="utf-8",
    )
    credential.write_text("DIAGNOSTIC_TOKEN=file-only-token\n", encoding="utf-8")
    monkeypatch.delenv("DIAGNOSTIC_TOKEN", raising=False)
    captured = {}
    monkeypatch.setattr(diagnostics, "diagnose_request", lambda request_id, **kwargs: (
        captured.update(request_id=request_id, **kwargs) or _dispatch_result()
    ))
    result = diagnostics.dispatch(["--config", str(config), "--request-id", "req_123"])
    assert result.exit_code == 0
    assert captured == {"request_id": "req_123", "router_url": "http://127.0.0.1:8111",
                        "token": "file-only-token", "timeout": 2.5}
    assert "file-only-token" not in (result.human_stdout + result.human_stderr)


@pytest.mark.parametrize("body", [
    'unknown = "value"\n',
    'router_url = 7\n',
    'auth_env = "invalid-name"\n',
    'credential_env_file = "~/.env"\n',
    'timeout = 31\n',
    '[nested]\nvalue = "unsupported"\n',
])
def test_cli_malformed_diagnostics_config_fails_closed(tmp_path, body):
    config = tmp_path / "router-diagnostics.toml"
    config.write_text(body, encoding="utf-8")
    result = diagnostics.dispatch(["--config", str(config), "--request-id", "req_123"])
    assert result.exit_code != 0
    assert result.error is not None
    assert result.error.code == "router_diagnostics_config_invalid"
    assert str(config) not in result.human_stderr
