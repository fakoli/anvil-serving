from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anvil_serving.observability.dashboard.timeseries import CORE_SIGNALS, retained_timeseries
from anvil_serving.observability.retention import RetentionStore


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _snapshot(metrics):
    return {
        "samples": [
            {
                "host_id": "dark",
                "metric": metric,
                "labels": labels,
                "value": value,
                "unit": unit,
                "capability_status": status,
            }
            for metric, labels, value, unit, status in metrics
        ]
    }


def test_all_required_core_signals_have_chart_series() -> None:
    store = RetentionStore()
    metrics = [
        (names[0], {}, index + 1, "bytes", "ok")
        for index, names in enumerate(CORE_SIGNALS.values())
    ]
    store.add(
        _snapshot(metrics),
        observed_at=NOW,
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )

    payload = retained_timeseries(store)

    assert set(payload["signals"]) == set(CORE_SIGNALS)
    assert all(payload["signals"][name] for name in CORE_SIGNALS)
    assert all(payload["signals"][name][0]["points"][0][1] > 0 for name in CORE_SIGNALS)


def test_missing_intervals_and_degraded_values_become_gaps_not_zeroes() -> None:
    store = RetentionStore()
    metric = ("host.cpu.utilization", {}, 40, "percent", "ok")
    store.add(
        _snapshot([metric]),
        observed_at=NOW,
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )
    store.add(
        _snapshot([]),
        observed_at=NOW + timedelta(seconds=2),
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )
    store.add(
        _snapshot([("host.cpu.utilization", {}, None, "percent", "failed")]),
        observed_at=NOW + timedelta(seconds=4),
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )
    store.add(
        _snapshot([metric]),
        observed_at=NOW + timedelta(seconds=8),
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )

    points = retained_timeseries(store, signals=("cpu",))["signals"]["cpu"][0]["points"]

    assert None in points
    assert not any(point is not None and point[1] == 0 for point in points)


def test_curve_projection_reads_selected_bounded_retention_tier() -> None:
    store = RetentionStore(max_bytes=20_000)
    for index in range(4):
        store.add(
            _snapshot([("host.memory.used", {}, 100 + index, "bytes", "ok")]),
            observed_at=NOW + timedelta(seconds=index * 16),
            probe_duration_seconds=0.1,
            expected_interval_seconds=2,
        )

    payload = retained_timeseries(store, resolution_seconds=15, signals=("physical-memory",))

    assert payload["retained_bytes"] == store.total_bytes
    assert payload["resolution_seconds"] == 15
    points = payload["signals"]["physical-memory"][0]["points"]
    assert sum(point is not None for point in points) == len(store.frames(15))
