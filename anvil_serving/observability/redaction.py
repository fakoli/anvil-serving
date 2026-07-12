"""Secret-safe normalization for telemetry samples and collector records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from anvil_serving.operator_output import redact

from .schema import TelemetrySample


def redact_record(value: Any, *, secrets: Sequence[str] = ()) -> Any:
    """Return a recursively redacted collector or log record."""

    return redact(value, secrets=secrets)


def redact_sample(
    sample: TelemetrySample, *, secrets: Sequence[str] = ()
) -> TelemetrySample:
    """Return an immutable telemetry sample with all text fields redacted."""

    labels = redact(dict(sample.labels), secrets=secrets)
    if not isinstance(labels, Mapping):  # pragma: no cover - defensive contract guard
        raise TypeError("redacted telemetry labels must remain a mapping")
    return replace(
        sample,
        metric=_redacted_text(sample.metric, secrets),
        host_id=_redacted_text(sample.host_id, secrets),
        collector_id=_redacted_text(sample.collector_id, secrets),
        capability=_redacted_text(sample.capability, secrets),
        value=(
            _redacted_text(sample.value, secrets)
            if isinstance(sample.value, str)
            else sample.value
        ),
        unit=(
            _redacted_text(sample.unit, secrets) if sample.unit is not None else None
        ),
        labels=tuple((str(key), str(value)) for key, value in labels.items()),
        detail=(
            _redacted_text(sample.detail, secrets)
            if sample.detail is not None
            else None
        ),
    )


def _redacted_text(value: str, secrets: Sequence[str]) -> str:
    redacted = redact(value, secrets=secrets)
    if not isinstance(redacted, str):  # pragma: no cover - defensive contract guard
        raise TypeError("redacted telemetry text must remain a string")
    return redacted
