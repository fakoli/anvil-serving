from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


from anvil_serving.observability.retention import (
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
