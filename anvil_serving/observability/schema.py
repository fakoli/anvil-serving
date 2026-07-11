"""Capability-aware telemetry sample contract shared by observability probes."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeAlias


SCHEMA_VERSION = 1
SampleValue: TypeAlias = bool | int | float | str | None
_MAX_JSON_BYTES = 1024 * 1024
_MAX_TEXT_CHARS = 256
_MAX_DETAIL_CHARS = 4096
_MAX_VALUE_TEXT_CHARS = 64 * 1024
_MAX_INTEGER_BITS = 256
_MAX_LABELS = 64
_MAX_LABEL_VALUE_CHARS = 1024
_SAMPLE_FIELDS = frozenset(
    {
        "schema_version",
        "metric",
        "source_timestamp",
        "collection_timestamp",
        "host_id",
        "collector_id",
        "capability",
        "capability_status",
        "value",
        "unit",
        "freshness",
        "labels",
        "detail",
    }
)
_FRESHNESS_FIELDS = frozenset({"age_seconds", "stale_after_seconds", "is_stale"})


class CapabilityStatus(str, Enum):
    """Whether a collector can provide a trustworthy value for a capability."""

    OK = "ok"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    PERMISSION_DENIED = "permission-denied"
    STALE = "stale"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    """One normalized metric observation returned by an observability probe.

    Timestamps are normalized to UTC. ``source_timestamp`` identifies when the
    underlying source observed the value; ``collection_timestamp`` identifies
    when Anvil received it. Keeping both is required for cross-host alignment.
    """

    metric: str
    source_timestamp: datetime
    collection_timestamp: datetime
    host_id: str
    collector_id: str
    capability: str
    capability_status: CapabilityStatus
    value: SampleValue = None
    unit: str | None = None
    stale_after_seconds: float | None = None
    labels: tuple[tuple[str, str], ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("metric", "host_id", "collector_id", "capability"):
            _require_text(field_name, getattr(self, field_name))
        if not isinstance(self.capability_status, CapabilityStatus):
            raise TypeError("capability_status must be a CapabilityStatus")

        object.__setattr__(
            self,
            "source_timestamp",
            _normalize_timestamp("source_timestamp", self.source_timestamp),
        )
        object.__setattr__(
            self,
            "collection_timestamp",
            _normalize_timestamp("collection_timestamp", self.collection_timestamp),
        )
        _validate_value(self.value)

        if self.unit is not None:
            _require_text("unit", self.unit)
        if self.detail is not None and not isinstance(self.detail, str):
            raise TypeError("detail must be a string or None")
        if self.detail is not None and len(self.detail) > _MAX_DETAIL_CHARS:
            raise ValueError(f"detail must not exceed {_MAX_DETAIL_CHARS} characters")
        if self.stale_after_seconds is not None:
            if isinstance(self.stale_after_seconds, bool) or not isinstance(
                self.stale_after_seconds, (int, float)
            ):
                raise TypeError("stale_after_seconds must be a non-negative number or None")
            if not math.isfinite(self.stale_after_seconds) or self.stale_after_seconds < 0:
                raise ValueError("stale_after_seconds must be a finite non-negative number")
            object.__setattr__(self, "stale_after_seconds", float(self.stale_after_seconds))

        object.__setattr__(self, "labels", _normalize_labels(self.labels))

    @property
    def age_seconds(self) -> float:
        """Return source-to-collection age, preserving negative clock skew."""

        return (self.collection_timestamp - self.source_timestamp).total_seconds()

    @property
    def is_stale(self) -> bool:
        """Return whether status or the declared freshness window marks the sample stale."""

        return self.capability_status is CapabilityStatus.STALE or (
            self.stale_after_seconds is not None and self.age_seconds > self.stale_after_seconds
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-compatible representation."""

        return {
            "schema_version": SCHEMA_VERSION,
            "metric": self.metric,
            "source_timestamp": _format_timestamp(self.source_timestamp),
            "collection_timestamp": _format_timestamp(self.collection_timestamp),
            "host_id": self.host_id,
            "collector_id": self.collector_id,
            "capability": self.capability,
            "capability_status": self.capability_status.value,
            "value": self.value,
            "unit": self.unit,
            "freshness": {
                "age_seconds": self.age_seconds,
                "stale_after_seconds": self.stale_after_seconds,
                "is_stale": self.is_stale,
            },
            "labels": {key: value for key, value in self.labels},
            "detail": self.detail,
        }

    def to_json(self) -> str:
        """Serialize the sample with deterministic standard-library JSON."""

        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TelemetrySample:
        """Parse a sample from its stable mapping representation."""

        if not isinstance(data, Mapping):
            raise TypeError("telemetry sample must be a mapping")
        unknown = set(data) - _SAMPLE_FIELDS
        if unknown:
            raise ValueError(f"unknown telemetry sample field(s): {', '.join(sorted(unknown))}")
        if type(data.get("schema_version")) is not int or data["schema_version"] != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
        freshness = data.get("freshness")
        if not isinstance(freshness, Mapping):
            raise TypeError("freshness must be a mapping")
        unknown_freshness = set(freshness) - _FRESHNESS_FIELDS
        if unknown_freshness:
            raise ValueError(
                "unknown freshness field(s): " + ", ".join(sorted(unknown_freshness))
            )
        missing_freshness = _FRESHNESS_FIELDS - set(freshness)
        if missing_freshness:
            raise ValueError(
                "missing freshness field(s): " + ", ".join(sorted(missing_freshness))
            )
        labels = data.get("labels", {})
        if not isinstance(labels, Mapping):
            raise TypeError("labels must be a mapping")
        try:
            status = CapabilityStatus(data["capability_status"])
        except KeyError as exc:
            raise ValueError("missing required field: capability_status") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid capability_status") from exc

        required = (
            "metric",
            "source_timestamp",
            "collection_timestamp",
            "host_id",
            "collector_id",
            "capability",
        )
        missing = [field_name for field_name in required if field_name not in data]
        if missing:
            raise ValueError(f"missing required field(s): {', '.join(missing)}")

        sample = cls(
            metric=data["metric"],
            source_timestamp=_parse_timestamp("source_timestamp", data["source_timestamp"]),
            collection_timestamp=_parse_timestamp(
                "collection_timestamp", data["collection_timestamp"]
            ),
            host_id=data["host_id"],
            collector_id=data["collector_id"],
            capability=data["capability"],
            capability_status=status,
            value=data.get("value"),
            unit=data.get("unit"),
            stale_after_seconds=freshness.get("stale_after_seconds"),
            labels=tuple(labels.items()),
            detail=data.get("detail"),
        )
        if isinstance(freshness["age_seconds"], bool) or not isinstance(
            freshness["age_seconds"], (int, float)
        ):
            raise TypeError("freshness.age_seconds must be a number")
        if not math.isclose(
            float(freshness["age_seconds"]), sample.age_seconds, rel_tol=0, abs_tol=1e-9
        ):
            raise ValueError("freshness.age_seconds does not match the sample timestamps")
        if type(freshness["is_stale"]) is not bool:
            raise TypeError("freshness.is_stale must be a boolean")
        if freshness["is_stale"] is not sample.is_stale:
            raise ValueError("freshness.is_stale does not match the sample timestamps and status")
        return sample

    @classmethod
    def from_json(cls, payload: str) -> TelemetrySample:
        """Parse a sample from JSON using only the standard library."""

        if not isinstance(payload, str):
            raise TypeError("telemetry JSON payload must be a string")
        if len(payload.encode("utf-8")) > _MAX_JSON_BYTES:
            raise ValueError(f"telemetry JSON payload must not exceed {_MAX_JSON_BYTES} bytes")
        data = json.loads(payload, object_pairs_hook=_unique_object)
        if not isinstance(data, Mapping):
            raise TypeError("telemetry JSON payload must contain an object")
        return cls.from_dict(data)


