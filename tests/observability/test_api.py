from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from anvil_serving import mcp
from anvil_serving.observability.api import (
    ProbeRegistration,
    TelemetryRegistry,
    build_default_registry,
    create_server,
    run_server_in_thread,
)
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
TOKEN = "api-controller-secret"


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
