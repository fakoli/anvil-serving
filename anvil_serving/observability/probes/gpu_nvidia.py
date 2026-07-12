"""NVIDIA GPU telemetry parsed from ``nvidia-smi -q -x`` output."""

from __future__ import annotations

import math
import platform
import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone

from ..schema import CapabilityStatus, TelemetrySample
from ..status import prepare_sample


XmlProvider = Callable[[], str]
_MAX_XML_BYTES = 4 * 1024 * 1024
_NUMBER = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*(?:MiB|%|C|W)?$")
_NVIDIA_DTD = re.compile(
    r'<!DOCTYPE\s+nvidia_smi_log\s+SYSTEM\s+"nvsmi_device_v[0-9]+\.dtd"\s*>',
    re.IGNORECASE,
)


def collect_nvidia_gpus(
    *,
    provider: XmlProvider | None = None,
    host_id: str | None = None,
    collected_at: datetime | None = None,
) -> list[TelemetrySample]:
    """Collect normalized NVIDIA telemetry with no long-running sidecar."""

    now = _timestamp(collected_at)
    resolved_host = host_id or platform.node() or "gpu-host"
    try:
        payload = (provider or _read_nvidia_xml)()
        if not isinstance(payload, str):
            raise TypeError("NVIDIA collector output must be text")
        if len(payload.encode("utf-8")) > _MAX_XML_BYTES:
            raise ValueError("NVIDIA collector output exceeds 4 MiB")
        payload = _NVIDIA_DTD.sub("", payload, count=1)
        if "<!DOCTYPE" in payload.upper() or "<!ENTITY" in payload.upper():
            raise ValueError("NVIDIA collector output contains forbidden XML declarations")
        root = ET.fromstring(payload)
        gpu_nodes = root.findall("./gpu")
        if not gpu_nodes:
            raise ValueError("NVIDIA collector returned no GPUs")
    except PermissionError as exc:
        return [_collector_status(now, resolved_host, CapabilityStatus.PERMISSION_DENIED, exc)]
    except Exception as exc:
        return [_collector_status(now, resolved_host, CapabilityStatus.FAILED, exc)]

    samples: list[TelemetrySample] = []
    for index, gpu in enumerate(gpu_nodes):
        samples.extend(_gpu_samples(gpu, index, now, resolved_host))
    return samples


