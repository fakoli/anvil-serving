"""Derive conservative operator indicators from normalized telemetry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..retention import RetentionStore


def build_indicators(
    snapshot: Mapping[str, Any], *, retention: RetentionStore | None = None
) -> dict[str, Any]:
    samples = snapshot.get("samples", [])
    if not isinstance(samples, list):
        raise TypeError("telemetry snapshot samples must be an array")
    normalized = [sample for sample in samples if isinstance(sample, Mapping)]
    return {
        "pressure": _pressure(normalized),
        "health": _health(normalized),
        "ownership": _ownership(normalized),
        "freshness": _freshness(normalized),
        "model_loading": _model_loading(normalized, retention),
    }


def _pressure(samples: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for sample in samples:
        metric = str(sample.get("metric", ""))
        if metric not in {
            "host.memory.pressure",
            "host.paging.rate",
            "host.swap.used",
            "boundary.memory.used",
            "gpu.memory.used",
            "gpu.memory.shared.used",
        }:
            continue
        value = sample.get("value")
        severity = "unknown"
        if _number(value):
            if sample.get("unit") == "percent":
                severity = "critical" if value >= 90 else "warning" if value >= 75 else "normal"
            else:
                severity = "active" if value > 0 else "normal"
        output.append(_base(sample, severity=severity))
    return output


def _health(samples: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **_base(sample),
            "component": _labels(sample).get("component", "unknown"),
            "health": _labels(sample).get("health", "unknown"),
            "served_identity": _labels(sample).get("served_identity", "unknown"),
            "owning_host": _labels(sample).get("owning_host", sample.get("host_id")),
        }
        for sample in samples
        if sample.get("metric") == "service.health"
    ]


def _ownership(samples: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for sample in samples:
        labels = _labels(sample)
        owner = (
            labels.get("container_name")
            or labels.get("served_identity")
            or labels.get("process")
            or labels.get("component")
        )
        if not owner or owner in {"unknown", "not-configured"}:
            continue
        attribution = labels.get("attribution", "inferred")
        reliability = (
            "reliable" if attribution in {"configured", "direct", "reliable"} else "inferred"
        )
        output.append({**_base(sample), "owner": owner, "attribution": reliability})
    return output


def _freshness(samples: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for sample in samples:
        freshness = sample.get("freshness")
        stale = sample.get("capability_status") == "stale" or (
            isinstance(freshness, Mapping) and freshness.get("is_stale") is True
        )
        if stale:
            output.append({**_base(sample), "stale": True})
    return output


def _model_loading(
    samples: list[Mapping[str, Any]], retention: RetentionStore | None
) -> dict[str, Any]:
    current = _loading_values(samples)
    previous: dict[str, float] = {}
    if retention is not None:
        frames = retention.frames(0)
        if len(frames) >= 2:
            prior_samples = frames[-2].snapshot.get("samples", [])
            if isinstance(prior_samples, list):
                previous = _loading_values(
                    [sample for sample in prior_samples if isinstance(sample, Mapping)]
                )
    host_delta = current.get("host_memory", 0) - previous.get(
        "host_memory", current.get("host_memory", 0)
    )
    shared_delta = current.get("shared_gpu_memory", 0) - previous.get(
        "shared_gpu_memory", current.get("shared_gpu_memory", 0)
    )
    vram_delta = current.get("dedicated_vram", 0) - previous.get(
        "dedicated_vram", current.get("dedicated_vram", 0)
    )
    if shared_delta < 0 and vram_delta > 0:
        phase = "shared-to-vram"
    elif shared_delta > 0:
        phase = "host-to-shared"
    elif host_delta > 0 and current.get("dedicated_vram", 0) == 0:
        phase = "host-staging"
    elif current.get("dedicated_vram", 0) > 0:
        phase = "vram-resident"
    else:
        phase = "unknown"
    return {
        "phase": phase,
        "attribution": "inferred",
        "host_memory": current.get("host_memory"),
        "shared_gpu_memory": current.get("shared_gpu_memory"),
        "dedicated_vram": current.get("dedicated_vram"),
    }


def _loading_values(samples: list[Mapping[str, Any]]) -> dict[str, float]:
    metrics = {
        "host.memory.used": "host_memory",
        "gpu.memory.shared.used": "shared_gpu_memory",
        "gpu.memory.used": "dedicated_vram",
    }
    gpu_hosts = {
        sample.get("host_id")
        for sample in samples
        if sample.get("metric") in {"gpu.memory.shared.used", "gpu.memory.used"}
    }
    output: dict[str, float] = {}
    for sample in samples:
        key = metrics.get(sample.get("metric"))
        value = sample.get("value")
        if key == "host_memory" and gpu_hosts and sample.get("host_id") not in gpu_hosts:
            continue
        if key and _number(value) and sample.get("capability_status", "ok") == "ok":
            output[key] = output.get(key, 0) + float(value)
    return output


def _base(sample: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "host_id": sample.get("host_id"),
        "metric": sample.get("metric"),
        "value": sample.get("value"),
        "unit": sample.get("unit"),
        "status": sample.get("capability_status"),
        "labels": _labels(sample),
        **extra,
    }


def _labels(sample: Mapping[str, Any]) -> dict[str, Any]:
    labels = sample.get("labels")
    return dict(labels) if isinstance(labels, Mapping) else {}


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
