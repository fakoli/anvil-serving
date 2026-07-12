from __future__ import annotations

from datetime import datetime, timezone

from anvil_serving.observability.probes.gpu_shared_memory import (
    SOURCE,
    collect_windows_shared_gpu_memory,
)
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def test_shared_memory_is_distinct_and_source_labeled() -> None:
    samples = collect_windows_shared_gpu_memory(
        provider=lambda: [
            {"adapter": "luid-a", "shared_used_bytes": 512 * 1024**2},
            {"adapter": "luid-b", "shared_used_bytes": 1024 * 1024**2},
        ],
        host_id="dark",
        collected_at=NOW,
    )

    assert [sample.metric for sample in samples] == [
        "gpu.memory.shared.used",
        "gpu.memory.shared.used",
    ]
    assert [sample.value for sample in samples] == [512 * 1024**2, 1024 * 1024**2]
    assert all(dict(sample.labels)["source"] == SOURCE for sample in samples)
    assert all("dedicated" not in sample.metric for sample in samples)
    assert all(TelemetrySample.from_json(sample.to_json()) == sample for sample in samples)


def test_platform_without_counter_is_unsupported_not_zero(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")

    sample = collect_windows_shared_gpu_memory(
        host_id="mini", collected_at=NOW
    )[0]

    assert sample.capability_status is CapabilityStatus.UNSUPPORTED
    assert sample.value is None
    assert dict(sample.labels)["source"] == SOURCE


def test_empty_counter_result_is_unsupported_not_zero() -> None:
    sample = collect_windows_shared_gpu_memory(
        provider=lambda: [], host_id="dark", collected_at=NOW
    )[0]

    assert sample.capability_status is CapabilityStatus.UNSUPPORTED
    assert sample.value is None


def test_null_or_invalid_adapter_value_is_missing_not_zero() -> None:
    for value in (None, -1, float("nan"), True):
        sample = collect_windows_shared_gpu_memory(
            provider=lambda value=value: [
                {"adapter": "luid-a", "shared_used_bytes": value}
            ],
            host_id="dark",
            collected_at=NOW,
        )[0]
        assert sample.capability_status is CapabilityStatus.MISSING
        assert sample.value is None


def test_permission_failure_is_explicit() -> None:
    def denied():
        raise PermissionError("Access is denied")

    sample = collect_windows_shared_gpu_memory(
        provider=denied, host_id="dark", collected_at=NOW
    )[0]

    assert sample.capability_status is CapabilityStatus.PERMISSION_DENIED
    assert sample.value is None


def test_adapter_rows_are_bounded() -> None:
    rows = [{"adapter": str(index), "shared_used_bytes": 1} for index in range(65)]

    sample = collect_windows_shared_gpu_memory(
        provider=lambda: rows, host_id="dark", collected_at=NOW
    )[0]

    assert sample.capability_status is CapabilityStatus.FAILED
    assert sample.value is None
