"""Read-only per-container telemetry through the Docker Engine CLI client."""

from __future__ import annotations

import json
import math
import platform
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from ..schema import CapabilityStatus, TelemetrySample
from ..status import prepare_sample


DockerProvider = Callable[
    [], tuple[Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]]
]
_MAX_CONTAINERS = 256
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_BYTE_VALUE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B)$", re.IGNORECASE)
_BYTE_FACTORS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}


def collect_containers(
    *,
    provider: DockerProvider | None = None,
    host_id: str | None = None,
    collected_at: datetime | None = None,
) -> list[TelemetrySample]:
    """Collect container state and resources without changing engine state."""

    now = _timestamp(collected_at)
    resolved_host = host_id or platform.node() or "docker-host"
    try:
        inspections, stats_rows = (provider or _read_docker)()
        if isinstance(inspections, (str, bytes)) or not isinstance(inspections, Sequence):
            raise TypeError("Docker inspections must be a sequence")
        if isinstance(stats_rows, (str, bytes)) or not isinstance(stats_rows, Sequence):
            raise TypeError("Docker stats must be a sequence")
        if len(inspections) > _MAX_CONTAINERS or len(stats_rows) > _MAX_CONTAINERS:
            raise ValueError(f"Docker collector exceeds {_MAX_CONTAINERS} containers")
    except PermissionError as exc:
        return [_collector_status(now, resolved_host, CapabilityStatus.PERMISSION_DENIED, exc)]
    except Exception as exc:
        return [_collector_status(now, resolved_host, CapabilityStatus.FAILED, exc)]

    stats_by_id = {
        str(row.get("ID", "")): row
        for row in stats_rows
        if isinstance(row, Mapping) and row.get("ID")
    }
    samples: list[TelemetrySample] = []
    for inspection in inspections:
        if not isinstance(inspection, Mapping):
            continue
        samples.extend(_container_samples(inspection, stats_by_id, now, resolved_host))
    return samples


def _read_docker() -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    ids = [line.strip() for line in _run_docker(["container", "ls", "-aq"]).splitlines() if line.strip()]
    if len(ids) > _MAX_CONTAINERS:
        raise ValueError(f"Docker collector exceeds {_MAX_CONTAINERS} containers")
    if not ids:
        return [], []
    inspections = _json_array(_run_docker(["inspect", *ids]), "docker inspect")
    stats_text = _run_docker(
        ["stats", "--no-stream", "--no-trunc", "--format", "{{json .}}", *ids]
    )
    stats_rows = [
        _json_object(line, "docker stats")
        for line in stats_text.splitlines()
        if line.strip()
    ]
    return inspections, stats_rows


