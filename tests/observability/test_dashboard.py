from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

import pytest

from anvil_serving.observability.api import (
    ProbeRegistration,
    TelemetryRegistry,
    run_server_in_thread,
)
from anvil_serving.observability.dashboard import app
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample
from anvil_serving.observability.workloads import FleetResult, ResultStatus, Truncation


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
TELEMETRY_TOKEN = "dashboard-legacy-token"
WORKLOAD_TOKEN = "dashboard-workload-reader-token"


def _registry() -> TelemetryRegistry:
    samples = [
        TelemetrySample(
            metric=metric,
            source_timestamp=NOW,
            collection_timestamp=NOW,
            host_id="fixture-host",
            collector_id="fixture",
            capability=capability,
            capability_status=CapabilityStatus.OK,
            value=1,
            stale_after_seconds=10,
        )
        for metric, capability in (
            ("host.memory.used", "host-resources"),
            ("boundary.memory.used", "boundary-resources"),
            ("gpu.memory.used", "nvidia-gpu"),
            ("container.memory.used", "containers"),
            ("service.health", "service-health"),
        )
    ]
    return TelemetryRegistry(
        [ProbeRegistration("system-view", lambda: samples, "fixture-host", "fixture")]
    )


def _policy(tmp_path) -> str:
    path = tmp_path / "dashboard-workload-policy.json"
    path.write_text(
        json.dumps({"schema_version": 1, "clients": [{
            "id": "reader",
            "scopes": ["workloads:read"],
            "credential_env": "WORKLOAD_TOKEN",
        }]}),
        encoding="utf-8",
    )
    return str(path)


def _workload_request(base: str, token: str = WORKLOAD_TOKEN):
    return urllib.request.Request(
        base + "/v1/workloads",
        headers={"Authorization": "Bearer " + token},
    )


