"""Non-elevated macOS host telemetry for Mini and generic Macs."""

from __future__ import annotations

import math
import platform
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from ..macos_primitives import macos_identity_snapshot, macos_memory_snapshot, run_command
from ..schema import CapabilityStatus, TelemetrySample
from ..status import prepare_sample


RawProvider = Callable[[], Mapping[str, Any]]
_MAX_PROCESSES = 64
_METRICS = (
    ("host.identity", "host-identity", "host_identity", None),
    ("host.cpu.utilization", "cpu", "cpu_percent", "percent"),
    ("host.memory.total", "physical-memory", "memory_total_bytes", "bytes"),
    ("host.memory.available", "physical-memory", "memory_available_bytes", "bytes"),
    ("host.memory.used", "unified-memory", "memory_used_bytes", "bytes"),
    ("host.memory.pressure", "memory-pressure", "memory_pressure_percent", "percent"),
    ("host.swap.used", "swap", "swap_used_bytes", "bytes"),
    ("host.disk.throughput", "disk-activity", "disk_bytes_per_second", "bytes/second"),
    ("host.network.throughput", "network-activity", "network_bytes_per_second", "bytes/second"),
)


def collect_macos_host(
    *,
    provider: RawProvider | None = None,
    host_id: str | None = None,
    collected_at: datetime | None = None,
) -> list[TelemetrySample]:
    """Collect a normalized macOS snapshot without administrator privileges."""

    now = _timestamp(collected_at)
    resolved_host = host_id or platform.node() or "macos-host"
    if provider is None and platform.system() != "Darwin":
        return _degraded_samples(
            now,
            resolved_host,
            CapabilityStatus.UNSUPPORTED,
            "macOS host metrics are only available on macOS",
        )
    try:
        raw = dict((provider or _read_macos_snapshot)())
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

    labels = _identity_labels(raw)
    explicit_statuses = raw.get("_statuses")
    explicit_details = raw.get("_details")
    statuses = explicit_statuses if isinstance(explicit_statuses, Mapping) else {}
    details = explicit_details if isinstance(explicit_details, Mapping) else {}
    samples: list[TelemetrySample] = []
    for metric, capability, key, unit in _METRICS:
        value = raw.get(key)
        explicit_status = statuses.get(key)
        if isinstance(explicit_status, CapabilityStatus) and explicit_status is not CapabilityStatus.OK:
            value = None
            status = explicit_status
            detail = str(details.get(key) or f"macOS collector could not provide {key}")
        elif _valid_value(value, allow_text=metric == "host.identity"):
            status = CapabilityStatus.OK
            detail = None
        else:
            value = None
            status = CapabilityStatus.MISSING
            detail = f"macOS collector did not expose {key}"
        samples.append(
            _sample(
                now,
                resolved_host,
                metric,
                capability,
                status,
                value,
                unit,
                labels,
                detail,
            )
        )
    process_status = statuses.get("processes")
    samples.extend(
        _process_samples(
            raw.get("processes"),
            now,
            resolved_host,
            explicit_status=(
                process_status if isinstance(process_status, CapabilityStatus) else None
            ),
            explicit_detail=str(details.get("processes") or ""),
        )
    )
    return samples


