"""Small, strict browser projections; no controller or lifecycle authority."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z")
DIGEST = re.compile(r"[a-f0-9]{64}\Z")
METRIC_STATUSES = frozenset({"fresh", "stale", "unavailable", "unsupported", "insufficient_data"})


class ObservatoryError(ValueError):
    """An explicitly safe public error, never an upstream exception string."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def identifier(value: object) -> str:
    if type(value) is not str or not IDENTIFIER.fullmatch(value):
        raise ObservatoryError("invalid_identifier", "Select a declared resource.")
    return value


def finite(value: object) -> bool:
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def present_metric(value: object, *, source_status: str, unit: str) -> dict:
    if type(source_status) is not str or source_status not in METRIC_STATUSES:
        raise ValueError("invalid metric status")
    if type(unit) is not str or not re.fullmatch(r"[a-zA-Z%][a-zA-Z0-9 /%_.-]{0,47}", unit):
        raise ValueError("invalid metric unit")
    if value is not None and not finite(value):
        raise ValueError("metric value must be finite numeric or missing")
    status = "unavailable" if source_status == "fresh" and value is None else source_status
    return {"value": value if status == "fresh" else None,
            "last_known_value": value if status == "stale" else None,
            "status": status, "unit": unit}


def preview_is_current(preview: object, current: object, *, now_epoch_seconds: object) -> bool:
    if type(preview) is not dict or type(current) is not dict or not finite(now_epoch_seconds):
        return False
    expiry = preview.get("expires_at_epoch_seconds")
    if not finite(expiry) or expiry <= now_epoch_seconds:
        return False
    for key in ("host_id", "resource_id", "action_id", "baseline_digest", "candidate_digest", "policy_digest"):
        value = preview.get(key)
        pattern = DIGEST if key.endswith("digest") else IDENTIFIER
        if type(value) is not str or not pattern.fullmatch(value) or current.get(key) != value:
            return False
    return True


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ObservatoryError("invalid_json", "Values must be finite JSON data.") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        canonical(value)
        return value
    except (ValueError, TypeError, RecursionError) as exc:
        raise ObservatoryError("invalid_json", "Use an unambiguous JSON object with finite values.") from exc


def fields(value: object, *, required=(), optional=()) -> dict:
    if type(value) is not dict or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ObservatoryError("invalid_fields", "The request has missing or unsupported fields.")
    return value


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate_values(values: object, settings: list[dict]) -> dict:
    """Validate a complete candidate against authoritative bounded descriptors."""
    if type(values) is not dict or len(values) > 32:
        raise ObservatoryError("invalid_candidate", "Use a bounded configuration candidate.")
    descriptors = {s["setting_id"]: s for s in settings if s.get("support") == "supported"}
    if set(values) - set(descriptors):
        raise ObservatoryError("invalid_candidate", "The candidate contains unsupported settings.")
    complete = {key: values.get(key, s.get("configured")) for key, s in descriptors.items()}
    for key, value in complete.items():
        item = descriptors[key]
        kind, limits = item["value_type"], item.get("constraints", {})
        valid = ((kind == "integer" and type(value) is int) or
                 (kind == "number" and finite(value)) or
                 (kind == "boolean" and type(value) is bool) or
                 (kind == "string" and type(value) is str and len(value) <= 256))
        if not valid:
            raise ObservatoryError("invalid_candidate", f"{item['label']} has an invalid value.")
        if kind in ("number", "integer") and (
            ("minimum" in limits and value < limits["minimum"]) or
            ("maximum" in limits and value > limits["maximum"])):
            raise ObservatoryError("invalid_candidate", f"{item['label']} is outside its supported range.")
        if "choices" in limits and value not in limits["choices"]:
            raise ObservatoryError("invalid_candidate", f"Select a supported {item['label']} value.")
    canonical(complete)
    return complete
