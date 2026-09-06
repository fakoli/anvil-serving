from __future__ import annotations

import json
import re
import time
import urllib.request
from datetime import datetime, timezone

from anvil_serving.observability.api import (
    ProbeRegistration,
    TelemetryRegistry,
    run_server_in_thread,
)
from anvil_serving.observability.dashboard.app import DashboardSampler, create_dashboard_server
from anvil_serving.observability.retention import BENCHMARK_PROFILE, RetentionStore
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


def _sample(
    metric: str,
    capability: str,
    value: float,
    *,
    labels=(),
    observed_at: datetime | None = None,
):
    observed = observed_at or datetime.now(timezone.utc)
    return TelemetrySample(
        metric=metric,
        source_timestamp=observed,
        collection_timestamp=observed,
        host_id="dark",
        collector_id="fixture",
        capability=capability,
        capability_status=CapabilityStatus.OK,
        value=value,
        unit="bytes",
        stale_after_seconds=60,
        labels=labels,
    )


def _snapshot(observed_at: datetime | None = None):
    observed = observed_at or datetime.now(timezone.utc)
    samples = [
        _sample("host.cpu.utilization", "cpu", 20, observed_at=observed),
        _sample("host.memory.used", "physical-memory", 1000, observed_at=observed),
        _sample("host.paging.rate", "paging", 1, observed_at=observed),
        _sample("host.disk.throughput", "disk-activity", 2, observed_at=observed),
        _sample("host.network.throughput", "network-activity", 3, observed_at=observed),
        _sample("boundary.memory.used", "memory", 400, observed_at=observed),
        _sample(
            "gpu.utilization",
            "utilization",
            50,
            labels=(("gpu_index", "0"),),
            observed_at=observed,
        ),
        _sample(
            "gpu.memory.used",
            "dedicated-vram",
            500,
            labels=(("gpu_index", "0"),),
            observed_at=observed,
        ),
        _sample("gpu.memory.shared.used", "shared-gpu-memory", 100, observed_at=observed),
        _sample(
            "container.memory.used",
            "memory",
            200,
            labels=(("container_name", "router"), ("attribution", "configured")),
            observed_at=observed,
        ),
        _sample(
            "service.health",
            "service-health",
            1,
            labels=(
                ("component", "router"),
                ("health", "healthy"),
                ("served_identity", "anvil-router"),
                ("owning_host", "dark"),
            ),
            observed_at=observed,
        ),
    ]
    return {
        "schema_version": 1,
        "generated_at": observed.isoformat().replace("+00:00", "Z"),
        "capabilities": [
            "host-resources",
            "boundary-resources",
            "nvidia-gpu",
            "containers",
            "service-health",
        ],
        "available_capabilities": [
            "host-resources",
            "boundary-resources",
            "nvidia-gpu",
            "containers",
            "service-health",
        ],
        "sample_count": len(samples),
        "degraded_count": 0,
        "samples": [sample.to_dict() for sample in samples],
    }


def test_supported_dashboard_serves_current_history_and_interpretation_read_only() -> None:
    observed_at = datetime.now(timezone.utc)
    store = RetentionStore()
    store.add(
        _snapshot(observed_at),
        observed_at=observed_at,
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )
    server = create_dashboard_server(TelemetryRegistry(), port=0, retention=store)
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        payloads = {}
        for path in ("/", "/v1/metrics", "/v1/timeseries", "/v1/indicators"):
            with urllib.request.urlopen(base + path, timeout=2) as response:
                payloads[path] = response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    html = payloads["/"].decode("utf-8")
    metrics = json.loads(payloads["/v1/metrics"])["data"]
    series = json.loads(payloads["/v1/timeseries"])["data"]
    indicators = json.loads(payloads["/v1/indicators"])["data"]
    assert metrics["sample_count"] == 11
    assert series["retained_bytes"] == store.total_bytes
    assert set(series["signals"]) >= {
        "cpu",
        "physical-memory",
        "gpu-utilization",
        "wsl-docker-cpu",
    }
    assert indicators["model_loading"]["phase"] == "vram-resident"
    assert "System graphs" in html
    assert "Model loading transition" in html
    assert "series[0]" not in html
    assert "Object.entries(body.data.signals)" in html
    # Navigation and connection controls are read-only; lifecycle actions are not.
    buttons = re.findall(r"<button\b[^>]*>(.*?)</button>", html, re.IGNORECASE | re.DOTALL)
    assert sorted(label.strip().lower() for label in buttons) == [
        "connect", "connect", "disconnect", "overview", "probes", "workloads",
    ]
    assert ">connect</button>" in html.lower()
    assert 'data-tab="overview"' in html.lower()
    assert 'data-tab="probes"' in html.lower()
    assert 'data-tab="workloads"' in html.lower()
    assert 'id="probe-search"' in html.lower()
    assert not any(action in html.lower() for action in ("/start", "/stop", "/restart"))


def test_sampler_separates_core_and_costly_cadences_into_retention() -> None:
    registry = TelemetryRegistry(
        (
            ProbeRegistration(
                "host-resources",
                lambda: [_sample("host.memory.used", "physical-memory", 1)],
                "dark",
                "host",
            ),
            ProbeRegistration(
                "nvidia-gpu",
                lambda: [_sample("gpu.utilization", "utilization", 1)],
                "dark",
                "gpu",
            ),
        )
    )
    store = RetentionStore()
    sampler = DashboardSampler(registry, store)
    sampler.start()
    deadline = time.monotonic() + 2
    while len(store.frames()) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    sampler.stop()

    frames = store.frames()
    assert {frame.snapshot["sampling_group"] for frame in frames} == {"core", "costly"}
    assert {frame.expected_interval_seconds for frame in frames} == {2, 5}

    benchmark_store = RetentionStore()
    benchmark_sampler = DashboardSampler(registry, benchmark_store, profile=BENCHMARK_PROFILE)
    benchmark_sampler.start()
    deadline = time.monotonic() + 2
    while len(benchmark_store.frames()) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    benchmark_sampler.stop()
    assert {frame.expected_interval_seconds for frame in benchmark_store.frames()} == {1, 2}
