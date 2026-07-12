"""Windows shared GPU memory from the non-local adapter memory counter."""

from __future__ import annotations

import json
import math
import platform
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from ..schema import CapabilityStatus, TelemetrySample
from ..status import prepare_sample


SharedMemoryProvider = Callable[[], Sequence[Mapping[str, Any]]]
SOURCE = "windows-cim-gpu-nonlocal-adapter-memory"
_MAX_OUTPUT_BYTES = 1024 * 1024
_MAX_ADAPTERS = 64
_POWERSHELL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$rows = Get-CimInstance Win32_PerfRawData_GPUPerformanceCounters_GPUNonLocalAdapterMemory
$result = @($rows | ForEach-Object {
    [ordered]@{ adapter = $_.Name; shared_used_bytes = $_.NonLocalUsage }
})
ConvertTo-Json -InputObject $result -Compress
""".strip()


def collect_windows_shared_gpu_memory(
    *,
    provider: SharedMemoryProvider | None = None,
    host_id: str | None = None,
    collected_at: datetime | None = None,
) -> list[TelemetrySample]:
    """Return shared GPU memory separately from NVIDIA dedicated VRAM."""

    now = _timestamp(collected_at)
    resolved_host = host_id or platform.node() or "windows-host"
    if provider is None and platform.system() != "Windows":
        return [_unsupported(now, resolved_host, "Windows GPU counters are unavailable")]
    try:
        rows = (provider or _read_shared_memory)()
        if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence):
            raise TypeError("shared GPU memory provider must return a sequence")
        if len(rows) > _MAX_ADAPTERS:
            raise ValueError(f"shared GPU memory provider exceeds {_MAX_ADAPTERS} adapters")
        if not rows:
            return [_unsupported(now, resolved_host, "shared GPU memory counter is unavailable")]
    except PermissionError as exc:
        return [_degraded(now, resolved_host, CapabilityStatus.PERMISSION_DENIED, str(exc))]
    except FileNotFoundError as exc:
        return [_unsupported(now, resolved_host, str(exc))]
    except Exception as exc:
        return [_degraded(now, resolved_host, CapabilityStatus.FAILED, str(exc))]

    samples: list[TelemetrySample] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            samples.append(
                _degraded(
                    now,
                    resolved_host,
                    CapabilityStatus.MISSING,
                    "shared GPU memory row is not an object",
                    adapter=f"adapter-{index}",
                )
            )
            continue
        adapter = str(row.get("adapter") or f"adapter-{index}")[:1024]
        try:
            value = _bytes_value(row.get("shared_used_bytes"))
            status = CapabilityStatus.OK
            detail = None
        except (TypeError, ValueError) as exc:
            value = None
            status = CapabilityStatus.MISSING
            detail = str(exc)
        samples.append(
            _sample(now, resolved_host, status, value, detail, adapter=adapter)
        )
    return samples


def _read_shared_memory() -> Sequence[Mapping[str, Any]]:
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
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "Windows shared GPU memory query failed"
        lowered = message.lower()
        if "invalid class" in lowered or "not found" in lowered:
            raise FileNotFoundError(message)
        if "access is denied" in lowered or "unauthorized" in lowered:
            raise PermissionError(message)
        raise OSError(message)
    if len(completed.stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        raise ValueError("Windows shared GPU memory output exceeds 1 MiB")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise TypeError("Windows shared GPU memory query did not return an array")
    return payload


def _bytes_value(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("shared GPU memory value is not numeric")
    if not math.isfinite(value) or value < 0:
        raise ValueError("shared GPU memory value is invalid")
    if isinstance(value, int) and value.bit_length() > 256:
        raise ValueError("shared GPU memory value is oversized")
    return value


def _unsupported(now: datetime, host_id: str, detail: str) -> TelemetrySample:
    return _degraded(now, host_id, CapabilityStatus.UNSUPPORTED, detail)


def _degraded(
    now: datetime,
    host_id: str,
    status: CapabilityStatus,
    detail: str,
    *,
    adapter: str = "unavailable",
) -> TelemetrySample:
    return _sample(now, host_id, status, None, detail, adapter=adapter)


def _sample(
    now: datetime,
    host_id: str,
    status: CapabilityStatus,
    value: int | float | None,
    detail: str | None,
    *,
    adapter: str,
) -> TelemetrySample:
    return prepare_sample(
        TelemetrySample(
            metric="gpu.memory.shared.used",
            source_timestamp=now,
            collection_timestamp=now,
            host_id=host_id,
            collector_id="windows-gpu-shared-memory",
            capability="shared-gpu-memory",
            capability_status=status,
            value=value,
            unit="bytes",
            stale_after_seconds=3.0,
            labels=(("adapter", adapter), ("source", SOURCE)),
            detail=detail[:4096] if detail is not None else None,
        )
    )


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("collected_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    return value.astimezone(timezone.utc)
