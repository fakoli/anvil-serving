from __future__ import annotations

from datetime import datetime, timezone

from anvil_serving.observability.probes.windows_host import collect_windows_host
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
RAW = {
    "cpu_percent": 37.5,
    "memory_total_bytes": 64 * 1024**3,
    "memory_available_bytes": 16 * 1024**3,
    "paging_pages_per_second": 12.0,
    "disk_bytes_per_second": 1_000_000.0,
    "network_bytes_per_second": 2_000_000.0,
}


def _by_metric(samples: list[TelemetrySample]) -> dict[str, TelemetrySample]:
    return {sample.metric: sample for sample in samples}


def test_probe_emits_normalized_cpu_memory_paging_disk_and_network_samples() -> None:
    samples = collect_windows_host(
        provider=lambda: RAW, host_id="dark", collected_at=NOW
    )
    metrics = _by_metric(samples)

    assert len(samples) == 8
    assert all(sample.capability_status is CapabilityStatus.OK for sample in samples)
    assert metrics["host.cpu.utilization"].value == 37.5
    assert metrics["host.memory.used"].value == 48 * 1024**3
    assert metrics["host.memory.pressure"].value == 75.0
    assert metrics["host.paging.rate"].value == 12.0
    assert metrics["host.disk.throughput"].value == 1_000_000.0
    assert metrics["host.network.throughput"].value == 2_000_000.0
    for sample in samples:
        assert TelemetrySample.from_json(sample.to_json()) == sample


def test_permission_failure_is_explicit_and_never_zero() -> None:
    def denied() -> dict[str, object]:
        raise PermissionError("Access is denied")

    samples = collect_windows_host(provider=denied, host_id="dark", collected_at=NOW)

    assert all(
        sample.capability_status is CapabilityStatus.PERMISSION_DENIED
        for sample in samples
    )
    assert all(sample.value is None for sample in samples)


def test_missing_individual_signal_does_not_hide_available_metrics() -> None:
    raw = dict(RAW)
    del raw["disk_bytes_per_second"]

    metrics = _by_metric(
        collect_windows_host(provider=lambda: raw, host_id="dark", collected_at=NOW)
    )

    assert metrics["host.disk.throughput"].capability_status is CapabilityStatus.MISSING
    assert metrics["host.disk.throughput"].value is None
    assert metrics["host.cpu.utilization"].capability_status is CapabilityStatus.OK


def test_invalid_memory_totals_degrade_derived_metrics_only() -> None:
    raw = dict(RAW, memory_available_bytes=128 * 1024**3)

    metrics = _by_metric(
        collect_windows_host(provider=lambda: raw, host_id="dark", collected_at=NOW)
    )

    assert metrics["host.memory.used"].capability_status is CapabilityStatus.MISSING
    assert metrics["host.memory.pressure"].capability_status is CapabilityStatus.MISSING
    assert metrics["host.memory.total"].capability_status is CapabilityStatus.OK


def test_non_windows_default_path_reports_unsupported(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Linux")

    samples = collect_windows_host(host_id="test-host", collected_at=NOW)

    assert all(sample.capability_status is CapabilityStatus.UNSUPPORTED for sample in samples)
    assert all(sample.value is None for sample in samples)


def test_no_third_party_runtime_imports_are_required() -> None:
    import anvil_serving.observability.probes.windows_host as windows_host

    assert windows_host.subprocess.__name__ == "subprocess"


def test_unexpected_provider_failure_is_returned_as_failed_samples() -> None:
    def broken() -> dict[str, object]:
        raise RuntimeError("provider bug")

    samples = collect_windows_host(provider=broken, host_id="dark", collected_at=NOW)

    assert all(sample.capability_status is CapabilityStatus.FAILED for sample in samples)
    assert all(sample.value is None for sample in samples)


def test_oversized_failure_detail_is_bounded() -> None:
    def denied() -> dict[str, object]:
        raise PermissionError("x" * 10_000)

    samples = collect_windows_host(provider=denied, host_id="dark", collected_at=NOW)

    assert all(len(sample.detail or "") == 4096 for sample in samples)


def test_null_signal_is_missing_not_a_healthy_zero() -> None:
    raw = dict(RAW, network_bytes_per_second=None)

    metric = _by_metric(
        collect_windows_host(provider=lambda: raw, host_id="dark", collected_at=NOW)
    )["host.network.throughput"]

    assert metric.capability_status is CapabilityStatus.MISSING
    assert metric.value is None