def _run_docker(arguments: list[str]) -> str:
    completed = subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "Docker Engine query failed"
        lowered = message.lower()
        if "permission denied" in lowered or "access is denied" in lowered:
            raise PermissionError(message)
        raise OSError(message)
    if len(completed.stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise ValueError("Docker Engine output exceeds 4 MiB")
    return completed.stdout


def _container_samples(
    inspection: Mapping[str, Any],
    stats_by_id: Mapping[str, Mapping[str, Any]],
    now: datetime,
    host_id: str,
) -> list[TelemetrySample]:
    container_id = str(inspection.get("Id") or "unknown")
    name = str(inspection.get("Name") or "unknown").lstrip("/") or "unknown"
    labels = (("container_id", container_id[:1024]), ("container_name", name[:1024]))
    state = inspection.get("State")
    status = state.get("Status") if isinstance(state, Mapping) else None
    stats = _find_stats(stats_by_id, container_id)
    samples = [
        _sample(now, host_id, labels, "container.identity", "identity", name, None),
        _text_metric(now, host_id, labels, "container.status", "status", status),
    ]
    samples.extend(
        (
            _stat_metric(
                now,
                host_id,
                labels,
                stats,
                "CPUPerc",
                "container.cpu.utilization",
                "cpu",
                "percent",
                _percent,
            ),
            _stat_pair_metric(
                now,
                host_id,
                labels,
                stats,
                "MemUsage",
                0,
                "container.memory.used",
                "memory",
            ),
            _stat_pair_metric(
                now,
                host_id,
                labels,
                stats,
                "NetIO",
                0,
                "container.network.received",
                "network",
            ),
            _stat_pair_metric(
                now,
                host_id,
                labels,
                stats,
                "NetIO",
                1,
                "container.network.sent",
                "network",
            ),
            _stat_pair_metric(
                now,
                host_id,
                labels,
                stats,
                "BlockIO",
                0,
                "container.storage.read",
                "storage-io",
            ),
            _stat_pair_metric(
                now,
                host_id,
                labels,
                stats,
                "BlockIO",
                1,
                "container.storage.written",
                "storage-io",
            ),
        )
    )
    assignment = _gpu_assignment(inspection)
    samples.append(
        _sample(
            now,
            host_id,
            labels + (("attribution", "configured"),),
            "container.gpu.assignment",
            "configured-gpu",
            assignment,
            None,
        )
    )
    samples.append(
        _sample(
            now,
            host_id,
            labels + (("attribution", "unavailable"),),
            "container.gpu.memory.used",
            "gpu-consumption",
            None,
            "bytes",
            status=CapabilityStatus.UNSUPPORTED,
            detail="Docker stats does not expose reliable per-container GPU memory attribution",
        )
    )
    return samples


def _find_stats(
    stats_by_id: Mapping[str, Mapping[str, Any]], container_id: str
) -> Mapping[str, Any] | None:
    if container_id in stats_by_id:
        return stats_by_id[container_id]
    matches = [row for key, row in stats_by_id.items() if container_id.startswith(key)]
    return matches[0] if len(matches) == 1 else None


def _text_metric(
    now: datetime,
    host_id: str,
    labels: tuple[tuple[str, str], ...],
    metric: str,
    capability: str,
    value: object,
) -> TelemetrySample:
    if isinstance(value, str) and value.strip():
        return _sample(now, host_id, labels, metric, capability, value[: 64 * 1024], None)
    return _sample(
        now,
        host_id,
        labels,
        metric,
        capability,
        None,
        None,
        status=CapabilityStatus.MISSING,
        detail=f"Docker inspect did not expose {metric}",
    )


def _stat_pair_metric(
    now: datetime,
    host_id: str,
    labels: tuple[tuple[str, str], ...],
    stats: Mapping[str, Any] | None,
    field: str,
    index: int,
    metric: str,
    capability: str,
) -> TelemetrySample:
    return _stat_metric(
        now,
        host_id,
        labels,
        stats,
        field,
        metric,
        capability,
        "bytes",
        lambda raw: _bytes(_pair(raw, index)),
    )


def _stat_metric(
    now: datetime,
    host_id: str,
    labels: tuple[tuple[str, str], ...],
    stats: Mapping[str, Any] | None,
    field: str,
    metric: str,
    capability: str,
    unit: str,
    parser: Callable[[object], int | float],
) -> TelemetrySample:
    try:
        if stats is None or field not in stats:
            raise ValueError(f"Docker stats did not expose {field}")
        value = parser(stats[field])
        return _sample(now, host_id, labels, metric, capability, value, unit)
    except (TypeError, ValueError) as exc:
        return _sample(
            now,
            host_id,
            labels,
            metric,
            capability,
            None,
            unit,
            status=CapabilityStatus.MISSING,
            detail=str(exc),
        )


def _gpu_assignment(inspection: Mapping[str, Any]) -> str:
    host_config = inspection.get("HostConfig")
    requests = host_config.get("DeviceRequests") if isinstance(host_config, Mapping) else None
    if not isinstance(requests, list):
        return "none"
    assignments: list[str] = []
    for request in requests:
        if not isinstance(request, Mapping):
            continue
        capabilities = request.get("Capabilities")
        if "gpu" not in str(capabilities).lower():
            continue
        device_ids = request.get("DeviceIDs")
        if isinstance(device_ids, list) and device_ids:
            assignments.extend(str(item) for item in device_ids[:64])
        elif request.get("Count") == -1:
            assignments.append("all")
        else:
            assignments.append(str(request.get("Count", "configured")))
    return ",".join(assignments)[: 64 * 1024] if assignments else "none"


def _pair(value: object, index: int) -> str:
    if not isinstance(value, str):
        raise TypeError("Docker stats pair is not text")
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 2:
        raise ValueError("Docker stats pair is malformed")
    return parts[index]


def _percent(value: object) -> float:
    if not isinstance(value, str) or not value.strip().endswith("%"):
        raise ValueError("Docker CPU percentage is malformed")
    result = float(value.strip()[:-1])
    if not math.isfinite(result) or result < 0:
        raise ValueError("Docker CPU percentage is invalid")
    return result


def _bytes(value: object) -> int | float:
    if not isinstance(value, str):
        raise TypeError("Docker byte value is not text")
    match = _BYTE_VALUE.fullmatch(value.strip())
    if match is None:
        raise ValueError("Docker byte value is malformed")
    result = float(match.group(1)) * _BYTE_FACTORS[match.group(2).lower()]
    if not math.isfinite(result) or result < 0:
        raise ValueError("Docker byte value is invalid")
    return int(result) if result.is_integer() else result


def _sample(
    now: datetime,
    host_id: str,
    labels: tuple[tuple[str, str], ...],
    metric: str,
    capability: str,
    value: int | float | str | None,
    unit: str | None,
    *,
    status: CapabilityStatus = CapabilityStatus.OK,
    detail: str | None = None,
) -> TelemetrySample:
    return prepare_sample(
        TelemetrySample(
            metric=metric,
            source_timestamp=now,
            collection_timestamp=now,
            host_id=host_id,
            collector_id="docker-engine",
            capability=capability,
            capability_status=status,
            value=value,
            unit=unit,
            stale_after_seconds=5.0,
            labels=labels,
            detail=detail[:4096] if detail is not None else None,
        )
    )


def _collector_status(
    now: datetime, host_id: str, status: CapabilityStatus, error: BaseException
) -> TelemetrySample:
    return _sample(
        now,
        host_id,
        (),
        "container.collector.status",
        "docker-engine",
        None,
        None,
        status=status,
        detail=str(error),
    )


def _json_array(payload: str, source: str) -> list[Mapping[str, Any]]:
    value = json.loads(payload)
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise TypeError(f"{source} did not return an object array")
    return value


def _json_object(payload: str, source: str) -> Mapping[str, Any]:
    value = json.loads(payload)
    if not isinstance(value, Mapping):
        raise TypeError(f"{source} did not return an object")
    return value


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("collected_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    return value.astimezone(timezone.utc)
