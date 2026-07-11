from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _sample(**overrides) -> TelemetrySample:
    values = {
        "metric": "host.memory.used_bytes",
        "source_timestamp": NOW,
        "collection_timestamp": NOW + timedelta(milliseconds=125),
        "host_id": "fakoli-dark",
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


@pytest.mark.parametrize(
    "field_name",
    [
        "source_timestamp",
        "collection_timestamp",
        "host_id",
        "collector_id",
        "capability_status",
    ],
)
def test_required_identity_timestamps_and_status_cannot_be_omitted(field_name):
    values = {
        "metric": "host.cpu.utilization",
        "source_timestamp": NOW,
        "collection_timestamp": NOW,
        "host_id": "fakoli-dark",
        "collector_id": "windows-host",
        "capability": "host-cpu",
        "capability_status": CapabilityStatus.OK,
    }
    del values[field_name]

    with pytest.raises(TypeError):
        TelemetrySample(**values)


@pytest.mark.parametrize("field_name", ["host_id", "collector_id", "capability"])
def test_required_text_fields_reject_empty_values(field_name):
    with pytest.raises(ValueError, match=field_name):
        _sample(**{field_name: " "})


def test_timestamps_must_be_timezone_aware_and_are_normalized_to_utc():
    with pytest.raises(ValueError, match="timezone-aware"):
        _sample(source_timestamp=datetime(2026, 7, 11, 20, 0))

    offset = timezone(timedelta(hours=-7))
    sample = _sample(source_timestamp=datetime(2026, 7, 11, 13, 0, tzinfo=offset))

    assert sample.source_timestamp == NOW
    assert sample.source_timestamp.tzinfo is timezone.utc


def test_status_enum_exposes_every_degraded_state_separately_from_ok():
    assert {status.value for status in CapabilityStatus} == {
        "ok",
        "missing",
        "unsupported",
        "permission-denied",
        "stale",
        "failed",
    }


def test_healthy_zero_is_not_conflated_with_a_missing_value():
    healthy_zero = _sample(value=0, capability_status=CapabilityStatus.OK)
    missing = _sample(value=None, capability_status=CapabilityStatus.MISSING)

    assert healthy_zero.to_dict()["value"] == 0
    assert healthy_zero.to_dict()["capability_status"] == "ok"
    assert missing.to_dict()["value"] is None
    assert missing.to_dict()["capability_status"] == "missing"


def test_freshness_preserves_source_age_clock_skew_and_stale_status():
    delayed = _sample(
        collection_timestamp=NOW + timedelta(seconds=3), stale_after_seconds=2
    )
    skewed = _sample(collection_timestamp=NOW - timedelta(milliseconds=50))
    explicit = _sample(capability_status=CapabilityStatus.STALE)

    assert delayed.age_seconds == 3
    assert delayed.is_stale is True
    assert skewed.age_seconds == pytest.approx(-0.05)
    assert explicit.is_stale is True


def test_json_round_trip_is_deterministic_and_uses_only_json_scalars():
    sample = _sample(
        detail="counter available",
        labels=(("zone", "host"), ("scope", "physical")),
    )

    payload = sample.to_json()
    restored = TelemetrySample.from_json(payload)

    assert restored == sample
    assert restored.to_json() == payload
    assert json.loads(payload)["freshness"] == {
        "age_seconds": 0.125,
        "is_stale": False,
        "stale_after_seconds": 2.0,
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_non_json_or_non_finite_values_are_rejected(value):
    with pytest.raises((TypeError, ValueError)):
        _sample(value=value)


def test_invalid_or_missing_serialized_capability_status_is_rejected():
    data = _sample().to_dict()
    data["capability_status"] = "unknown"
    with pytest.raises(ValueError, match="invalid capability_status"):
        TelemetrySample.from_dict(data)

    del data["capability_status"]
    with pytest.raises(ValueError, match="capability_status"):
        TelemetrySample.from_dict(data)


def test_boolean_schema_version_is_not_accepted_as_integer_one():
    data = _sample().to_dict()
    data["schema_version"] = True

    with pytest.raises(ValueError, match="schema_version"):
        TelemetrySample.from_dict(data)


def test_duplicate_json_object_keys_are_rejected():
    payload = _sample().to_json().replace(
        '"capability_status":"ok"',
        '"capability_status":"failed","capability_status":"ok"',
    )

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        TelemetrySample.from_json(payload)


def test_oversized_integer_is_rejected_before_serialization():
    with pytest.raises(ValueError, match="256 bits"):
        _sample(value=10**100)


def test_label_cardinality_is_bounded():
    labels = tuple((f"label-{index}", "value") for index in range(65))

    with pytest.raises(ValueError, match="64 entries"):
        _sample(labels=labels)


def test_serialized_freshness_cannot_disagree_with_timestamps():
    data = _sample().to_dict()
    data["freshness"]["age_seconds"] = 999

    with pytest.raises(ValueError, match="does not match"):
        TelemetrySample.from_dict(data)