def _read_macos_snapshot() -> Mapping[str, Any]:
    raw: dict[str, Any] = {"_statuses": {}, "_details": {}}
    try:
        identity = macos_identity_snapshot()
        raw.update(identity)
        raw["host_identity"] = (
            identity.get("computer_name")
            or identity.get("local_host_name")
            or identity.get("hostname")
            or identity.get("platform_node")
        )
    except Exception as exc:
        _mark_degraded(raw, ("host_identity",), exc)

    memory_keys = (
        "memory_total_bytes",
        "memory_available_bytes",
        "memory_used_bytes",
        "memory_pressure_percent",
        "swap_used_bytes",
    )
    try:
        raw.update(macos_memory_snapshot())
    except Exception as exc:
        _mark_degraded(raw, memory_keys, exc)

    try:
        processes = _read_processes()
        raw["processes"] = processes
        logical_cpu = int(run_command(["sysctl", "-n", "hw.logicalcpu"]))
        if logical_cpu <= 0:
            raise ValueError("hw.logicalcpu was not positive")
        raw["cpu_percent"] = min(
            100.0,
            sum(float(process["cpu_percent"]) for process in processes) / logical_cpu,
        )
    except Exception as exc:
        _mark_degraded(raw, ("processes", "cpu_percent"), exc)

    first_disk = _capture_counter(raw, "disk_bytes_per_second", _read_iostat_total)
    first_network = _capture_counter(
        raw, "network_bytes_per_second", _read_netstat_total
    )
    if first_disk is not None or first_network is not None:
        started = time.monotonic()
        time.sleep(0.25)
        elapsed = max(time.monotonic() - started, 0.001)
        if first_disk is not None:
            second = _capture_counter(raw, "disk_bytes_per_second", _read_iostat_total)
            if second is not None:
                try:
                    raw["disk_bytes_per_second"] = _counter_rate(
                        first_disk, second, elapsed
                    )
                except Exception as exc:
                    _mark_degraded(raw, ("disk_bytes_per_second",), exc)
        if first_network is not None:
            second = _capture_counter(
                raw, "network_bytes_per_second", _read_netstat_total
            )
            if second is not None:
                try:
                    raw["network_bytes_per_second"] = _counter_rate(
                        first_network, second, elapsed
                    )
                except Exception as exc:
                    _mark_degraded(raw, ("network_bytes_per_second",), exc)
    return raw


def _read_processes() -> list[dict[str, int | float | str]]:
    output = run_command(["ps", "-axo", "pid=,pcpu=,rss=,comm="])
    processes: list[dict[str, int | float | str]] = []
    for line in output.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        pid, cpu, rss, command = parts
        try:
            record = {
                "pid": int(pid),
                "cpu_percent": float(cpu),
                "memory_used_bytes": int(rss) * 1024,
                "command": command[:1024],
            }
        except ValueError:
            continue
        if record["pid"] > 0 and record["cpu_percent"] >= 0 and record["memory_used_bytes"] >= 0:
            processes.append(record)
    return sorted(
        processes,
        key=lambda item: (float(item["memory_used_bytes"]), float(item["cpu_percent"])),
        reverse=True,
    )[:_MAX_PROCESSES]


def _read_iostat_total() -> int:
    output = run_command(["iostat", "-Id"])
    numeric_lines = [
        line.split()
        for line in output.splitlines()
        if line.strip() and all(_is_number(part) for part in line.split())
    ]
    if not numeric_lines:
        raise ValueError("iostat contained no numeric device totals")
    values = numeric_lines[-1]
    if len(values) % 3:
        raise ValueError("iostat device totals had an unexpected shape")
    return int(sum(float(values[index]) for index in range(2, len(values), 3)) * 1024**2)


def _read_netstat_total() -> int:
    output = run_command(["netstat", "-ibn"])
    lines = [line.split() for line in output.splitlines() if line.strip()]
    header_index = next(
        (index for index, row in enumerate(lines) if "Ibytes" in row and "Obytes" in row),
        None,
    )
    if header_index is None:
        raise ValueError("netstat did not expose byte counters")
    header = lines[header_index]
    ibytes = header.index("Ibytes")
    obytes = header.index("Obytes")
    seen: set[str] = set()
    total = 0
    for row in lines[header_index + 1 :]:
        if len(row) <= max(ibytes, obytes) or row[0] in seen or row[0].startswith("lo"):
            continue
        if len(row) < 3 or not row[2].startswith("<Link#"):
            continue
        try:
            total += int(row[ibytes]) + int(row[obytes])
        except ValueError:
            continue
        seen.add(row[0])
    return total


