from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.retention import (
    BENCHMARK_PROFILE,
    MAX_ORDINARY_BYTES,
    NORMAL_PROFILE,
    RetentionStore,
)


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _snapshot(value: int = 1):
    return {
        "samples": [
            {
                "host_id": "dark",
                "metric": "host.memory.used",
                "labels": {},
                "value": value,
            }
        ]
    }


def test_profiles_apply_approved_normal_and_benchmark_tiers() -> None:
    assert NORMAL_PROFILE.interval_for(costly=False) == 2
    assert NORMAL_PROFILE.interval_for(costly=True) == 5
    assert BENCHMARK_PROFILE.interval_for(costly=False) == 1
    assert 2 <= BENCHMARK_PROFILE.interval_for(costly=True) <= 5


def test_retention_windows_and_rollups_are_bounded_by_age() -> None:
    store = RetentionStore()
    for age in (
        timedelta(days=8),
        timedelta(days=2),
        timedelta(hours=2),
        timedelta(minutes=30),
        timedelta(),
    ):
        store.add(
            _snapshot(),
            observed_at=NOW - age,
            probe_duration_seconds=0.1,
            expected_interval_seconds=2,
        )

    assert all(NOW - frame.observed_at <= timedelta(hours=1) for frame in store.frames(0))
    assert all(NOW - frame.observed_at <= timedelta(hours=24) for frame in store.frames(15))
    assert all(NOW - frame.observed_at <= timedelta(days=7) for frame in store.frames(60))


def test_actual_timestamps_duration_and_sampling_gap_are_recorded() -> None:
    store = RetentionStore()
    first = store.add(
        _snapshot(), observed_at=NOW, probe_duration_seconds=0.25, expected_interval_seconds=2
    )
    second = store.add(
        _snapshot(),
        observed_at=NOW + timedelta(seconds=4),
        probe_duration_seconds=0.5,
        expected_interval_seconds=2,
    )

    assert first.observed_at == NOW
    assert first.probe_duration_seconds == 0.25
    assert first.gap is False
    assert second.gap is True

    with pytest.raises(ValueError, match="backwards"):
        store.add(
            _snapshot(),
            observed_at=NOW,
            probe_duration_seconds=0.1,
            expected_interval_seconds=2,
        )


def test_rollups_average_numeric_series_within_bucket() -> None:
    store = RetentionStore()
    store.add(
        _snapshot(10), observed_at=NOW, probe_duration_seconds=0.1, expected_interval_seconds=2
    )
    store.add(
        _snapshot(20),
        observed_at=NOW + timedelta(seconds=2),
        probe_duration_seconds=0.3,
        expected_interval_seconds=2,
    )

    frame = store.frames(15)[0]
    assert frame.snapshot["samples"][0]["value"] == 15
    assert frame.probe_duration_seconds == 0.2


def test_byte_budget_never_exceeds_cap_and_oldest_is_evicted() -> None:
    store = RetentionStore(max_bytes=3500)
    for index in range(50):
        store.add(
            {"sequence": index, "padding": "x" * 200},
            observed_at=NOW + timedelta(seconds=index * 16),
            probe_duration_seconds=0.1,
            expected_interval_seconds=2,
        )

    assert store.total_bytes <= 3500 <= MAX_ORDINARY_BYTES
    retained = [
        frame.snapshot.get("sequence") for tier in (0, 15, 60) for frame in store.frames(tier)
    ]
    assert retained
    assert min(value for value in retained if value is not None) > 0


def test_persistence_is_batched_and_suspended_during_capture(tmp_path) -> None:
    target = tmp_path / "ordinary-history.json"
    store = RetentionStore(persistence_path=target, persistence_batch_seconds=60)
    store.add(_snapshot(), observed_at=NOW, probe_duration_seconds=0.1, expected_interval_seconds=2)
    store.set_capture_active(True)

    assert store.flush_if_due(now=NOW) is False
    assert not target.exists()

    store.set_capture_active(False)
    assert store.flush_if_due(now=NOW) is True
    first = target.read_bytes()
    store.add(
        _snapshot(2),
        observed_at=NOW + timedelta(seconds=2),
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )
    assert store.flush_if_due(now=NOW + timedelta(seconds=30)) is False
    assert target.read_bytes() == first
    assert store.flush_if_due(now=NOW + timedelta(seconds=61)) is True
    assert json.loads(target.read_text(encoding="utf-8"))["tiers"]["0"]
