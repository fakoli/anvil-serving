"""Strict observability overhead measurement and acceptance gates."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import statistics
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..redaction import redact_record


MIB = 1024 * 1024
PROFILE_LIMITS = {
    "normal": {"rss_bytes": 100 * MIB, "cpu_percent": 1.0},
    "benchmark": {"rss_bytes": 150 * MIB, "cpu_percent": 2.0},
}


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    timestamp_seconds: float
    cpu_percent: float
    rss_bytes: int
    disk_write_bytes: int
    docker_requests: int
    network_requests: int
    gpu_allocated_bytes: int
    subprocess_cpu_percent: float = 0
    subprocess_rss_bytes: int = 0

    def __post_init__(self) -> None:
        integer_fields = {
            "rss_bytes",
            "disk_write_bytes",
            "docker_requests",
            "network_requests",
            "gpu_allocated_bytes",
            "subprocess_rss_bytes",
        }
        for field, value in asdict(self).items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{field} must be a non-negative number")
            if field in integer_fields and not isinstance(value, int):
                raise ValueError(f"{field} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class BenchmarkOutcome:
    throughput: float
    latency_seconds: float

    def __post_init__(self) -> None:
        values = (self.throughput, self.latency_seconds)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            for value in values
        ):
            raise ValueError("benchmark throughput and latency must be positive")


def evaluate_overhead(
    profile: str,
    observations: Sequence[ResourceObservation],
    *,
    collection_off: BenchmarkOutcome,
    collection_on: BenchmarkOutcome,
) -> dict[str, Any]:
    if profile not in PROFILE_LIMITS:
        raise ValueError("profile must be normal or benchmark")
    if len(observations) < 2:
        raise ValueError("at least two resource observations are required")
    _validate_observations(observations)
    limits = PROFILE_LIMITS[profile]
    cpu = [sample.cpu_percent for sample in observations]
    rss = [sample.rss_bytes for sample in observations]
    disk = [sample.disk_write_bytes for sample in observations]
    gpu = [sample.gpu_allocated_bytes for sample in observations]
    throughput_change = _percent_change(collection_off.throughput, collection_on.throughput)
    latency_change = _percent_change(collection_off.latency_seconds, collection_on.latency_seconds)
    metrics = {
        "duration_seconds": observations[-1].timestamp_seconds - observations[0].timestamp_seconds,
        "cpu_percent": {"average": statistics.fmean(cpu), "peak": max(cpu)},
        "rss_bytes": {"average": statistics.fmean(rss), "peak": max(rss)},
        "disk_write_bytes": {"total": max(disk) - min(disk), "peak": max(disk)},
        "docker_requests": _counter_summary(observations, "docker_requests"),
        "network_requests": _counter_summary(observations, "network_requests"),
        "gpu_allocated_bytes": {"average": statistics.fmean(gpu), "peak": max(gpu)},
        "subprocess_spikes": {
            "cpu_percent": max(item.subprocess_cpu_percent for item in observations),
            "rss_bytes": max(item.subprocess_rss_bytes for item in observations),
        },
        "benchmark_effect": {
            "throughput_change_percent": throughput_change,
            "latency_change_percent": latency_change,
        },
    }
    failures = []
    if metrics["cpu_percent"]["average"] > limits["cpu_percent"]:
        failures.append("average CPU exceeds profile limit")
    if metrics["rss_bytes"]["peak"] > limits["rss_bytes"]:
        failures.append("peak RSS exceeds profile limit")
    if profile == "benchmark" and metrics["gpu_allocated_bytes"]["peak"] != 0:
        failures.append("benchmark observability allocated GPU memory")
    if profile == "benchmark" and metrics["disk_write_bytes"]["total"] != 0:
        failures.append("benchmark capture performed disk writes")
    if abs(throughput_change) > 1:
        failures.append("benchmark throughput changed by more than 1 percent")
    if abs(latency_change) > 1:
        failures.append("benchmark latency changed by more than 1 percent")
    return {
        "profile": profile,
        "passed": not failures,
        "limits": {
            "max_rss_bytes": limits["rss_bytes"],
            "max_average_cpu_percent": limits["cpu_percent"],
            "max_benchmark_effect_percent": 1.0,
            "gpu_allocation_bytes": 0 if profile == "benchmark" else None,
            "capture_disk_write_bytes": 0 if profile == "benchmark" else None,
        },
        "metrics": metrics,
        "failures": failures,
    }


def measure_callable(
    workload: Callable[[], None],
    provider: Callable[[], Mapping[str, int | float]],
    *,
    duration_seconds: float,
    interval_seconds: float = 0.25,
) -> list[ResourceObservation]:
    """Repeat a workload and sample a target-specific resource provider."""

    if not 0.1 <= duration_seconds <= 3600:
        raise ValueError("duration_seconds must be between 0.1 and 3600")
    if not 0.01 <= interval_seconds <= 60:
        raise ValueError("interval_seconds must be between 0.01 and 60")
    started = time.monotonic()
    observations = []
    while True:
        workload()
        values = dict(provider())
        observations.append(
            ResourceObservation(
                timestamp_seconds=time.monotonic() - started,
                cpu_percent=float(values["cpu_percent"]),
                rss_bytes=int(values["rss_bytes"]),
                disk_write_bytes=int(values.get("disk_write_bytes", 0)),
                docker_requests=int(values.get("docker_requests", 0)),
                network_requests=int(values.get("network_requests", 0)),
                gpu_allocated_bytes=int(values.get("gpu_allocated_bytes", 0)),
                subprocess_cpu_percent=float(values.get("subprocess_cpu_percent", 0)),
                subprocess_rss_bytes=int(values.get("subprocess_rss_bytes", 0)),
            )
        )
        elapsed = time.monotonic() - started
        if elapsed >= duration_seconds:
            break
        time.sleep(min(interval_seconds, duration_seconds - elapsed))
    return observations


def process_cpu_percent(process_cpu_start: float, wall_start: float) -> float:
    """Return current-process CPU as a percentage of whole-host capacity."""

    wall = time.monotonic() - wall_start
    if wall <= 0:
        return 0.0
    return max(0.0, (time.process_time() - process_cpu_start) / wall * 100 / (os.cpu_count() or 1))


def publish_overhead_result(
    results: Mapping[str, Mapping[str, Any]],
    *,
    evidence_root: str | Path,
    repo_root: str | Path,
    finding_path: str | Path,
    findings_index: str | Path,
    secrets: Sequence[str] = (),
) -> dict[str, Any]:
    """Persist raw results externally and a sanitized indexed summary in Git."""

    root = Path(evidence_root).expanduser().resolve()
    repository = Path(repo_root).expanduser().resolve()
    if root == repository or root.is_relative_to(repository):
        raise ValueError("raw overhead evidence must be outside the Git repository")
    if not results:
        raise ValueError("at least one overhead profile result is required")
    finding = Path(finding_path).resolve()
    index = Path(findings_index).resolve()
    findings_root = repository / "docs" / "findings"
    if finding.parent != findings_root or finding.suffix != ".md":
        raise ValueError("overhead finding must be a Markdown file under docs/findings")
    if index != findings_root / "README.md":
        raise ValueError("findings index must be docs/findings/README.md")
    if finding.exists():
        raise FileExistsError("observability overhead finding already exists")
    index_text = index.read_text(encoding="utf-8")
    marker = "|------|------|---------|\n"
    if marker not in index_text:
        raise ValueError("findings index table marker is missing")
    for name, result in results.items():
        if name not in PROFILE_LIMITS or not isinstance(result, Mapping):
            raise ValueError("published overhead profiles must be normal or benchmark mappings")
        if result.get("profile") != name or type(result.get("passed")) is not bool:
            raise ValueError("published overhead result has invalid profile or pass state")
    safe = redact_record({"schema_version": 1, "profiles": dict(results)}, secrets=secrets)
    encoded = json.dumps(safe, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    if len(encoded) > 8 * MIB:
        raise ValueError("overhead evidence exceeds 8 MiB")
    compressed = gzip.compress(encoded, compresslevel=9, mtime=0)
    path = root / "observability-overhead.json.gz"
    if path.exists():
        raise FileExistsError("observability overhead artifact already exists")
    manifest = {
        "artifact_locator": "anvil-evidence://observability/observability-overhead.json.gz",
        "sha256": hashlib.sha256(compressed).hexdigest(),
        "compressed_bytes": len(compressed),
        "passed": all(result["passed"] is True for result in results.values()),
        "profiles": {
            name: {
                "passed": result.get("passed"),
                "failures": result.get("failures", []),
                "metrics": result.get("metrics", {}),
            }
            for name, result in results.items()
        },
    }
    lines = [
        "# Observability overhead gate\n",
        f"- Overall result: **{'PASS' if manifest['passed'] else 'FAIL'}**",
        f"- Raw artifact: `{manifest['artifact_locator']}`",
        f"- SHA-256: `{manifest['sha256']}`",
        f"- Compressed size: {manifest['compressed_bytes']} bytes",
    ]
    for name, result in manifest["profiles"].items():
        metrics = result["metrics"]
        lines.extend(
            (
                f"\n## {name.title()} profile",
                f"- Result: **{'PASS' if result['passed'] else 'FAIL'}**",
                f"- CPU average / peak: {metrics['cpu_percent']['average']:.4f}% / {metrics['cpu_percent']['peak']:.4f}%",
                f"- RSS average / peak: {metrics['rss_bytes']['average']:.0f} / {metrics['rss_bytes']['peak']} bytes",
                f"- Disk writes / GPU allocation: {metrics['disk_write_bytes']['total']} / {metrics['gpu_allocated_bytes']['peak']} bytes",
                f"- Throughput / latency change: {metrics['benchmark_effect']['throughput_change_percent']:.4f}% / {metrics['benchmark_effect']['latency_change_percent']:.4f}%",
                f"- Failures: {', '.join(result['failures']) if result['failures'] else 'none'}",
            )
        )
    _write_new(path, compressed)
    _write_new(finding, ("\n".join(lines) + "\n").encode())
    row = f"| 2026-07-11 | [{finding.name}]({finding.name}) | Strict observability overhead and benchmark-effect gate |\n"
    if f"]({finding.name})" not in index_text:
        _write(index, index_text.replace(marker, marker + row, 1).encode())
    return manifest


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_new(path: Path, payload: bytes) -> None:
    """Create evidence without a check-then-replace overwrite race."""

    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite {path.name}") from None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _percent_change(baseline: float, measured: float) -> float:
    return (measured - baseline) / baseline * 100


def _validate_observations(observations: Sequence[ResourceObservation]) -> None:
    previous = observations[0]
    for current in observations[1:]:
        if current.timestamp_seconds <= previous.timestamp_seconds:
            raise ValueError("resource observation timestamps must be strictly increasing")
        for field in ("disk_write_bytes", "docker_requests", "network_requests"):
            if getattr(current, field) < getattr(previous, field):
                raise ValueError(f"{field} must be a monotonic cumulative counter")
        previous = current


def _counter_summary(observations: Sequence[ResourceObservation], field: str) -> dict[str, int]:
    values = [int(getattr(item, field)) for item in observations]
    deltas = [current - previous for previous, current in zip(values, values[1:])]
    return {"total": values[-1] - values[0], "peak": max(deltas, default=0)}
