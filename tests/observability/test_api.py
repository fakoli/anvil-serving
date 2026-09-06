from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from anvil_serving import mcp
from anvil_serving.control_plane.authorization import load_authorization_policy
from anvil_serving.observability.api import (
    ProbeRegistration,
    TelemetryRegistry,
    build_default_registry,
    create_server,
    run_server_in_thread,
)
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample
from anvil_serving.observability.workload_http import WorkloadHTTPService
from anvil_serving.observability.workloads import FleetResult, ResultStatus, Truncation


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
TOKEN = "api-controller-secret"
WORKLOAD_TOKEN = "workload-reader-fixture-token"


def _sample(*, detail=None):
    return TelemetrySample(
        metric="host.memory.used",
        source_timestamp=NOW,
        collection_timestamp=NOW,
        host_id="generic-host",
        collector_id="fixture",
        capability="host-resources",
        capability_status=CapabilityStatus.OK,
        value=1024,
        unit="bytes",
        stale_after_seconds=10,
        detail=detail,
    )


def _registry(probe=None):
    return TelemetryRegistry([
        ProbeRegistration(
            "host-resources", probe or (lambda: [_sample()]), "generic-host", "fixture"
        )
    ])


def _workload_service(tmp_path, calls, *, reader=None):
    policy_path = tmp_path / "workload-policy.json"
    policy_path.write_text(
        json.dumps({"schema_version": 1, "clients": [{
            "id": "reader",
            "scopes": ["workloads:read"],
            "credential_env": "WORKLOAD_TOKEN",
        }]}),
        encoding="utf-8",
    )
    policy = load_authorization_policy(policy_path, env={"WORKLOAD_TOKEN": WORKLOAD_TOKEN})

    def default_reader(*_args, **_kwargs):
        calls.append(True)
        return FleetResult(ResultStatus.COMPLETE, NOW, (), Truncation(0, 0))

    return WorkloadHTTPService(
        "http://127.0.0.1:8765",
        "controller-a",
        policy,
        clock=lambda: NOW,
        reader=default_reader if reader is None else reader,
    )


def _workload_request(base, path="/v1/workloads", *, method="GET", data=None, headers=None):
    request_headers = {"Authorization": "Bearer " + WORKLOAD_TOKEN}
    request_headers.update(headers or {})
    return urllib.request.Request(base + path, data=data, headers=request_headers, method=method)


def test_registry_returns_structured_redacted_probe_contract() -> None:
    payload = _registry(lambda: [_sample(detail=f"Bearer {TOKEN}")]).snapshot(
        generated_at=NOW, secrets=(TOKEN,)
    )

    assert payload["schema_version"] == 1
    assert payload["sample_count"] == 1
    assert payload["samples"][0]["metric"] == "host.memory.used"
    assert TOKEN not in json.dumps(payload)


def test_probe_failure_is_degraded_without_hiding_registry() -> None:
    def broken():
        raise RuntimeError("probe broke")

    payload = _registry(broken).snapshot(generated_at=NOW)

    assert payload["degraded_count"] == 1
    assert payload["samples"][0]["capability_status"] == "failed"
    assert payload["samples"][0]["value"] is None


def test_default_bind_is_loopback_and_non_loopback_requires_authentication() -> None:
    server = create_server(_registry())
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()

    with pytest.raises(ValueError, match="require authentication"):
        create_server(_registry(), host="100.64.0.20")
    with pytest.raises(ValueError, match="private"):
        create_server(
            _registry(), host="8.8.8.8", auth_env="ANVIL_TELEMETRY_TOKEN",
            environment={"ANVIL_TELEMETRY_TOKEN": TOKEN},
        )


