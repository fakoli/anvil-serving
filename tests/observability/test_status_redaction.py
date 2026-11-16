from __future__ import annotations

from datetime import datetime, timezone

import pytest

from anvil_serving.observability.redaction import redact_record, redact_sample
from anvil_serving.observability.schema import CapabilityStatus, TelemetrySample


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _sample(**overrides: object) -> TelemetrySample:
    values = {
        "metric": "host.memory.used",
        "source_timestamp": NOW,
        "collection_timestamp": NOW,
        "host_id": "primary-node",
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
