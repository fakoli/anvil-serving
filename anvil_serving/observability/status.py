"""Canonical degraded and stale-state handling for telemetry samples."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone

from .redaction import redact_sample
from .schema import CapabilityStatus, TelemetrySample


_VALUELESS_STATUSES = frozenset(
    {
        CapabilityStatus.MISSING,
        CapabilityStatus.UNSUPPORTED,
        CapabilityStatus.PERMISSION_DENIED,
        CapabilityStatus.FAILED,
    }
)


def prepare_sample(
    sample: TelemetrySample,
    *,
    observed_at: datetime | None = None,
    secrets: Sequence[str] = (),
) -> TelemetrySample:
    """Apply freshness, degraded-value, and redaction policy before emission.

    Missing or failed capabilities never retain a numeric value that a consumer
    could mistake for a healthy zero. Stale samples retain their last observed
    value, but carry an explicit ``stale`` capability status.
    """

    status = sample.capability_status
    value = sample.value
    if status in _VALUELESS_STATUSES:
        value = None
    elif status is CapabilityStatus.OK and _is_past_freshness(sample, observed_at):
        status = CapabilityStatus.STALE
    return redact_sample(
        replace(sample, capability_status=status, value=value), secrets=secrets
    )


def _is_past_freshness(
    sample: TelemetrySample, observed_at: datetime | None
) -> bool:
    if sample.stale_after_seconds is None:
        return False
    when = sample.collection_timestamp if observed_at is None else _utc(observed_at)
    return (when - sample.source_timestamp).total_seconds() > sample.stale_after_seconds


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("observed_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc)