def _read_nvidia_xml() -> str:
    completed = subprocess.run(
        ["nvidia-smi", "-q", "-x"],
        capture_output=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "nvidia-smi failed"
        if "permission" in message.lower() or "access is denied" in message.lower():
            raise PermissionError(message)
        raise OSError(message)
    return completed.stdout


def _gpu_samples(
    gpu: ET.Element, index: int, now: datetime, host_id: str
) -> list[TelemetrySample]:
    uuid = _text(gpu, "uuid") or f"gpu-{index}"
    labels = (("gpu_index", str(index)), ("gpu_uuid", uuid[:1024]))
    fields = (
        ("gpu.identity", "identity", _text(gpu, "product_name"), None, "text"),
        (
            "gpu.utilization",
            "utilization",
            _text(gpu, "utilization/gpu_util"),
            "percent",
            "number",
        ),
        (
            "gpu.memory.total",
            "dedicated-vram",
            _text(gpu, "fb_memory_usage/total"),
            "bytes",
            "mib",
        ),
        (
            "gpu.memory.used",
            "dedicated-vram",
            _text(gpu, "fb_memory_usage/used"),
            "bytes",
            "mib",
        ),
        (
            "gpu.memory.available",
            "dedicated-vram",
            _text(gpu, "fb_memory_usage/free"),
            "bytes",
            "mib",
        ),
        (
            "gpu.temperature",
            "temperature",
            _text(gpu, "temperature/gpu_temp"),
            "celsius",
            "number",
        ),
        (
            "gpu.power.draw",
            "power",
            _first_supported_text(
                gpu,
                "gpu_power_readings/instant_power_draw",
                "gpu_power_readings/power_draw",
                "gpu_power_readings/average_power_draw",
                "power_readings/power_draw",
            ),
            "watts",
            "number",
        ),
    )
    samples = [
        _field_sample(now, host_id, labels, metric, capability, raw, unit, kind)
        for metric, capability, raw, unit, kind in fields
    ]
    process_nodes = gpu.findall("./processes/process_info")
    samples.append(
        _sample(
            now,
            host_id,
            "gpu.process.count",
            "active-processes",
            CapabilityStatus.OK,
            len(process_nodes),
            "processes",
            labels,
        )
    )
    for process in process_nodes:
        process_labels = labels + (
            ("pid", (_text(process, "pid") or "unknown")[:1024]),
            ("process", (_text(process, "process_name") or "unknown")[:1024]),
        )
        samples.append(
            _field_sample(
                now,
                host_id,
                process_labels,
                "gpu.process.memory.used",
                "active-processes",
                _text(process, "used_memory"),
                "bytes",
                "mib",
            )
        )
    return samples


def _field_sample(
    now: datetime,
    host_id: str,
    labels: tuple[tuple[str, str], ...],
    metric: str,
    capability: str,
    raw: str | None,
    unit: str | None,
    kind: str,
) -> TelemetrySample:
    try:
        value: int | float | str
        if raw is None or raw.strip().lower() in {"", "n/a", "[n/a]"}:
            raise ValueError(f"NVIDIA collector did not expose {metric}")
        if kind == "text":
            value = raw.strip()
            if len(value) > 64 * 1024:
                raise ValueError(f"NVIDIA collector returned an oversized {metric}")
        else:
            value = _number(raw, mib=kind == "mib")
        return _sample(
            now, host_id, metric, capability, CapabilityStatus.OK, value, unit, labels
        )
    except (TypeError, ValueError) as exc:
        return _sample(
            now,
            host_id,
            metric,
            capability,
            CapabilityStatus.MISSING,
            None,
            unit,
            labels,
            str(exc),
        )


def _sample(
    now: datetime,
    host_id: str,
    metric: str,
    capability: str,
    status: CapabilityStatus,
    value: int | float | str | None,
    unit: str | None,
    labels: tuple[tuple[str, str], ...],
    detail: str | None = None,
) -> TelemetrySample:
    return prepare_sample(
        TelemetrySample(
            metric=metric,
            source_timestamp=now,
            collection_timestamp=now,
            host_id=host_id,
            collector_id="nvidia-smi",
            capability=capability,
            capability_status=status,
            value=value,
            unit=unit,
            stale_after_seconds=3.0,
            labels=labels,
            detail=detail,
        )
    )


def _collector_status(
    now: datetime, host_id: str, status: CapabilityStatus, error: BaseException
) -> TelemetrySample:
    return _sample(
        now,
        host_id,
        "gpu.collector.status",
        "nvidia-gpu",
        status,
        None,
        None,
        (),
        str(error)[:4096],
    )


def _text(node: ET.Element, path: str) -> str | None:
    child = node.find(path)
    return child.text if child is not None else None


def _first_supported_text(node: ET.Element, *paths: str) -> str | None:
    for path in paths:
        value = _text(node, path)
        if value is not None and value.strip().lower() not in {"", "n/a", "[n/a]"}:
            return value
    return None


def _number(value: str, *, mib: bool) -> int | float:
    match = _NUMBER.match(value.strip())
    if match is None:
        raise ValueError("NVIDIA collector returned a non-numeric value")
    number = float(match.group(1))
    if not math.isfinite(number) or number < 0:
        raise ValueError("NVIDIA collector returned an invalid value")
    converted = number * 1024 * 1024 if mib else number
    return int(converted) if converted.is_integer() else converted


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("collected_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    return value.astimezone(timezone.utc)
