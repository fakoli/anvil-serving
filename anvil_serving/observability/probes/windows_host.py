"""Unprivileged Windows host telemetry using built-in CIM providers."""

from __future__ import annotations

import json
import math
import platform
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from ..schema import CapabilityStatus, TelemetrySample
from ..status import prepare_sample


RawProvider = Callable[[], Mapping[str, Any]]
_POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor |
    Measure-Object -Property LoadPercentage -Average
$memory = Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory
$disk = Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk |
    Where-Object Name -eq '_Total' | Select-Object -First 1
$network = Get-CimInstance Win32_PerfFormattedData_Tcpip_NetworkInterface |
    Measure-Object -Property BytesTotalPersec -Sum
[ordered]@{
    cpu_percent = $cpu.Average
    memory_total_bytes = $(if ($null -eq $os.TotalVisibleMemorySize) { $null } else { [double]$os.TotalVisibleMemorySize * 1024 })
    memory_available_bytes = $(if ($null -eq $os.FreePhysicalMemory) { $null } else { [double]$os.FreePhysicalMemory * 1024 })
    paging_pages_per_second = $memory.PagesPersec
    disk_bytes_per_second = $disk.DiskBytesPersec
    network_bytes_per_second = $network.Sum
} | ConvertTo-Json -Compress
""".strip()

_METRICS = (
    ("host.cpu.utilization", "cpu", "cpu_percent", "percent"),
    ("host.memory.total", "physical-memory", "memory_total_bytes", "bytes"),
    (
        "host.memory.available",
        "physical-memory",
        "memory_available_bytes",
        "bytes",
    ),
    ("host.memory.used", "physical-memory", "memory_used_bytes", "bytes"),
    ("host.memory.pressure", "memory-pressure", "memory_pressure_percent", "percent"),
    ("host.paging.rate", "paging", "paging_pages_per_second", "pages/second"),
    ("host.disk.throughput", "disk-activity", "disk_bytes_per_second", "bytes/second"),
    (
        "host.network.throughput",
        "network-activity",
        "network_bytes_per_second",
        "bytes/second",
    ),
)


def collect_windows_host(
    *,
    provider: RawProvider | None = None,
    host_id: str | None = None,
    collected_at: datetime | None = None,
) -> list[TelemetrySample]:
    """Collect one normalized snapshot without requiring administrator rights."""

    now = _timestamp(collected_at)
    resolved_host = host_id or platform.node() or "windows-host"
    if provider is None and platform.system() != "Windows":
        return _degraded_samples(
            now,
            resolved_host,
            CapabilityStatus.UNSUPPORTED,
            "Windows host metrics are only available on Windows",
        )

    try:
        raw = dict((provider or _read_cim_snapshot)())
    except PermissionError as exc:
        return _degraded_samples(
            now, resolved_host, CapabilityStatus.PERMISSION_DENIED, str(exc)
        )
    except FileNotFoundError as exc:
        return _degraded_samples(
            now, resolved_host, CapabilityStatus.UNSUPPORTED, str(exc)
        )
    except Exception as exc:
        return _degraded_samples(now, resolved_host, CapabilityStatus.FAILED, str(exc))

    _derive_memory(raw)
    samples: list[TelemetrySample] = []
    for metric, capability, raw_key, unit in _METRICS:
        try:
            value = _measurement(raw, raw_key)
            status = CapabilityStatus.OK
            detail = None
        except (KeyError, TypeError, ValueError) as exc:
            value = None
            status = CapabilityStatus.MISSING
            detail = str(exc)
        samples.append(
            prepare_sample(
                TelemetrySample(
                    metric=metric,
                    source_timestamp=now,
                    collection_timestamp=now,
                    host_id=resolved_host,
                    collector_id="windows-host",
                    capability=capability,
                    capability_status=status,
                    value=value,
                    unit=unit,
                    stale_after_seconds=5.0,
                    detail=detail,
                )
            )
        )
    return samples


def _read_cim_snapshot() -> Mapping[str, Any]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _POWERSHELL_SCRIPT,
        ],
        capture_output=True,
        check=False,
        creationflags=creation_flags,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "Windows CIM query failed"
        if "access is denied" in message.lower() or "unauthorized" in message.lower():
            raise PermissionError(message)
        raise OSError(message)
    payload = json.loads(completed.stdout)
    if not isinstance(payload, Mapping):
        raise ValueError("Windows CIM query did not return an object")
    return payload


def _derive_memory(raw: dict[str, Any]) -> None:
    try:
        total = _measurement(raw, "memory_total_bytes")
        available = _measurement(raw, "memory_available_bytes")
        if total <= 0 or available > total:
            raise ValueError("physical memory totals are inconsistent")
        raw["memory_used_bytes"] = total - available
        raw["memory_pressure_percent"] = (total - available) / total * 100.0
    except (KeyError, TypeError, ValueError):
        raw.pop("memory_used_bytes", None)
        raw.pop("memory_pressure_percent", None)


def _measurement(raw: Mapping[str, Any], key: str) -> int | float:
    if key not in raw:
        raise KeyError(f"Windows collector did not return {key}")
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"Windows collector returned a non-numeric {key}")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Windows collector returned an invalid {key}")
    if isinstance(value, int) and value.bit_length() > 256:
        raise ValueError(f"Windows collector returned an oversized {key}")
    return value


def _degraded_samples(
    now: datetime,
    host_id: str,
    status: CapabilityStatus,
    detail: str,
) -> list[TelemetrySample]:
    return [
        prepare_sample(
            TelemetrySample(
                metric=metric,
                source_timestamp=now,
                collection_timestamp=now,
                host_id=host_id,
                collector_id="windows-host",
                capability=capability,
                capability_status=status,
                value=None,
                unit=unit,
                stale_after_seconds=5.0,
                detail=_detail(detail),
            )
        )
        for metric, capability, _raw_key, unit in _METRICS
    ]


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("collected_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _detail(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:4096]