def _process_samples(
    value: object,
    now: datetime,
    host_id: str,
    *,
    explicit_status: CapabilityStatus | None = None,
    explicit_detail: str = "",
) -> list[TelemetrySample]:
    if (
        explicit_status is not None
        or isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
    ):
        status = explicit_status or CapabilityStatus.MISSING
        return [
            _sample(
                now,
                host_id,
                metric,
                "process-resource",
                status,
                None,
                unit,
                (("source", "ps"),),
                explicit_detail or "macOS collector did not expose process resources",
            )
            for metric, unit in (
                ("host.process.cpu.utilization", "percent"),
                ("host.process.memory.used", "bytes"),
            )
        ]
    samples: list[TelemetrySample] = []
    for item in value[:_MAX_PROCESSES]:
        if not isinstance(item, Mapping):
            continue
        pid = str(item.get("pid", "unknown"))[:1024]
        command = str(item.get("command", "unknown"))[:1024]
        process_labels = (("command", command), ("pid", pid), ("source", "ps"))
        for metric, key, unit in (
            ("host.process.cpu.utilization", "cpu_percent", "percent"),
            ("host.process.memory.used", "memory_used_bytes", "bytes"),
        ):
            measurement = item.get(key)
            status = CapabilityStatus.OK if _valid_value(measurement) else CapabilityStatus.MISSING
            samples.append(
                _sample(
                    now,
                    host_id,
                    metric,
                    "process-resource",
                    status,
                    measurement if status is CapabilityStatus.OK else None,
                    unit,
                    process_labels,
                    None if status is CapabilityStatus.OK else f"process did not expose {key}",
                )
            )
    if samples:
        return samples
    return _process_samples(None, now, host_id)


def _identity_labels(raw: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    labels = [("source", "macos-builtins")]
    for label, key in (("hardware_model", "hardware_model"), ("os_version", "os_version")):
        value = raw.get(key)
        if isinstance(value, str) and value:
            labels.append((label, value[:1024]))
    return tuple(labels)


def _valid_value(value: object, *, allow_text: bool = False) -> bool:
    if allow_text and isinstance(value, str):
        return bool(value.strip()) and len(value) <= 64 * 1024
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int) and value.bit_length() > 256:
        return False
    return math.isfinite(value) and value >= 0


def _capture_counter(
    raw: dict[str, Any], key: str, callback: Callable[[], int]
) -> int | None:
    try:
        return callback()
    except Exception as exc:
        _mark_degraded(raw, (key,), exc)
        return None


def _mark_degraded(
    raw: dict[str, Any], keys: tuple[str, ...], error: BaseException
) -> None:
    if isinstance(error, PermissionError):
        status = CapabilityStatus.PERMISSION_DENIED
    elif isinstance(error, FileNotFoundError):
        status = CapabilityStatus.UNSUPPORTED
    else:
        status = CapabilityStatus.FAILED
    statuses = raw["_statuses"]
    details = raw["_details"]
    for key in keys:
        statuses[key] = status
        details[key] = str(error)[:4096]


def _counter_rate(first: int, second: int, elapsed: float) -> int | float:
    if second < first:
        raise ValueError("macOS activity counter moved backwards")
    rate = (second - first) / elapsed
    return int(rate) if rate.is_integer() else rate


def _is_number(value: str) -> bool:
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _sample(
    now: datetime,
    host_id: str,
    metric: str,
    capability: str,
    status: CapabilityStatus,
    value: int | float | str | None,
    unit: str | None,
    labels: tuple[tuple[str, str], ...],
    detail: str | None,
) -> TelemetrySample:
    return prepare_sample(
        TelemetrySample(
            metric=metric,
            source_timestamp=now,
            collection_timestamp=now,
            host_id=host_id,
            collector_id="macos-host",
            capability=capability,
            capability_status=status,
            value=value,
            unit=unit,
            stale_after_seconds=5.0,
            labels=labels,
            detail=detail[:4096] if detail else None,
        )
    )


def _degraded_samples(
    now: datetime, host_id: str, status: CapabilityStatus, detail: str
) -> list[TelemetrySample]:
    samples = [
        _sample(
            now,
            host_id,
            metric,
            capability,
            status,
            None,
            unit,
            (("source", "macos-builtins"),),
            detail,
        )
        for metric, capability, _key, unit in _METRICS
    ]
    samples.extend(
        _sample(
            now,
            host_id,
            metric,
            "process-resource",
            status,
            None,
            unit,
            (("source", "ps"),),
            detail,
        )
        for metric, unit in (
            ("host.process.cpu.utilization", "percent"),
            ("host.process.memory.used", "bytes"),
        )
    )
    return samples


def _timestamp(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, datetime):
        raise TypeError("collected_at must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collected_at must be timezone-aware")
    return value.astimezone(timezone.utc)
