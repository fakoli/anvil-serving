"""Progress observation and history-authoritative media job reconciliation."""

from __future__ import annotations

import hashlib
import io
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .backends import BackendOutput, BackendStatus
from .artifacts import ArtifactStore
from .comfyui import ComfyUIClient
from .contracts import JobState, MediaArtifact, MediaJob, TERMINAL_STATES
from .errors import MediaError
from .jobs import MediaJobStore
from .workflows import WorkflowRegistry


@dataclass(frozen=True)
class ProgressUpdate:
    prompt_id: str
    state: str
    progress: float | None = None


def normalize_progress_event(event: Mapping[str, Any], *, prompt_id: str) -> ProgressUpdate | None:
    """Normalize one already-bounded ComfyUI WS event for the owned prompt only."""
    if not isinstance(event, Mapping) or len(event) > 16:
        raise MediaError("backend_event_invalid", "ComfyUI progress event is invalid")
    event_type = event.get("type")
    data = event.get("data")
    if not isinstance(event_type, str) or not isinstance(data, Mapping) or len(data) > 32:
        raise MediaError("backend_event_invalid", "ComfyUI progress event is invalid")
    observed = data.get("prompt_id")
    if observed != prompt_id:
        return None
    if event_type in {"execution_start", "executing"}:
        return ProgressUpdate(prompt_id, "running")
    if event_type == "progress":
        value = data.get("value")
        maximum = data.get("max")
        if (
            isinstance(value, bool)
            or isinstance(maximum, bool)
            or not isinstance(value, (int, float))
            or not isinstance(maximum, (int, float))
            or maximum <= 0
            or value < 0
            or value > maximum
        ):
            raise MediaError("backend_event_invalid", "ComfyUI progress values are invalid")
        return ProgressUpdate(prompt_id, "running", min(float(value / maximum), 1.0))
    if event_type == "execution_success":
        return ProgressUpdate(prompt_id, "completed", 1.0)
    if event_type in {"execution_error", "execution_interrupted"}:
        return ProgressUpdate(prompt_id, "failed")
    return None


class MediaJobReconciler:
    """Reconcile durable jobs from history; never submit or start backend work."""

    def __init__(
        self,
        store: MediaJobStore,
        history: Callable[[str], BackendStatus],
        capture: Callable[[MediaJob, BackendOutput], MediaArtifact] | None = None,
    ) -> None:
        self.store = store
        self.history = history
        self.capture = capture

    def reconcile(self, job: MediaJob) -> MediaJob:
        if job.state in TERMINAL_STATES:
            return job
        if not job.backend_prompt_id:
            return job
        status = self.history(job.backend_prompt_id)
        if status.state == "queued":
            return self._advance(job, JobState.QUEUED)
        if status.state == "running":
            current = self._advance(job, JobState.QUEUED)
            return self._advance(current, JobState.RUNNING)
        if status.state == "failed":
            return self.store.transition(
                job.id,
                JobState.FAILED,
                principal=job.principal,
                reason=status.error_code or "backend_failed",
            )
        if status.state == "completed":
            current = self._advance(job, JobState.QUEUED)
            current = self._advance(current, JobState.RUNNING)
            if self.capture is None and status.outputs:
                raise MediaError("artifact_capture_unavailable", "completed backend output cannot be retained", status=503)
            for output in status.outputs:
                artifact = self.capture(current, output) if self.capture is not None else None
                if artifact is not None:
                    current = self.store.add_artifact(artifact)
            if not current.artifacts:
                return self.store.transition(
                    current.id,
                    JobState.FAILED,
                    principal=current.principal,
                    reason="backend_output_missing",
                )
            return self.store.transition(
                current.id,
                JobState.COMPLETED,
                principal=current.principal,
                reason="completed",
            )
        return job

    def reconcile_all(self) -> list[MediaJob]:
        results: list[MediaJob] = []
        for job in self.store.nonterminal():
            try:
                results.append(self.reconcile(job))
            except MediaError:
                results.append(job)
        return results

    def _advance(self, job: MediaJob, target: JobState) -> MediaJob:
        order = {
            JobState.ACCEPTED: 0,
            JobState.PREPARING: 1,
            JobState.QUEUED: 2,
            JobState.RUNNING: 3,
        }
        if job.state == JobState.AWAITING_APPROVAL:
            return job
        if job.state not in order or order[job.state] >= order[target]:
            return job
        current = job
        for state in (JobState.PREPARING, JobState.QUEUED, JobState.RUNNING):
            if order[state] <= order[current.state] or order[state] > order[target]:
                continue
            current = self.store.transition(current.id, state, principal=current.principal)
        return current


class MediaArtifactCapture:
    """Copy one allowlisted backend output into the durable artifact boundary."""

    def __init__(
        self,
        registry: WorkflowRegistry,
        artifacts: ArtifactStore,
        backend: ComfyUIClient,
    ) -> None:
        self.registry = registry
        self.artifacts = artifacts
        self.backend = backend

    def __call__(self, job: MediaJob, output: BackendOutput) -> MediaArtifact | None:
        descriptor = self.registry.get(job.workflow_id, job.workflow_version)
        if output.node not in descriptor.output_nodes:
            return None
        if len(descriptor.output_mime_types) != 1:
            raise MediaError(
                "artifact_output_ambiguous",
                "workflow output MIME mapping is ambiguous",
                status=500,
            )
        media_type = descriptor.output_mime_types[0]
        payload = self.backend.fetch_output(
            output,
            max_bytes=descriptor.max_artifact_bytes,
        )
        digest = hashlib.sha256(payload).hexdigest()
        for artifact in job.artifacts:
            if (
                artifact.media_type == media_type
                and artifact.byte_length == len(payload)
                and artifact.sha256 == digest
            ):
                return artifact
        return self.artifacts.ingest(
            job,
            io.BytesIO(payload),
            media_type=media_type,
            max_bytes=descriptor.max_artifact_bytes,
            retention_seconds=descriptor.retention_seconds,
        )


class MediaReconciliationLoop:
    """Own one bounded daemon that advances restart-safe media jobs."""

    def __init__(
        self,
        reconciler: MediaJobReconciler,
        *,
        poll_seconds: float = 0.25,
        maintenance: Callable[[], Any] | None = None,
        maintenance_cycles: int = 240,
    ) -> None:
        if poll_seconds <= 0 or poll_seconds > 5:
            raise MediaError("invalid_worker_policy", "media reconciliation poll interval is invalid")
        if maintenance_cycles < 1:
            raise MediaError("invalid_worker_policy", "media maintenance interval is invalid")
        self.reconciler = reconciler
        self.poll_seconds = poll_seconds
        self.maintenance = maintenance
        self.maintenance_cycles = maintenance_cycles
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self.is_alive:
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="anvil-media-reconciler",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout: float = 6.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))

    def reconcile_once(self) -> list[MediaJob]:
        return self.reconciler.reconcile_all()

    def _run(self) -> None:
        cycle = 0
        while not self._stop.is_set():
            try:
                self.reconcile_once()
                if self.maintenance is not None and cycle % self.maintenance_cycles == 0:
                    self.maintenance()
            except Exception:
                # Durable state remains authoritative; one unexpected cycle
                # must not silently kill reconciliation for every later job.
                pass
            cycle += 1
            self._stop.wait(self.poll_seconds)


__all__ = [
    "MediaArtifactCapture",
    "MediaJobReconciler",
    "MediaReconciliationLoop",
    "ProgressUpdate",
    "normalize_progress_event",
]
