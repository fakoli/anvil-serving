"""Read-only host and runtime telemetry probes."""

from datetime import datetime, timezone


def normalize_collection_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("collected_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    return value.astimezone(timezone.utc)
