from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _sample(**overrides) -> TelemetrySample:
    values = {
        "metric": "host.memory.used_bytes",
        "source_timestamp": NOW,
        "collection_timestamp": NOW + timedelta(milliseconds=125),
        "host_id": "primary-node",
        "collector_id": "windows-host",
        "capability": "host-memory",
        "capability_status": CapabilityStatus.OK,
        "value": 0,
        "unit": "bytes",
        "stale_after_seconds": 2,
        "labels": (("scope", "physical"),),
    }
    values.update(overrides)
    return TelemetrySample(**values)


def test_duplicate_json_object_keys_are_rejected():
    payload = _sample().to_json().replace(
        '"capability_status":"ok"',
        '"capability_status":"failed","capability_status":"ok"',
    )

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        TelemetrySample.from_json(payload)