def test_authenticated_server_returns_json_and_refuses_writes() -> None:
    server = create_server(
        _registry(), auth_env="ANVIL_TELEMETRY_TOKEN",
        environment={"ANVIL_TELEMETRY_TOKEN": TOKEN},
    )
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(base + "/v1/metrics", timeout=2)
        assert unauthorized.value.code == 401

        request = urllib.request.Request(
            base + "/v1/metrics?capability=host-resources",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        assert payload["ok"] is True
        assert payload["data"]["samples"][0]["collector_id"] == "fixture"
        assert TOKEN not in json.dumps(payload)

        post = urllib.request.Request(
            base + "/v1/metrics", data=b"{}",
            headers={"Authorization": f"Bearer {TOKEN}"}, method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(post, timeout=2)
        assert refused.value.code == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("telemetry_env", ({}, {"TELEMETRY_TOKEN": TOKEN}))
def test_reserved_workload_route_precedes_legacy_auth_and_cannot_be_shadowed(tmp_path, telemetry_env):
    calls = []
    service = _workload_service(tmp_path, calls)
    private = "private-static-callback-token"
    server = create_server(
        _registry(),
        auth_env="TELEMETRY_TOKEN" if telemetry_env else None,
        environment=telemetry_env,
        static_routes={"/v1/workloads": ("text/plain", private.encode())},
        public_static_routes=("/v1/workloads",),
        json_routes={"/v1/workloads": lambda: {"private": private}},
        query_routes={"/v1/workloads": lambda _query: {"private": private}},
        workload_service=service,
    )
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(_workload_request(base, "/v1/workloads?limit=1"), timeout=2) as response:
            body = response.read()
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert "Access-Control-Allow-Origin" not in response.headers
        assert json.loads(body) == {
            "ok": True,
            "data": {
                "schema": "anvil-workloads/v1",
                "status": "complete",
                "collection_timestamp": "2026-07-11T20:00:00.000000Z",
                "nodes": [],
                "truncation": {"returned": 0, "omitted": 0},
            },
        }
        assert private.encode() not in body
        assert calls == [True]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_reserved_workload_absence_exception_and_invalid_service_are_fixed(tmp_path):
    calls = []
    with pytest.raises(ValueError, match="exact WorkloadHTTPService"):
        create_server(_registry(), workload_service=lambda: None)

    absent = create_server(_registry())
    failing = create_server(
        _registry(),
        workload_service=_workload_service(tmp_path, calls, reader=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private failure"))),
    )
    threads = [(absent, run_server_in_thread(absent)), (failing, run_server_in_thread(failing))]
    try:
        for server, _thread in threads:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(_workload_request(base), timeout=2)
            body = refused.value.read()
            assert refused.value.code in {403, 503}
            assert json.loads(body)["error"]["code"] in {
                "authorization_scope_denied", "workload_source_unavailable"
            }
            assert b"private failure" not in body
    finally:
        for server, thread in threads:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


@pytest.mark.parametrize("framing", [
    "Content-Length: 1\r\n",
    "Content-Length: 0\r\nContent-Length: 0\r\n",
    "Transfer-Encoding: chunked\r\n",
    "X-Anvil-Idempotency-Key: private-header\r\n",
])
def test_reserved_workload_rejects_unread_body_headers_and_post_before_service(tmp_path, framing):
    calls = []
    server = create_server(_registry(), workload_service=_workload_service(tmp_path, calls))
    thread = run_server_in_thread(server)
    host, port = server.server_address[:2]
    try:
        with socket.create_connection((host, port), timeout=2) as connection:
            connection.sendall(
                (
                    "GET /v1/workloads HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n"
                    f"Authorization: Bearer {WORKLOAD_TOKEN}\r\n"
                    f"{framing}"
                    "\r\n"
                    "x"
                    "GET /v1/workloads HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
                ).encode("ascii")
            )
            response = b""
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                response += chunk
        assert b" 400 " in response
        assert b"invalid_workload_request" in response
        assert b"private-header" not in response
        assert calls == []

        with socket.create_connection((host, port), timeout=2) as connection:
            connection.sendall(
                (
                    "GET http://example.invalid/v1/workloads HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n\r\n"
                ).encode("ascii")
            )
            absolute_response = b""
            while True:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                absolute_response += chunk
        assert b" 400 " in absolute_response
        assert b"invalid_workload_request" in absolute_response
        assert calls == []

        base = f"http://127.0.0.1:{port}"
        with pytest.raises(urllib.error.HTTPError) as post:
            urllib.request.urlopen(_workload_request(base, method="POST", data=b"{}"), timeout=2)
        assert post.value.code == 405
        assert json.loads(post.value.read())["error"]["code"] == "read_only_workload_api"
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_mcp_controller_tool_returns_same_structured_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "anvil_serving.observability.api.build_default_registry", lambda: _registry()
    )

    result = mcp.call_tool("observability_collect", {"capabilities": ["host-resources"]})

    assert result["ok"] is True
    assert result["data"]["samples"][0]["host_id"] == "generic-host"
    assert result["data"]["samples"][0]["collector_id"] == "fixture"


def test_api_has_no_third_party_imports() -> None:
    import ast
    import inspect
    import anvil_serving.observability.api as api

    tree = ast.parse(inspect.getsource(api))
    roots = {
        node.names[0].name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    assert roots <= {
        "hmac", "ipaddress", "json", "os", "platform", "re", "threading", "urllib"
    }


def test_macos_default_registry_preserves_model_free_mini_role(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("platform.node", lambda: "generic-mac")

    capabilities = build_default_registry().capabilities

    assert "host-resources" in capabilities
    assert "service-health" in capabilities
    assert "nvidia-gpu" not in capabilities
    assert "containers" not in capabilities


def test_mcp_rejects_empty_capability_request_as_typed_error() -> None:
    result = mcp.call_tool("observability_collect", {"capabilities": []})

    assert result["ok"] is False
    assert result["error"]["code"] == "bad_argument"
