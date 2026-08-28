"""Managed functional and capacity qualification for pinned media workflows."""

from __future__ import annotations

import io
import json
import secrets
import struct
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifacts import ArtifactStore
from .bundle import DEFAULT_LOCK, inventory as bundle_inventory
from .comfyui import ComfyUIClient
from .contracts import JobState, MediaArtifact, TERMINAL_STATES
from .errors import MediaError
from .jobs import MediaJobStore
from .operations import MediaOperations
from .worker import MediaJobReconciler
from .workflows import WorkflowRegistry


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _gpu_memory_mib(
    gpu_index: int,
    *,
    runner: CommandRunner = subprocess.run,
) -> tuple[int, int]:
    try:
        completed = runner(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise MediaError("media_qualification_gpu", "GPU memory sampler is unavailable", status=503) from exc
    if completed.returncode != 0 or len(completed.stdout) > 65536:
        raise MediaError("media_qualification_gpu", "GPU memory sampling failed", status=503)
    for line in completed.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            index, used, total = (int(part) for part in parts)
        except ValueError:
            continue
        if index == gpu_index and 0 <= used <= total:
            return used, total
    raise MediaError("media_qualification_gpu", "selected GPU was absent from memory sampling", status=503)


def _png_metadata(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            header = handle.read(32)
    except OSError as exc:
        raise MediaError("media_qualification_decode", "retained image artifact is unavailable") from exc
    if len(header) < 24 or not header.startswith(b"\x89PNG\r\n\x1a\n") or header[12:16] != b"IHDR":
        raise MediaError("media_qualification_decode", "retained image artifact is not a decodable PNG")
    width, height = struct.unpack(">II", header[16:24])
    if width < 1 or height < 1 or width > 16384 or height > 16384:
        raise MediaError("media_qualification_decode", "retained image dimensions are outside policy")
    return {"decodable": True, "format": "png", "width": width, "height": height}


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaError("media_qualification_decode", f"ffprobe {label} is invalid") from exc
    if number < 0 or number != number or number in {float("inf"), float("-inf")}:
        raise MediaError("media_qualification_decode", f"ffprobe {label} is invalid")
    return number


def _ratio(value: Any) -> float:
    if not isinstance(value, str) or "/" not in value:
        return _number(value, "frame rate")
    numerator, denominator = value.split("/", 1)
    divisor = _number(denominator, "frame rate divisor")
    if divisor == 0:
        raise MediaError("media_qualification_decode", "ffprobe frame rate divisor is zero")
    return _number(numerator, "frame rate numerator") / divisor


def _video_metadata(
    path: Path,
    *,
    ffprobe: str,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    try:
        completed = runner(
            [
                ffprobe,
                "-v", "error",
                "-count_frames",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=codec_name,width,height,avg_frame_rate,nb_read_frames:format=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        raise MediaError("media_qualification_decode", "ffprobe is unavailable") from exc
    if completed.returncode != 0 or len(completed.stdout) > 65536:
        raise MediaError("media_qualification_decode", "retained video artifact failed ffprobe")
    try:
        raw = json.loads(completed.stdout)
        streams = raw["streams"]
        stream = streams[0]
        codec = stream["codec_name"]
        width = int(stream["width"])
        height = int(stream["height"])
        frames = int(stream["nb_read_frames"])
        rate = _ratio(stream["avg_frame_rate"])
        duration = _number(raw["format"]["duration"], "duration")
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaError("media_qualification_decode", "ffprobe returned incomplete video metadata") from exc
    if (
        not isinstance(codec, str)
        or not codec
        or len(codec) > 64
        or width < 1
        or height < 1
        or frames < 1
        or rate <= 0
        or duration <= 0
    ):
        raise MediaError("media_qualification_decode", "ffprobe video metadata is outside policy")
    return {
        "decodable": True,
        "format": "mp4",
        "codec": codec,
        "width": width,
        "height": height,
        "frames": frames,
        "frameRate": round(rate, 6),
        "durationSeconds": round(duration, 6),
    }


def _model_provenance(
    lock_path: str | Path,
    workflow_id: str,
    version: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MediaError("media_bundle_invalid", "media bundle lock is unavailable or invalid") from exc
    matches = [
        item for item in lock.get("workflows", [])
        if isinstance(item, Mapping) and item.get("id") == workflow_id and item.get("version") == version
    ]
    if len(matches) != 1 or not isinstance(lock.get("runtime"), Mapping):
        raise MediaError("media_bundle_invalid", "media bundle provenance is incomplete")
    models = [
        {
            "repository": item["repository"],
            "revision": item["revision"],
            "path": item["path"],
            "bytes": item["size"],
            "sha256": item["sha256"],
        }
        for item in matches[0]["models"]
    ]
    return dict(lock["runtime"]), models


def qualify(
    workflow_id: str,
    version: str,
    parameters: Mapping[str, Any],
    *,
    registry: WorkflowRegistry,
    jobs: MediaJobStore,
    artifacts: ArtifactStore,
    backend: ComfyUIClient,
    principal: str,
    lock_path: str | Path = DEFAULT_LOCK,
    models_volume: str,
    gpu_index: int = 0,
    poll_seconds: float = 2.0,
    ffprobe: str = "ffprobe",
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    gpu_runner: CommandRunner = subprocess.run,
    ffprobe_runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    """Run one unavailable candidate under an explicit qualification-only gate."""

    if (
        isinstance(gpu_index, bool)
        or gpu_index < 0
        or isinstance(poll_seconds, bool)
        or not 0.1 <= poll_seconds <= 30
    ):
        raise MediaError("media_qualification_invalid", "qualification sampler settings are invalid")
    descriptor = registry.get(workflow_id, version)
    exact_assets = bundle_inventory(
        workflow_id,
        version,
        lock_path=lock_path,
        models_volume=models_volume,
    )
    if not exact_assets["ready"]:
        raise MediaError(
            "media_qualification_assets",
            "pinned workflow assets are not exact",
            status=409,
            details={"assets": exact_assets["assets"]},
        )
    compatibility = backend.compatibility(descriptor, qualification=True)
    if not compatibility.available:
        raise MediaError(
            "media_qualification_compatibility",
            "candidate workflow is incompatible with the selected backend",
            status=409,
            details=compatibility.as_public_dict(),
        )
    baseline_used, total_vram = _gpu_memory_mib(gpu_index, runner=gpu_runner)
    queue_before = backend.queue()
    operations = MediaOperations(registry, jobs, artifacts)
    started = monotonic()
    submitted = operations.workflow_run(
        workflow_id,
        version,
        parameters,
        principal=principal,
        idempotency_key="qualify-" + secrets.token_urlsafe(24),
        backend=backend,
        qualification=True,
    )
    submitted_at = monotonic()
    job = jobs.get(submitted["job"]["id"], principal=principal)
    peak_used = baseline_used
    queue_running = queue_before["running"]
    queue_pending = queue_before["pending"]
    samples = 1

    def capture(current, output) -> MediaArtifact | None:
        if output.node not in descriptor.output_nodes:
            return None
        if len(descriptor.output_mime_types) != 1:
            raise MediaError("media_qualification_artifact", "workflow output MIME mapping is ambiguous")
        payload = backend.fetch_output(output, max_bytes=descriptor.max_artifact_bytes)
        return artifacts.ingest(
            current,
            io.BytesIO(payload),
            media_type=descriptor.output_mime_types[0],
            max_bytes=descriptor.max_artifact_bytes,
            retention_seconds=descriptor.retention_seconds,
        )

    reconciler = MediaJobReconciler(jobs, backend.history, capture)
    deadline = started + descriptor.timeout_seconds
    while job.state not in TERMINAL_STATES:
        used, observed_total = _gpu_memory_mib(gpu_index, runner=gpu_runner)
        if observed_total != total_vram:
            raise MediaError("media_qualification_gpu", "GPU total memory changed during qualification")
        peak_used = max(peak_used, used)
        queue = backend.queue()
        queue_running = max(queue_running, queue["running"])
        queue_pending = max(queue_pending, queue["pending"])
        samples += 1
        job = reconciler.reconcile(job)
        if job.state in TERMINAL_STATES:
            break
        if monotonic() >= deadline:
            raise MediaError(
                "media_qualification_timeout",
                "media workflow qualification exceeded its declared timeout",
                status=504,
                details={"jobId": job.id, "state": job.state.value},
            )
        sleep(poll_seconds)
    finished = monotonic()
    if job.state != JobState.COMPLETED or not job.artifacts:
        raise MediaError(
            "media_qualification_failed",
            "media workflow qualification did not complete with an artifact",
            status=502,
            details={"jobId": job.id, "state": job.state.value},
        )
    decoded = []
    for artifact in job.artifacts:
        path = Path(artifact.source_path)
        metadata = (
            _png_metadata(path)
            if artifact.media_type == "image/png"
            else _video_metadata(path, ffprobe=ffprobe, runner=ffprobe_runner)
        )
        decoded.append({"artifactId": artifact.id, **metadata})
    runtime, models = _model_provenance(lock_path, workflow_id, version)
    latency = max(0.0, finished - started)
    return {
        "schema": "anvil-serving.media-qualification/v1",
        "passed": True,
        "promoted": False,
        "workflow": {
            "id": descriptor.id,
            "version": descriptor.version,
            "kind": descriptor.kind,
            "graphSha256": descriptor.graph_digest,
        },
        "runtime": runtime,
        "models": models,
        "compatibility": compatibility.as_public_dict(),
        "job": {
            "id": job.id,
            "submittedState": submitted["job"]["state"],
            "finalState": job.state.value,
            "immediateReturnSeconds": round(max(0.0, submitted_at - started), 6),
            "events": [event.state.value for event in job.events],
        },
        "artifacts": [artifact.as_public_dict() for artifact in job.artifacts],
        "decoding": decoded,
        "capacity": {
            "latencySeconds": round(latency, 6),
            "successfulTasksPerHour": round(3600 / latency, 6) if latency > 0 else None,
            "baselineVramMiB": baseline_used,
            "peakVramMiB": peak_used,
            "totalVramMiB": total_vram,
            "queueBefore": queue_before,
            "maxQueueRunning": queue_running,
            "maxQueuePending": queue_pending,
            "samples": samples,
        },
        "limits": descriptor.as_public_dict()["limits"],
        "quality": {
            "status": "human_required",
            "owner": "independent_perceptual_reviewer",
            "transportSuccessIsNotQualityApproval": True,
        },
        "rollback": {
            "managedSeparately": True,
            "requiredBeforePublication": True,
        },
    }


__all__ = ["qualify"]