def test_dashboard_serves_packaged_single_page_and_metrics() -> None:
    server = create_dashboard_server(_registry(), port=0)
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/", timeout=2) as response:
            html = response.read().decode("utf-8")
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        with urllib.request.urlopen(base + "/v1/metrics", timeout=2) as response:
            metrics = json.loads(response.read())["data"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    for label in ("Overview", "Probes", "System graphs", "Search probes", "All probes"):
        assert label in html
    assert metrics["sample_count"] == 5


def test_dashboard_is_read_only_and_binds_loopback_by_default() -> None:
    server = create_dashboard_server(_registry(), port=0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()

    html = (
        files("anvil_serving.observability.dashboard.static")
        .joinpath("index.html")
        .read_text(encoding="utf-8")
    )
    lowered = html.lower()
    assert "apiFetch('/v1/metrics'" in html
    assert "method:" not in lowered
    assert 'type="password"' in lowered
    assert "sessionstorage" in lowered
    assert 'role="tablist"' in lowered
    assert 'role="tabpanel"' in lowered
    assert "capacityfor" in lowered
    assert "observed max" in lowered
    assert "search probes" in lowered
    assert "probe_render_limit=500" in lowered
    assert "if(activetab==='probes')renderprobeexplorer()" in lowered
    assert "resolvegpuroles" not in lowered
    assert "aggregate gpu memory" in lowered
    assert "graphics card" in lowered
    assert "fast tier gpu" not in lowered
    assert "heavy tier gpu" not in lowered
    for group in ("windows system", "shared graphics memory", "wsl", "docker"):
        assert group in lowered
    for action in ("/start", "/stop", "/restart", "/configure"):
        assert action not in lowered


def test_authenticated_dashboard_shell_loads_then_token_unlocks_apis() -> None:
    server = create_dashboard_server(
        _registry(), port=0, auth_env="ANVIL_TELEMETRY_TOKEN",
        environment={"ANVIL_TELEMETRY_TOKEN": "dashboard-secret"},
    )
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/", timeout=2) as response:
            html = response.read().decode("utf-8")
        assert "Dashboard bearer token" in html

        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            urllib.request.urlopen(base + "/v1/metrics", timeout=2)
        assert unauthorized.value.code == 401

        request = urllib.request.Request(
            base + "/v1/metrics",
            headers={"Authorization": "Bearer dashboard-secret"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            assert json.loads(response.read())["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_static_assets_are_present_in_package() -> None:
    asset = files("anvil_serving.observability.dashboard.static").joinpath("index.html")
    workload_asset = files("anvil_serving.observability.dashboard.static").joinpath("workloads.js")
    assert asset.is_file()
    assert asset.read_bytes().startswith(b"<!doctype html>")
    assert workload_asset.is_file() and workload_asset.read_bytes().startswith(b"/* Canonical")
    root = Path(__file__).parents[2]
    assert '"anvil_serving.observability.dashboard.static" = ["*.html", "*.js"]' in (
        root / "pyproject.toml"
    ).read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert workflow.index("run: node --version") < workflow.index("- name: Run tests")


def test_dashboard_packages_workload_script_and_forwards_only_presented_credential(
    monkeypatch, tmp_path
) -> None:
    calls = []
    policy_calls = []
    original_init = app.WorkloadHTTPService.__init__
    original_load_policy = app.load_authorization_policy

    def reader(endpoint, reference, node, query, now, **kwargs):
        calls.append((endpoint, reference, node, query, now, kwargs))
        return FleetResult(ResultStatus.COMPLETE, NOW, (), Truncation(0, 0))

    def configured_init(self, *args, **kwargs):
        kwargs["clock"] = lambda: NOW
        kwargs["reader"] = reader
        original_init(self, *args, **kwargs)

    def load_policy(*args, **kwargs):
        policy_calls.append((args, kwargs))
        return original_load_policy(*args, **kwargs)

    monkeypatch.setattr(app.WorkloadHTTPService, "__init__", configured_init)
    monkeypatch.setattr(app, "load_authorization_policy", load_policy)
    server = create_dashboard_server(
        _registry(),
        port=0,
        auth_env="TELEMETRY_TOKEN",
        environment={
            "TELEMETRY_TOKEN": TELEMETRY_TOKEN,
            "WORKLOAD_TOKEN": WORKLOAD_TOKEN,
            "UNRELATED": "private-unused-value",
        },
        workload_controller_url="http://127.0.0.1:8765",
        workload_expected_node="controller-a",
        workload_authorization_policy=_policy(tmp_path),
    )
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(_workload_request(base), timeout=2) as response:
            payload = json.loads(response.read())
        with urllib.request.urlopen(base + "/workloads.js", timeout=2) as response:
            script = response.read()
            assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert payload["ok"] is True
    assert script == files("anvil_serving.observability.dashboard.static").joinpath("workloads.js").read_bytes()
    assert len(calls) == 1
    endpoint, reference, node, _query, _now, kwargs = calls[0]
    assert (endpoint, reference, node) == (
        "http://127.0.0.1:8765",
        "WORKLOAD_REQUEST_CREDENTIAL",
        "controller-a",
    )
    assert kwargs["environment"] == {reference: WORKLOAD_TOKEN}
    assert len(policy_calls) == 1
    assert policy_calls[0][1]["env"]["WORKLOAD_TOKEN"] == WORKLOAD_TOKEN
    assert policy_calls[0][1]["legacy_token"] == TELEMETRY_TOKEN


@pytest.mark.parametrize(
    "options",
    (
        {},
        {"workload_controller_url": "http://127.0.0.1:8765"},
        {
            "workload_controller_url": "https://example.invalid",
            "workload_expected_node": "controller-a",
            "workload_authorization_policy": "relative-policy.json",
        },
    ),
)
def test_dashboard_invalid_or_incomplete_workload_config_keeps_telemetry_available(tmp_path, options):
    server = create_dashboard_server(
        _registry(),
        port=0,
        auth_env="TELEMETRY_TOKEN",
        environment={"TELEMETRY_TOKEN": TELEMETRY_TOKEN, "WORKLOAD_TOKEN": WORKLOAD_TOKEN},
        **options,
    )
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as workload:
            urllib.request.urlopen(_workload_request(base), timeout=2)
        assert workload.value.code == 403
        assert json.loads(workload.value.read())["error"]["code"] == "authorization_scope_denied"
        telemetry = urllib.request.Request(
            base + "/v1/metrics", headers={"Authorization": "Bearer " + TELEMETRY_TOKEN}
        )
        with urllib.request.urlopen(telemetry, timeout=2) as response:
            assert json.loads(response.read())["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_dashboard_parser_and_main_forward_explicit_workload_options(monkeypatch):
    parsed = app.build_parser().parse_args([
        "--workload-controller-url", "http://127.0.0.1:8765",
        "--workload-expected-node", "controller-a",
        "--workload-authorization-policy", "C:\\policy.json",
    ])
    assert parsed.workload_controller_url == "http://127.0.0.1:8765"
    assert parsed.workload_expected_node == "controller-a"
    assert parsed.workload_authorization_policy == "C:\\policy.json"
    help_text = app.build_parser().format_help()
    assert "Absolute LOCAL" in help_text and "authorization-policy" in help_text

    received = {}

    class FakeServer:
        server_address = ("127.0.0.1", 0)

        def serve_forever(self, **_kwargs):
            raise KeyboardInterrupt

        def server_close(self):
            received["closed"] = True

    class FakeSampler:
        def start(self):
            received["started"] = True

        def stop(self):
            received["stopped"] = True

    monkeypatch.setattr(app, "build_default_registry", lambda: _registry())
    monkeypatch.setattr(app, "RetentionStore", lambda: object())
    monkeypatch.setattr(app, "DashboardSampler", lambda *_args: FakeSampler())
    def create_server(*_args, **kwargs):
        received["kwargs"] = kwargs
        return FakeServer()

    monkeypatch.setattr(app, "create_dashboard_server", create_server)
    assert app.main([
        "--workload-controller-url", "http://127.0.0.1:8765",
        "--workload-expected-node", "controller-a",
        "--workload-authorization-policy", "C:\\policy.json",
    ]) == 0
    assert received["kwargs"]["workload_controller_url"] == "http://127.0.0.1:8765"
    assert received["kwargs"]["workload_expected_node"] == "controller-a"
    assert received["kwargs"]["workload_authorization_policy"] == "C:\\policy.json"
    assert received["started"] and received["stopped"] and received["closed"]
