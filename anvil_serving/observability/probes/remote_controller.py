"""Authenticated controller transport for capability-based remote telemetry."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from anvil_serving.transports import (
    MAX_RESPONSE_BYTES,
    ControllerTransport,
    Operation,
    TransportError,
)

from ..schema import CapabilityStatus, TelemetrySample
from ..status import prepare_sample


_CAPABILITY = re.compile(r"^[a-z][a-z0-9_-]{0,79}$")
_MAX_CAPABILITIES = 32
_MAX_SAMPLES = 4096


def collect_remote_telemetry(
    *,
    endpoint: str,
    auth_env: str,
    expected_host_id: str,
    capabilities: Sequence[str],
    environment: Mapping[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
    received_at: datetime | None = None,
) -> list[TelemetrySample]:
    """Retrieve remote samples through Anvil's authenticated controller only."""

    now = _timestamp(received_at)
    _validate_identity("expected_host_id", expected_host_id)
    requested = _validate_capabilities(capabilities)
    try:
        transport = ControllerTransport(
            endpoint,
            auth_env=auth_env,
            allowed_operations=("observability-collect",),
            environment=environment,
            timeout_seconds=10,
            max_response_bytes=MAX_RESPONSE_BYTES,
            opener=opener,
        )
        result = transport.execute(
            Operation(
                "observability-collect",
                {"capabilities": list(requested)},
                tool_name="observability_collect",
            )
        )
        payload = result.data.get("data")
        if not isinstance(payload, Mapping):
            raise ValueError("controller telemetry response has no data object")
        raw_samples = payload.get("samples")
        if not isinstance(raw_samples, list):
            raise TypeError("controller telemetry response has no samples array")
        if len(raw_samples) > _MAX_SAMPLES:
            raise ValueError(f"controller telemetry response exceeds {_MAX_SAMPLES} samples")
        if not raw_samples:
            return [
                _failure_sample(
                    now,
                    expected_host_id,
                    CapabilityStatus.MISSING,
                    "controller returned no telemetry samples",
                )
            ]
        samples: list[TelemetrySample] = []
        for raw_sample in raw_samples:
            if not isinstance(raw_sample, Mapping):
                raise TypeError("remote telemetry sample must be an object")
            sample = TelemetrySample.from_dict(raw_sample)
            if sample.host_id != expected_host_id:
                raise ValueError("remote telemetry host identity does not match configuration")
            if sample.stale_after_seconds is None:
                raise ValueError("remote telemetry sample has no freshness window")
            future_limit = now + timedelta(seconds=30)
            if (
                sample.source_timestamp > future_limit
                or sample.collection_timestamp > future_limit
            ):
                raise ValueError("remote telemetry timestamp is materially in the future")
            samples.append(prepare_sample(sample, observed_at=now))
        return samples
    except TransportError as exc:
        status = _transport_status(exc)
        return [_failure_sample(now, expected_host_id, status, str(exc))]
    except Exception as exc:
        return [
            _failure_sample(
                now, expected_host_id, CapabilityStatus.FAILED, str(exc)
            )
        ]


def _validate_capabilities(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError("capabilities must be a sequence of identifiers")
    if not value or len(value) > _MAX_CAPABILITIES:
        raise ValueError(f"capabilities must contain 1-{_MAX_CAPABILITIES} entries")
    normalized = tuple(value)
    if any(not isinstance(item, str) or not _CAPABILITY.fullmatch(item) for item in normalized):
        raise ValueError("capabilities contain an invalid identifier")
    if len(set(normalized)) != len(normalized):
        raise ValueError("capabilities must not contain duplicates")
    return normalized


def _validate_identity(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{field} must be a 1-256 character string")


def _transport_status(error: TransportError) -> CapabilityStatus:
    status = error.details.get("status")
    if error.code == "missing_controller_token" or status in {401, 403}:
        return CapabilityStatus.PERMISSION_DENIED
    return CapabilityStatus.FAILED


def _failure_sample(
    now: datetime,
    host_id: str,
    status: CapabilityStatus,
    detail: str,
) -> TelemetrySample:
    return prepare_sample(
        TelemetrySample(
            metric="remote.collector.status",
            source_timestamp=now,
            collection_timestamp=now,
            host_id=host_id,
            collector_id="authenticated-controller",
            capability="remote-telemetry",
            capability_status=status,
            value=None,
            stale_after_seconds=10.0,
            labels=(("transport", "controller"),),
            detail=detail[:4096],
        )
    )


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("received_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("received_at must be timezone-aware")
    return value.astimezone(timezone.utc)