def _require_text(field_name: str, value: object) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if len(value) > _MAX_TEXT_CHARS:
        raise ValueError(f"{field_name} must not exceed {_MAX_TEXT_CHARS} characters")


def _normalize_timestamp(field_name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(field_name: str, value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    return _normalize_timestamp(field_name, parsed)


def _validate_value(value: object) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if value.bit_length() > _MAX_INTEGER_BITS:
            raise ValueError(f"integer value must not exceed {_MAX_INTEGER_BITS} bits")
        return
    if isinstance(value, str):
        if len(value) > _MAX_VALUE_TEXT_CHARS:
            raise ValueError(f"string value must not exceed {_MAX_VALUE_TEXT_CHARS} characters")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return
    raise TypeError("value must be a JSON scalar or None")


def _normalize_labels(labels: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(labels, tuple):
        raise TypeError("labels must be a tuple of string pairs")
    if len(labels) > _MAX_LABELS:
        raise ValueError(f"labels must not contain more than {_MAX_LABELS} entries")
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in labels:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError("labels must be a tuple of string pairs")
        key, value = item
        _require_text("label key", key)
        if not isinstance(value, str):
            raise TypeError("label values must be strings")
        if len(value) > _MAX_LABEL_VALUE_CHARS:
            raise ValueError(
                f"label values must not exceed {_MAX_LABEL_VALUE_CHARS} characters"
            )
        if key in seen:
            raise ValueError(f"duplicate label key: {key!r}")
        seen.add(key)
        normalized.append((key, value))
    return tuple(sorted(normalized))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result
