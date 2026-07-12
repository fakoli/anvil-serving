"""Project bounded retained telemetry into chart-ready series with real gaps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..retention import RetentionStore


CORE_SIGNALS: dict[str, tuple[str, ...]] = {
    "cpu": ("host.cpu.utilization",),
    "physical-memory": ("host.memory.used",),
    "paging-swap": ("host.paging.rate", "host.swap.used"),
    "wsl-docker-memory": ("boundary.memory.used",),
    "gpu-utilization": ("gpu.utilization",),
    "dedicated-vram": ("gpu.memory.used",),
    "shared-gpu-memory": ("gpu.memory.shared.used",),
    "wsl-docker-cpu": ("boundary.cpu.utilization",),
    "disk": ("host.disk.throughput",),
    "network": ("host.network.throughput",),
}


def retained_timeseries(
    store: RetentionStore,
    *,
    resolution_seconds: int = 0,
    signals: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return chart points; ``None`` entries are intentional visible gaps."""

    requested = tuple(CORE_SIGNALS) if signals is None else _signals(signals)
    frames = store.frames(resolution_seconds)
    output: dict[str, list[dict[str, Any]]] = {}
    for signal in requested:
        metrics = frozenset(CORE_SIGNALS[signal])
        series: dict[str, dict[str, Any]] = {}
        known_keys: set[str] = set()
        for frame in frames:
            frame_group = frame.snapshot.get("sampling_group")
            timestamp = frame.observed_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
            samples = frame.snapshot.get("samples", [])
            present: set[str] = set()
            if isinstance(samples, list):
                for sample in samples:
                    if not isinstance(sample, Mapping) or sample.get("metric") not in metrics:
                        continue
                    value = sample.get("value")
                    if not _numeric(value) or sample.get("capability_status", "ok") != "ok":
                        continue
                    key = _series_key(sample)
                    present.add(key)
                    known_keys.add(key)
                    item = series.setdefault(
                        key,
                        {
                            "host_id": sample.get("host_id", "unknown-host"),
                            "metric": sample.get("metric"),
                            "unit": sample.get("unit"),
                            "labels": dict(sample.get("labels") or {}),
                            "points": [],
                            "sampling_group": frame_group,
                        },
                    )
                    if frame.gap and item["points"] and item["points"][-1] is not None:
                        item["points"].append(None)
                    item["points"].append([timestamp, value])
            for key in known_keys - present:
                if frame_group is not None and series[key].get("sampling_group") != frame_group:
                    continue
                points = series[key]["points"]
                if points and points[-1] is not None:
                    points.append(None)
        output[signal] = sorted(
            series.values(),
            key=lambda item: (item["host_id"], item["metric"], str(item["labels"])),
        )
        for item in output[signal]:
            item.pop("sampling_group", None)
    return {
        "resolution_seconds": resolution_seconds,
        "retained_bytes": store.total_bytes,
        "signals": output,
    }


def _signals(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ValueError("signals must be a non-empty sequence")
    result = tuple(value)
    if len(result) > len(CORE_SIGNALS) or any(item not in CORE_SIGNALS for item in result):
        raise ValueError("signals contain an unknown core signal")
    if len(set(result)) != len(result):
        raise ValueError("signals must not contain duplicates")
    return result


def _series_key(sample: Mapping[str, Any]) -> str:
    labels = sample.get("labels") or {}
    if not isinstance(labels, Mapping):
        labels = {}
    return "|".join(
        (str(sample.get("host_id")), str(sample.get("metric")), repr(sorted(labels.items())))
    )


def _numeric(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
