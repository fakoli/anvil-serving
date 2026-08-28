"""Project durable Anvil media jobs into A2A 1.0 Tasks and stream events."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from ..control_plane.mcp.security import caller_context, require_scope
from ..media.comfyui import ComfyUIClient
from ..media.contracts import JobEvent, JobState, MediaArtifact, MediaJob, TERMINAL_STATES
from ..media.errors import MediaError
from ..media.operations import MediaOperations
from .protocol import MEDIA_TO_TASK_STATE


def _context_id(job_id: str) -> str:
    return "ctx_" + job_id.removeprefix("job_")


def project_artifact(artifact: MediaArtifact) -> dict[str, Any]:
    return {
        "artifactId": artifact.id,
        "name": f"{artifact.workflow_id}-{artifact.id[-8:]}",
        "parts": [
            {
                "url": f"/artifacts/{artifact.id}",
                "filename": f"{artifact.id}.{'png' if artifact.media_type == 'image/png' else 'mp4'}",
                "mediaType": artifact.media_type,
            }
        ],
        "metadata": {
            "byteLength": artifact.byte_length,
            "sha256": artifact.sha256,
            "workflow": {
                "id": artifact.workflow_id,
                "version": artifact.workflow_version,
            },
            "expiresAt": artifact.expires_at.isoformat(),
        },
    }


def _status(job: MediaJob, event: JobEvent | None = None) -> dict[str, Any]:
    selected = event or job.events[-1]
    status: dict[str, Any] = {
        "state": MEDIA_TO_TASK_STATE[selected.state.value],
        "timestamp": selected.at.isoformat(),
    }
    if selected.state == JobState.AWAITING_APPROVAL:
        status["message"] = {
            "role": "ROLE_AGENT",
            "messageId": f"approval-{job.id}-{selected.sequence}",
            "parts": [{"text": "Media worker lifecycle approval is required."}],
        }
    return status


def project_task(job: MediaJob, *, event: JobEvent | None = None) -> dict[str, Any]:
    selected = event or job.events[-1]
    return {
        "id": job.id,
        "contextId": _context_id(job.id),
        "status": _status(job, selected),
        "artifacts": (
            [project_artifact(artifact) for artifact in job.artifacts]
            if selected.state == JobState.COMPLETED
            else []
        ),
        "metadata": {
            "sequence": selected.sequence,
            "workflow": {"id": job.workflow_id, "version": job.workflow_version},
        },
    }


def stream_events(job: MediaJob, *, after_sequence: int = 0) -> list[dict[str, Any]]:
    if isinstance(after_sequence, bool) or after_sequence < 0:
        raise MediaError("invalid_stream_cursor", "A2A stream cursor is invalid")
    events: list[dict[str, Any]] = []
    if after_sequence == 0:
        events.append({"task": project_task(job, event=job.events[0])})
    for event in job.events:
        if event.sequence <= max(1, after_sequence):
            continue
        if event.state == JobState.COMPLETED:
            for artifact in job.artifacts:
                events.append(
                    {
                        "artifactUpdate": {
                            "taskId": job.id,
                            "contextId": _context_id(job.id),
                            "artifact": project_artifact(artifact),
                            "metadata": {"sequence": event.sequence},
                        }
                    }
                )
        events.append(
            {
                "statusUpdate": {
                    "taskId": job.id,
                    "contextId": _context_id(job.id),
                    "status": _status(job, event),
                    "metadata": {"sequence": event.sequence},
                }
            }
        )
    return events


class A2AMediaTasks:
    def __init__(self, operations: MediaOperations, backend: ComfyUIClient) -> None:
        self.operations = operations
        self.backend = backend

    def send_message(self, params: Mapping[str, Any], *, caller: Mapping[str, Any]) -> dict[str, Any]:
        with caller_context(caller):
            identity = require_scope("media:submit")
            request = _media_request(params)
            result = self.operations.workflow_run(
                request["workflowId"],
                request["version"],
                request["parameters"],
                principal=identity.principal,
                idempotency_key=request["idempotencyKey"],
                backend=self.backend,
            )
            job = self.operations.jobs.get(result["job"]["id"], principal=identity.principal)
            return {"task": project_task(job)}

    def get_task(self, task_id: str, *, caller: Mapping[str, Any]) -> dict[str, Any]:
        with caller_context(caller):
            identity = require_scope("media:read")
            return project_task(self.operations.jobs.get(task_id, principal=identity.principal))

    def cancel_task(self, task_id: str, *, caller: Mapping[str, Any]) -> dict[str, Any]:
        with caller_context(caller):
            identity = require_scope("media:cancel")
            result = self.operations.job_cancel(
                task_id, principal=identity.principal, backend=self.backend
            )
            job_id = result["job"]["id"]
            return project_task(self.operations.jobs.get(job_id, principal=identity.principal))

    def observe(
        self,
        task_id: str,
        *,
        caller: Mapping[str, Any],
        after_sequence: int = 0,
        timeout_seconds: float = 600,
        poll_interval: float = 0.25,
        disconnected: Callable[[], bool] = lambda: False,
    ) -> Iterator[dict[str, Any]]:
        if timeout_seconds <= 0 or timeout_seconds > 3600 or poll_interval <= 0 or poll_interval > 5:
            raise MediaError("invalid_stream_policy", "A2A stream policy is outside bounds")
        with caller_context(caller):
            identity = require_scope("media:read")
        cursor = after_sequence
        first = cursor == 0
        deadline = time.monotonic() + timeout_seconds
        while True:
            if disconnected():
                return
            job = self.operations.jobs.get(task_id, principal=identity.principal)
            updates = stream_events(job, after_sequence=cursor)
            if first and updates:
                first = False
            for update in updates:
                yield update
            cursor = max(cursor, job.events[-1].sequence)
            if job.state in TERMINAL_STATES or time.monotonic() >= deadline:
                return
            time.sleep(poll_interval)


def _media_request(params: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(params, Mapping) or set(params) - {"message", "configuration", "metadata"}:
        raise MediaError("invalid_a2a_request", "A2A message request contains unknown fields")
    try:
        encoded = json.dumps(params, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MediaError("invalid_a2a_request", "A2A request must contain JSON values") from exc
    if len(encoded) > 65536:
        raise MediaError("invalid_a2a_request", "A2A request exceeds its byte limit")
    configuration = params.get("configuration", {})
    if not isinstance(configuration, Mapping) or set(configuration) - {"returnImmediately"}:
        raise MediaError("invalid_a2a_request", "A2A configuration is unsupported")
    if "returnImmediately" in configuration and configuration["returnImmediately"] is not True:
        raise MediaError("invalid_a2a_request", "media tasks require returnImmediately=true")
    message = params.get("message")
    if (
        not isinstance(message, Mapping)
        or set(message) - {"role", "parts", "messageId", "contextId", "extensions", "metadata"}
        or message.get("role") != "ROLE_USER"
    ):
        raise MediaError("invalid_a2a_message", "A2A request requires a user message")
    parts = message.get("parts")
    if not isinstance(parts, list) or len(parts) != 1 or not isinstance(parts[0], Mapping):
        raise MediaError("invalid_a2a_message", "A2A media message requires one structured part")
    if not set(parts[0]) <= {"data", "text"} or len(parts[0]) != 1:
        raise MediaError("invalid_a2a_message", "A2A media part contains unsupported content")
    data = parts[0].get("data")
    if not isinstance(data, Mapping):
        text = parts[0].get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
            raise MediaError("invalid_a2a_message", "A2A media part must contain bounded structured data")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MediaError("invalid_a2a_message", "A2A text part is not structured JSON") from exc
    expected = {"workflowId", "version", "parameters", "idempotencyKey"}
    if not isinstance(data, Mapping) or set(data) != expected:
        raise MediaError("invalid_a2a_message", "A2A media data fields are incomplete or unknown")
    if not isinstance(data["parameters"], Mapping):
        raise MediaError("invalid_a2a_message", "A2A workflow parameters must be an object")
    for field in ("workflowId", "version", "idempotencyKey"):
        if not isinstance(data[field], str) or not data[field] or len(data[field]) > 128:
            raise MediaError("invalid_a2a_message", f"A2A field {field} is invalid")
    return {**dict(data), "parameters": dict(data["parameters"])}


__all__ = ["A2AMediaTasks", "project_artifact", "project_task", "stream_events"]
