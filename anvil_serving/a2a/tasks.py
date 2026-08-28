"""Project durable Anvil media jobs into A2A 1.0 Tasks and stream events."""

from __future__ import annotations

import json
import time
import datetime as dt
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from ..control_plane.mcp.security import caller_context, require_scope
from ..media.comfyui import ComfyUIClient
from ..media.contracts import JobEvent, JobState, MediaArtifact, MediaJob, TERMINAL_STATES
from ..media.errors import MediaError
from ..media.operations import MediaOperations
from .protocol import MEDIA_TO_TASK_STATE, reject_undeclared_tenant


_BLOCKING_RETURN_STATES = TERMINAL_STATES | {JobState.AWAITING_APPROVAL}


def _context_id(job_id: str) -> str:
    return "ctx_" + job_id.removeprefix("job_")


def _timestamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
            "expiresAt": _timestamp(artifact.expires_at),
        },
    }


def _status(job: MediaJob, event: JobEvent | None = None) -> dict[str, Any]:
    selected = event or job.events[-1]
    status: dict[str, Any] = {
        "state": MEDIA_TO_TASK_STATE[selected.state.value],
        "timestamp": _timestamp(selected.at),
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

    def send_message(
        self,
        params: Mapping[str, Any],
        *,
        caller: Mapping[str, Any],
        force_immediate: bool = False,
    ) -> dict[str, Any]:
        with caller_context(caller):
            identity = require_scope("media:submit")
            request = _media_request(params)
            descriptor = self.operations.registry.get(
                request["workflowId"], request["version"]
            )
            accepted_output_modes = request["acceptedOutputModes"]
            supported_output_modes = {
                "application/json",
                *descriptor.output_mime_types,
            }
            if accepted_output_modes and not (
                set(accepted_output_modes) & supported_output_modes
            ):
                raise MediaError(
                    "content_type_not_supported",
                    "none of the accepted output modes is supported",
                    status=409,
                )
            result = self.operations.workflow_run(
                request["workflowId"],
                request["version"],
                request["parameters"],
                principal=identity.principal,
                idempotency_key=request["idempotencyKey"],
                backend=self.backend,
            )
            job = self.operations.jobs.get(result["job"]["id"], principal=identity.principal)
            if not force_immediate and not request["returnImmediately"]:
                deadline = time.monotonic() + descriptor.timeout_seconds
                # A2A blocking sends also return when execution is interrupted
                # for caller input. Cold media workers use that state to expose
                # the exact human-approval request instead of hanging until the
                # workflow execution timeout.
                while job.state not in _BLOCKING_RETURN_STATES:
                    if time.monotonic() >= deadline:
                        raise MediaError(
                            "a2a_blocking_timeout",
                            "blocking A2A media request exceeded workflow timeout",
                            status=504,
                        )
                    time.sleep(0.05)
                    job = self.operations.jobs.get(job.id, principal=identity.principal)
            return {"task": project_task(job)}

    def get_task(self, task_id: str, *, caller: Mapping[str, Any]) -> dict[str, Any]:
        with caller_context(caller):
            identity = require_scope("media:read")
            return project_task(self.operations.jobs.get(task_id, principal=identity.principal))

    def cancel_task(self, task_id: str, *, caller: Mapping[str, Any]) -> dict[str, Any]:
        with caller_context(caller):
            identity = require_scope("media:cancel")
            existing = self.operations.jobs.get(task_id, principal=identity.principal)
            if existing.state in TERMINAL_STATES:
                raise MediaError(
                    "task_not_cancelable",
                    "terminal media task is not cancelable",
                    status=409,
                )
            result = self.operations.job_cancel(
                task_id, principal=identity.principal, backend=self.backend
            )
            if not result["canceled"]:
                raise MediaError(
                    "task_not_cancelable",
                    "media task is not cancelable in its current state",
                    status=409,
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
            if job.state in _BLOCKING_RETURN_STATES or time.monotonic() >= deadline:
                return
            time.sleep(poll_interval)


def _media_request(params: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(params, Mapping) or set(params) - {
        "tenant",
        "message",
        "configuration",
        "metadata",
    }:
        raise MediaError("invalid_a2a_request", "A2A message request contains unknown fields")
    reject_undeclared_tenant(params)
    try:
        encoded = json.dumps(params, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MediaError("invalid_a2a_request", "A2A request must contain JSON values") from exc
    if len(encoded) > 65536:
        raise MediaError("invalid_a2a_request", "A2A request exceeds its byte limit")
    request_metadata = params.get("metadata", {})
    if not isinstance(request_metadata, Mapping) or len(request_metadata) > 32:
        raise MediaError("invalid_a2a_request", "A2A request metadata is invalid")
    configuration = params.get("configuration", {})
    if not isinstance(configuration, Mapping) or set(configuration) - {
        "acceptedOutputModes",
        "historyLength",
        "taskPushNotificationConfig",
        "returnImmediately",
    }:
        raise MediaError("invalid_a2a_request", "A2A configuration is unsupported")
    if "taskPushNotificationConfig" in configuration:
        raise MediaError(
            "push_notification_not_supported",
            "task push notifications are not supported",
            status=409,
        )
    return_immediately = configuration.get("returnImmediately", False)
    if not isinstance(return_immediately, bool):
        raise MediaError("invalid_a2a_request", "A2A returnImmediately must be boolean")
    accepted_output_modes = configuration.get("acceptedOutputModes", [])
    if (
        not isinstance(accepted_output_modes, list)
        or len(accepted_output_modes) > 16
        or any(
            not isinstance(mode, str) or not mode or len(mode) > 128
            for mode in accepted_output_modes
        )
    ):
        raise MediaError("invalid_a2a_request", "A2A acceptedOutputModes is invalid")
    history_length = configuration.get("historyLength", 0)
    if (
        isinstance(history_length, bool)
        or not isinstance(history_length, int)
        or history_length < 0
        or history_length > 1000
    ):
        raise MediaError("invalid_a2a_request", "A2A historyLength is invalid")
    message = params.get("message")
    if (
        not isinstance(message, Mapping)
        or set(message) - {"role", "parts", "messageId", "contextId", "extensions", "metadata"}
        or message.get("role") != "ROLE_USER"
    ):
        raise MediaError("invalid_a2a_message", "A2A request requires a user message")
    message_id = message.get("messageId")
    if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
        raise MediaError("invalid_a2a_message", "A2A messageId is required")
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
    return {
        **dict(data),
        "parameters": dict(data["parameters"]),
        "returnImmediately": return_immediately,
        "acceptedOutputModes": tuple(accepted_output_modes),
        "historyLength": history_length,
    }


__all__ = ["A2AMediaTasks", "project_artifact", "project_task", "stream_events"]
