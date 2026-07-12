"""Secret-safe durable benchmark telemetry artifacts outside Git."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..redaction import redact_record
from ..retention import RetainedFrame
from .session import CaptureSession


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    topology: Mapping[str, Any]
    hosts: Sequence[Mapping[str, Any]]
    hardware: Sequence[Mapping[str, Any]]
    collector_versions: Mapping[str, str]
    capabilities: Sequence[str]
    sampling_intervals: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class ArtifactResult:
    raw_path: Path
    manifest: Mapping[str, Any]
    finding_path: Path | None = None


def write_capture_artifact(
    session: CaptureSession,
    metadata: ArtifactMetadata,
    *,
    evidence_root: str | Path,
    repo_root: str | Path,
    secrets: Sequence[str] = (),
    finding_path: str | Path | None = None,
    findings_index: str | Path | None = None,
) -> ArtifactResult:
    """Write compressed raw evidence externally and an optional sanitized finding."""

    root = Path(evidence_root).expanduser().resolve()
    repository = Path(repo_root).expanduser().resolve()
    if root == repository or root.is_relative_to(repository):
        raise ValueError("raw telemetry evidence root must be outside the Git repository")
    root.mkdir(parents=True, exist_ok=True)
    frames = _unique_frames((*session.pre_history, *session.captured))
    analysis = _analyze(frames)
    quality = (
        "incomplete"
        if session.state != "complete"
        else "degraded"
        if session.failures or analysis["gap_count"]
        else "complete"
    )
    raw = redact_record(
        {
            "schema_version": 1,
            "session": session.status(),
            "capture_quality": quality,
            "metadata": {
                "topology": dict(metadata.topology),
                "hosts": list(metadata.hosts),
                "hardware": list(metadata.hardware),
                "collector_versions": dict(metadata.collector_versions),
                "capabilities": list(metadata.capabilities),
                "sampling_intervals": dict(metadata.sampling_intervals),
            },
            "time_alignment": analysis["time_alignment"],
            "gaps": analysis["gaps"],
            "domain_metrics": analysis["domain_metrics"],
            "frames": [frame.as_dict() for frame in frames],
        },
        secrets=secrets,
    )
    encoded = json.dumps(raw, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    compressed = gzip.compress(encoded, compresslevel=9, mtime=0)
    filename = f"benchmark-telemetry-{session.session_id}.json.gz"
    raw_path = root / filename
    if raw_path.exists():
        raise FileExistsError("benchmark telemetry artifact already exists")
    _atomic_write(raw_path, compressed)
    digest = hashlib.sha256(compressed).hexdigest()
    manifest = redact_record(
        {
            "schema_version": 1,
            "session_id": session.session_id,
            "capture_quality": quality,
            "sha256": digest,
            "compressed_bytes": len(compressed),
            "artifact_locator": f"anvil-evidence://observability/{filename}",
            "frame_count": len(frames),
            "gap_count": analysis["gap_count"],
            "time_alignment": analysis["time_alignment"],
            "domain_metrics": analysis["domain_metrics"],
            "sampling_intervals": dict(metadata.sampling_intervals),
            "capabilities": sorted(set(metadata.capabilities)),
            "host_count": len(metadata.hosts),
            "hardware_count": len(metadata.hardware),
            "failure_count": len(session.failures),
        },
        secrets=secrets,
    )
    output_finding = None
    if finding_path is not None:
        output_finding = Path(finding_path).resolve()
        if output_finding.exists():
            raise FileExistsError("benchmark telemetry finding already exists")
        _write_finding(output_finding, manifest)
        if findings_index is not None:
            _link_finding(Path(findings_index).resolve(), output_finding)
    return ArtifactResult(raw_path, manifest, output_finding)


def _analyze(frames: Sequence[RetainedFrame]) -> dict[str, Any]:
    gaps = [
        {
            "observed_at": frame.as_dict()["observed_at"],
            "resolution_seconds": frame.resolution_seconds,
        }
        for frame in frames
        if frame.gap
    ]
    ages: list[float] = []
    domains = {name: 0 for name in ("system", "container", "gpu", "service", "other")}
    for frame in frames:
        samples = frame.snapshot.get("samples", [])
        if not isinstance(samples, list):
            continue
        for sample in samples:
            if not isinstance(sample, Mapping):
                continue
            metric = str(sample.get("metric", ""))
            domain = (
                "container"
                if metric.startswith("container.")
                else "gpu"
                if metric.startswith("gpu.")
                else "service"
                if metric.startswith("service.")
                else "system"
                if metric.startswith(("host.", "boundary."))
                else "other"
            )
            domains[domain] += 1
            freshness = sample.get("freshness")
            if isinstance(freshness, Mapping) and isinstance(
                freshness.get("age_seconds"), (int, float)
            ):
                ages.append(abs(float(freshness["age_seconds"])))
    maximum = max(ages, default=0.0)
    return {
        "gap_count": len(gaps),
        "gaps": gaps,
        "domain_metrics": domains,
        "time_alignment": {
            "sample_count": len(ages),
            "max_absolute_skew_seconds": maximum,
            "quality": "good" if maximum <= 2 else "degraded" if maximum <= 30 else "poor",
        },
    }


def _unique_frames(frames: Sequence[RetainedFrame]) -> tuple[RetainedFrame, ...]:
    selected: dict[tuple[str, int, str], RetainedFrame] = {}
    for frame in frames:
        record = frame.as_dict()
        digest = hashlib.sha256(
            json.dumps(record, separators=(",", ":"), sort_keys=True).encode()
        ).hexdigest()
        key = (record["observed_at"], frame.resolution_seconds, digest)
        selected[key] = frame
    return tuple(selected[key] for key in sorted(selected))


def _atomic_write(path: Path, payload: bytes) -> None:
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


def _write_finding(path: Path, manifest: Mapping[str, Any]) -> None:
    title_date = date.today().isoformat()
    domains = manifest["domain_metrics"]
    text = (
        f"# Benchmark telemetry capture — {title_date}\n\n"
        f"- Capture quality: **{manifest['capture_quality']}**\n"
        f"- Artifact: `{manifest['artifact_locator']}`\n"
        f"- SHA-256: `{manifest['sha256']}`\n"
        f"- Compressed size: {manifest['compressed_bytes']} bytes\n"
        f"- Frames / gaps: {manifest['frame_count']} / {manifest['gap_count']}\n"
        f"- Alignment: {manifest['time_alignment']['quality']} "
        f"(max absolute skew {manifest['time_alignment']['max_absolute_skew_seconds']} s)\n"
        f"- Domain samples: system={domains['system']}, container={domains['container']}, "
        f"GPU={domains['gpu']}, service={domains['service']}, other={domains['other']}\n\n"
        "Raw telemetry is retained outside Git. This finding is a sanitized locator and summary.\n"
    )
    _atomic_write(path, text.encode())


def _link_finding(index: Path, finding: Path) -> None:
    text = index.read_text(encoding="utf-8")
    relative = finding.name
    if f"]({relative})" in text:
        return
    marker = "|------|------|---------|\n"
    if marker not in text:
        raise ValueError("findings index table marker is missing")
    row = f"| {date.today().isoformat()} | [{relative}]({relative}) | Sanitized benchmark telemetry capture summary |\n"
    _atomic_write(index, text.replace(marker, marker + row, 1).encode())
