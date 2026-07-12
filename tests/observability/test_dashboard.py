from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from importlib.resources import files

import pytest

from anvil_serving.observability.api import (
    ProbeRegistration,
    TelemetryRegistry,
    run_server_in_thread,
)
from anvil_serving.observability.dashboard.app import create_dashboard_server
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


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
    assert "const roles=new map()" in lowered
    for group in ("windows system", "fast tier gpu", "heavy tier gpu", "wsl", "docker"):
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
    assert asset.is_file()
    assert asset.read_bytes().startswith(b"<!doctype html>")
