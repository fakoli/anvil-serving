from __future__ import annotations

from datetime import datetime, timezone

from anvil_serving.observability.probes.windows_host import collect_windows_host
from anvil_serving.observability.schema import CapabilityStatus


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
RAW = {
    "cpu_percent": 37.5,
    "memory_total_bytes": 64 * 1024**3,
    "memory_available_bytes": 16 * 1024**3,
    "paging_pages_per_second": 12.0,
    "disk_bytes_per_second": 1_000_000.0,
    "network_bytes_per_second": 2_000_000.0,
}


def test_permission_failure_is_explicit_and_never_zero() -> None:
    def denied() -> dict[str, object]:
        raise PermissionError("Access is denied")

    samples = collect_windows_host(provider=denied, host_id="dark", collected_at=NOW)

    assert all(
        sample.capability_status is CapabilityStatus.PERMISSION_DENIED
        for sample in samples
    )
    assert all(sample.value is None for sample in samples)
