"""Windows boundary telemetry for WSL virtualization and Docker workloads."""

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


RowsProvider = Callable[[], Sequence[Mapping[str, Any]]]
_MAX_WSL_PROCESSES = 64
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
_WSL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$rows = @(Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
    Where-Object Name -like 'vmmem*' |
    ForEach-Object {
        [ordered]@{
            name = $_.Name
            cpu_percent = $_.PercentProcessorTime
            memory_used_bytes = $_.WorkingSetPrivate
        }
    })
ConvertTo-Json -InputObject $rows -Compress
""".strip()


class _BoundaryMissingError(ValueError):
    """The boundary is valid but is not currently observable."""


def collect_wsl_docker_boundaries(
    *,
    wsl_provider: RowsProvider | None = None,
    docker_provider: RowsProvider | None = None,
    host_id: str | None = None,
    collected_at: datetime | None = None,
) -> list[TelemetrySample]:
    """Collect distinct WSL-envelope and Docker-container aggregate samples."""

    now = _timestamp(collected_at)
    resolved_host = host_id or platform.node() or "windows-host"
    if platform.system() != "Windows" and wsl_provider is None and docker_provider is None:
        return _boundary_degraded(
            now,
            resolved_host,
            "wsl-vm",
            "windows-perfproc-vmmem",
            "ambiguous",
            CapabilityStatus.UNSUPPORTED,
            "WSL/Docker Desktop boundary metrics require Windows",
        ) + _boundary_degraded(
            now,
            resolved_host,
            "docker-engine",
            "docker-stats-container-aggregate",
            "inferred",
            CapabilityStatus.UNSUPPORTED,
            "WSL/Docker Desktop boundary metrics require Windows",
        )

    return _collect_boundary(
        now,
        resolved_host,
        "wsl-vm",
        "windows-perfproc-vmmem",
        "ambiguous",
        wsl_provider or _read_wsl_rows,
        _aggregate_wsl,
    ) + _collect_boundary(
        now,
        resolved_host,
        "docker-engine",
        "docker-stats-container-aggregate",
        "inferred",
        docker_provider or _read_docker_rows,
        _aggregate_docker,
    )


def _collect_boundary(
    now: datetime,
    host_id: str,
    boundary: str,
    source: str,
    attribution: str,
    provider: RowsProvider,
    aggregate: Callable[[Sequence[Mapping[str, Any]]], tuple[int | float, int | float]],
) -> list[TelemetrySample]:
    try:
        rows = provider()
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise TypeError(f"{boundary} provider must return a sequence")
        cpu, memory = aggregate(rows)
        return _boundary_samples(
            now, host_id, boundary, source, attribution, cpu, memory
        )
    except PermissionError as exc:
        status = CapabilityStatus.PERMISSION_DENIED
        detail = str(exc)
    except FileNotFoundError as exc:
        status = CapabilityStatus.UNSUPPORTED
        detail = str(exc)
    except _BoundaryMissingError as exc:
        status = CapabilityStatus.MISSING
        detail = str(exc)
    except Exception as exc:
        status = CapabilityStatus.FAILED
        detail = str(exc)
    return _boundary_degraded(
        now, host_id, boundary, source, attribution, status, detail
    )


def _aggregate_wsl(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[int | float, int | float]:
    if len(rows) > _MAX_WSL_PROCESSES:
        raise ValueError(f"WSL boundary exceeds {_MAX_WSL_PROCESSES} processes")
    if not rows:
        raise _BoundaryMissingError("no active vmmem boundary process was found")
    cpu = 0.0
    memory = 0.0
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("WSL boundary row is not an object")
        cpu += _number(row.get("cpu_percent"), "WSL CPU")
        memory += _number(row.get("memory_used_bytes"), "WSL memory")
    return _compact(cpu), _compact(memory)


def _aggregate_docker(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[int | float, int | float]:
    if len(rows) > _MAX_CONTAINERS:
        raise ValueError(f"Docker boundary exceeds {_MAX_CONTAINERS} containers")
    cpu = 0.0
    memory = 0.0
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("Docker stats row is not an object")
        cpu += _percent(row.get("CPUPerc"))
        memory += _bytes(_pair(row.get("MemUsage"), 0))
    return _compact(cpu), _compact(memory)


def _read_wsl_rows() -> Sequence[Mapping[str, Any]]:
    payload = _run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _WSL_SCRIPT,
        ],
        "WSL performance counter query",
    )
    return _json_rows(payload, "WSL performance counter query")


def _read_docker_rows() -> Sequence[Mapping[str, Any]]:
    payload = _run(
        ["docker", "stats", "--no-stream", "--no-trunc", "--format", "{{json .}}"],
        "Docker stats query",
    )
    return [
        _json_object(line, "Docker stats query")
        for line in payload.splitlines()
        if line.strip()
    ]


def _run(command: list[str], source: str) -> str:
    completed = subprocess.run(
        command,
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or f"{source} failed"
        lowered = message.lower()
        if "permission denied" in lowered or "access is denied" in lowered:
            raise PermissionError(message)
        if "not found" in lowered or "not recognized" in lowered:
            raise FileNotFoundError(message)
        raise OSError(message)
    if len(completed.stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise ValueError(f"{source} output exceeds 4 MiB")
    return completed.stdout


def _boundary_samples(
    now: datetime,
    host_id: str,
    boundary: str,
    source: str,
    attribution: str,
    cpu: int | float,
    memory: int | float,
) -> list[TelemetrySample]:
    return [
        _sample(
            now,
            host_id,
            boundary,
            source,
            attribution,
            "boundary.cpu.utilization",
            "cpu",
            cpu,
            "percent",
        ),
        _sample(
            now,
            host_id,
            boundary,
            source,
            attribution,
            "boundary.memory.used",
            "memory",
            memory,
            "bytes",
        ),
    ]


def _boundary_degraded(
    now: datetime,
    host_id: str,
    boundary: str,
    source: str,
    attribution: str,
    status: CapabilityStatus,
    detail: str,
) -> list[TelemetrySample]:
    return [
        _sample(
            now,
            host_id,
            boundary,
            source,
            attribution,
            metric,
            capability,
            None,
            unit,
            status=status,
            detail=detail,
        )
        for metric, capability, unit in (
            ("boundary.cpu.utilization", "cpu", "percent"),
            ("boundary.memory.used", "memory", "bytes"),
        )
    ]


def _sample(
    now: datetime,
    host_id: str,
    boundary: str,
    source: str,
    attribution: str,
    metric: str,
    capability: str,
    value: int | float | None,
    unit: str,
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
            collector_id="wsl-docker-boundaries",
            capability=capability,
            capability_status=status,
            value=value,
            unit=unit,
            stale_after_seconds=5.0,
            labels=(
                ("attribution", attribution),
                ("boundary", boundary),
                ("source", source),
            ),
            detail=detail[:4096] if detail is not None else None,
        )
    )


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} is not numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} is invalid")
    return result


def _percent(value: object) -> float:
    if not isinstance(value, str) or not value.strip().endswith("%"):
        raise ValueError("Docker CPU percentage is malformed")
    return _number(float(value.strip()[:-1]), "Docker CPU")


def _pair(value: object, index: int) -> str:
    if not isinstance(value, str):
        raise TypeError("Docker memory usage is not text")
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 2:
        raise ValueError("Docker memory usage is malformed")
    return parts[index]


def _bytes(value: object) -> float:
    if not isinstance(value, str):
        raise TypeError("Docker memory value is not text")
    match = _BYTE_VALUE.fullmatch(value.strip())
    if match is None:
        raise ValueError("Docker memory value is malformed")
    return _number(
        float(match.group(1)) * _BYTE_FACTORS[match.group(2).lower()],
        "Docker memory",
    )


def _compact(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _json_rows(payload: str, source: str) -> Sequence[Mapping[str, Any]]:
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
