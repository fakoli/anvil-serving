from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anvil_serving.observability.dashboard.timeseries import retained_timeseries
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
