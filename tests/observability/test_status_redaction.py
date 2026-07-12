from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.redaction import redact_record, redact_sample
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample
from anvil_serving.observability.status import prepare_sample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _sample(**overrides: object) -> TelemetrySample:
    values = {
        "metric": "host.memory.used",
        "source_timestamp": NOW,
        "collection_timestamp": NOW,
        "host_id": "fakoli-dark",
        "collector_id": "windows-host",
        "capability": "host-memory",
        "capability_status": CapabilityStatus.OK,
        "value": 0,
        "unit": "bytes",
        "stale_after_seconds": 5.0,
        "labels": (),
        "detail": None,
    }
    values.update(overrides)
    return TelemetrySample(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "status",
    [
        CapabilityStatus.MISSING,
        CapabilityStatus.UNSUPPORTED,
        CapabilityStatus.PERMISSION_DENIED,
        CapabilityStatus.FAILED,
    ],
)
def test_degraded_capability_cannot_be_emitted_as_healthy_zero(
    status: CapabilityStatus,
) -> None:
    prepared = prepare_sample(_sample(capability_status=status, value=0))

    assert prepared.capability_status is status
    assert prepared.value is None


def test_healthy_zero_remains_a_real_measurement() -> None:
    prepared = prepare_sample(_sample(value=0))

    assert prepared.capability_status is CapabilityStatus.OK
    assert prepared.value == 0


def test_expired_ok_sample_is_explicitly_stale_and_keeps_last_value() -> None:
    prepared = prepare_sample(
        _sample(value=42, source_timestamp=NOW - timedelta(seconds=6))
    )

    assert prepared.capability_status is CapabilityStatus.STALE
    assert prepared.value == 42
    assert prepared.is_stale is True


def test_freshness_can_be_reevaluated_after_collection() -> None:
    prepared = prepare_sample(_sample(value=42), observed_at=NOW + timedelta(seconds=6))

    assert prepared.capability_status is CapabilityStatus.STALE


def test_existing_failure_is_not_reclassified_as_stale() -> None:
    prepared = prepare_sample(
        _sample(
            capability_status=CapabilityStatus.FAILED,
            value=42,
            source_timestamp=NOW - timedelta(seconds=6),
        )
    )

    assert prepared.capability_status is CapabilityStatus.FAILED
    assert prepared.value is None


def test_sample_text_and_known_secrets_are_redacted_before_serialization() -> None:
    secret = "one-off-controller-secret"
    sample = _sample(
        value=f"Bearer sk-proj-abcdefgh and {secret}",
        labels=(("api_token", "hf_abcdefghijk"), ("safe", secret)),
        detail="authorization: Bearer abcdefghijklmnop",
    )

    payload = redact_sample(sample, secrets=(secret,)).to_json()

    assert "sk-proj-abcdefgh" not in payload
    assert "hf_abcdefghijk" not in payload
    assert secret not in payload
    assert "abcdefghijklmnop" not in payload
    assert "<redacted>" in payload


def test_nested_log_records_use_the_existing_operator_redaction_contract() -> None:
    record = {
        "message": "request used x-api-key=super-secret-value",
        "credentials": {"username": "operator", "password": "bad"},
        "nested": [{"session_token": "also-bad"}],
    }

    redacted = redact_record(record)

    assert "super-secret-value" not in str(redacted)
    assert "bad" not in str(redacted)
    assert redacted["credentials"] == "<redacted>"
    assert redacted["nested"][0]["session_token"] == "<redacted>"


@pytest.mark.parametrize(
    "record, leaked",
    [
        ("collector failed password=hunter2", "hunter2"),
        ("controller token=opaque-controller-value", "opaque-controller-value"),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.signaturebytes",
            "eyJhbGciOiJIUzI1NiJ9",
        ),
    ],
)
def test_free_form_log_credentials_are_redacted(record: str, leaked: str) -> None:
    redacted = redact_record(record)

    assert leaked not in redacted
    assert "<redacted>" in redacted


def test_prepare_sample_does_not_mutate_the_input() -> None:
    original = _sample(capability_status=CapabilityStatus.MISSING, value=0)

    prepared = prepare_sample(original)

    assert original.value == 0
    assert prepared.value is None


def test_observed_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        prepare_sample(_sample(), observed_at=datetime(2026, 7, 11, 20, 0))
