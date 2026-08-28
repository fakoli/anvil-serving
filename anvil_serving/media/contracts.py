"""Immutable, protocol-neutral contracts for managed media generation."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import MediaError


_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_MIME_RE = re.compile(r"^(?:image|video)/[a-z0-9][a-z0-9.+-]{0,63}$")
SUBMISSION_RECOVERY_GRACE_SECONDS = 30


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _timestamp(value: dt.datetime, field_name: str) -> dt.datetime:
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        raise MediaError("invalid_contract", f"{field_name} must be timezone-aware")
    return value.astimezone(dt.timezone.utc)


def _identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise MediaError("invalid_contract", f"{field_name} is invalid")
    return value


def _principal(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise MediaError("invalid_contract", f"{field_name} is invalid")
    return value


class JobState(str, Enum):
    ACCEPTED = "accepted"
    AWAITING_APPROVAL = "awaiting_approval"
    PREPARING = "preparing"
    SUBMITTING = "submitting"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


TERMINAL_STATES = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELED})
_TRANSITIONS = {
    JobState.ACCEPTED: frozenset(
        {JobState.AWAITING_APPROVAL, JobState.PREPARING, JobState.QUEUED, JobState.FAILED, JobState.CANCELED}
    ),
    JobState.AWAITING_APPROVAL: frozenset(
        {
            JobState.AWAITING_APPROVAL,
            JobState.PREPARING,
            JobState.FAILED,
            JobState.CANCELED,
        }
    ),
    JobState.PREPARING: frozenset(
        {
            JobState.AWAITING_APPROVAL,
            JobState.SUBMITTING,
            JobState.QUEUED,
            JobState.FAILED,
            JobState.CANCELED,
        }
    ),
    JobState.SUBMITTING: frozenset(
        {JobState.QUEUED, JobState.FAILED}
    ),
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.FAILED, JobState.CANCELED}),
    JobState.RUNNING: frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELED}),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELED: frozenset(),
}


@dataclass(frozen=True)
class ParameterSpec:
    """One bounded caller-settable workflow parameter."""

    kind: str
    required: bool = True
    minimum: int | float | None = None
    maximum: int | float | None = None
    max_length: int | None = None
    enum: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"string", "integer", "number", "boolean", "artifact"}:
            raise MediaError("invalid_contract", "parameter kind is invalid")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise MediaError("invalid_contract", "parameter bounds are reversed")
        if self.max_length is not None and (isinstance(self.max_length, bool) or self.max_length < 1):
            raise MediaError("invalid_contract", "parameter max_length is invalid")
        if len(self.enum) > 64 or any(not isinstance(item, str) or not item for item in self.enum):
            raise MediaError("invalid_contract", "parameter enum is invalid")

    def validate(self, name: str, value: Any) -> Any:
        if self.kind in {"string", "artifact"}:
            if not isinstance(value, str):
                raise MediaError("invalid_parameter", f"{name} must be a string")
            if self.max_length is not None and len(value) > self.max_length:
                raise MediaError("invalid_parameter", f"{name} is too long")
            if self.kind == "artifact" and not _OPAQUE_RE.fullmatch(value):
                raise MediaError("invalid_artifact", f"{name} is not an opaque artifact id")
            if self.enum and value not in self.enum:
                raise MediaError("invalid_parameter", f"{name} is not an allowed value")
            return value
        if self.kind == "boolean":
            if not isinstance(value, bool):
                raise MediaError("invalid_parameter", f"{name} must be boolean")
            return value
        if self.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise MediaError("invalid_parameter", f"{name} must be an integer")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MediaError("invalid_parameter", f"{name} must be numeric")
        if self.minimum is not None and value < self.minimum:
            raise MediaError("invalid_parameter", f"{name} is below its minimum")
        if self.maximum is not None and value > self.maximum:
            raise MediaError("invalid_parameter", f"{name} exceeds its maximum")
        return value

    def as_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": "string" if self.kind == "artifact" else self.kind,
        }
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.max_length is not None:
            schema["maxLength"] = self.max_length
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.kind == "artifact":
            schema["format"] = "anvil-artifact-id"
        return schema


@dataclass(frozen=True)
class ParameterBinding:
    parameter: str
    node: str
    input: str

    def __post_init__(self) -> None:
        _identifier(self.parameter, "binding parameter")
        if not self.node.isdigit() or not self.input or len(self.input) > 128:
            raise MediaError("invalid_contract", "workflow binding is invalid")


@dataclass(frozen=True)
class WorkflowDescriptor:
    """A versioned allowlisted workflow with one logical target."""

    id: str
    version: str
    kind: str
    service_target: str
    graph_digest: str
    parameters: Mapping[str, ParameterSpec]
    bindings: tuple[ParameterBinding, ...]
    output_nodes: tuple[str, ...]
    output_mime_types: tuple[str, ...]
    required_features: tuple[str, ...] = ()
    required_nodes: tuple[str, ...] = ()
    required_models: tuple[str, ...] = ()
    available: bool = False
    unavailable_reasons: tuple[str, ...] = ("qualification_required",)
    max_request_bytes: int = 65536
    max_artifact_bytes: int = 33554432
    timeout_seconds: int = 600
    retention_seconds: int = 86400
    max_queue_depth: int = 4
    max_concurrency: int = 1

    def __post_init__(self) -> None:
        _identifier(self.id, "workflow id")
        _identifier(self.version, "workflow version")
        _identifier(self.service_target, "service target")
        if self.kind not in {"image", "video"}:
            raise MediaError("invalid_contract", "workflow kind is invalid")
        if not _HEX64_RE.fullmatch(self.graph_digest):
            raise MediaError("invalid_contract", "workflow graph digest is invalid")
        if not self.parameters or len(self.parameters) > 32:
            raise MediaError("invalid_contract", "workflow parameter schema is invalid")
        for name, spec in self.parameters.items():
            _identifier(name, "parameter name")
            if not isinstance(spec, ParameterSpec):
                raise MediaError("invalid_contract", "workflow parameter specification is invalid")
        if {binding.parameter for binding in self.bindings} - set(self.parameters):
            raise MediaError("invalid_contract", "workflow binding names an unknown parameter")
        if not self.output_nodes or any(not item.isdigit() for item in self.output_nodes):
            raise MediaError("invalid_contract", "workflow output nodes are invalid")
        if not self.output_mime_types or any(not _MIME_RE.fullmatch(item) for item in self.output_mime_types):
            raise MediaError("invalid_contract", "workflow output MIME types are invalid")
        for value in (
            self.max_request_bytes,
            self.max_artifact_bytes,
            self.timeout_seconds,
            self.retention_seconds,
            self.max_queue_depth,
            self.max_concurrency,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise MediaError("invalid_contract", "workflow limit is invalid")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def validate_parameters(self, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise MediaError("invalid_parameters", "workflow parameters must be an object")
        unknown = sorted(set(values) - set(self.parameters))
        if unknown:
            raise MediaError("unknown_parameter", "workflow parameters contain unknown fields", details={"fields": unknown})
        missing = sorted(name for name, spec in self.parameters.items() if spec.required and name not in values)
        if missing:
            raise MediaError("missing_parameter", "workflow parameters are incomplete", details={"fields": missing})
        return {name: self.parameters[name].validate(name, value) for name, value in values.items()}

    def as_public_dict(self) -> dict[str, Any]:
        required = [name for name, spec in self.parameters.items() if spec.required]
        return {
            "id": self.id,
            "version": self.version,
            "kind": self.kind,
            "schema": {
                "type": "object",
                "properties": {name: spec.as_schema() for name, spec in self.parameters.items()},
                "required": required,
                "additionalProperties": False,
                "maxProperties": len(self.parameters),
            },
            "outputMimeTypes": list(self.output_mime_types),
            "digest": self.graph_digest,
            "available": self.available,
            "unavailableReasons": list(self.unavailable_reasons),
            "limits": {
                "requestBytes": self.max_request_bytes,
                "artifactBytes": self.max_artifact_bytes,
                "timeoutSeconds": self.timeout_seconds,
                "retentionSeconds": self.retention_seconds,
                "queueDepth": self.max_queue_depth,
                "concurrency": self.max_concurrency,
            },
        }


@dataclass(frozen=True)
class RenderedWorkflow:
    descriptor: WorkflowDescriptor
    graph: Mapping[str, Any]
    parameters_digest: str


@dataclass(frozen=True)
class MediaArtifact:
    id: str
    job_id: str
    principal: str
    workflow_id: str
    workflow_version: str
    media_type: str
    byte_length: int
    sha256: str
    expires_at: dt.datetime
    source_path: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if not _OPAQUE_RE.fullmatch(self.id) or not _OPAQUE_RE.fullmatch(self.job_id):
            raise MediaError("invalid_contract", "artifact identity is invalid")
        _principal(self.principal, "artifact principal")
        _identifier(self.workflow_id, "artifact workflow")
        _identifier(self.workflow_version, "artifact workflow version")
        if not _MIME_RE.fullmatch(self.media_type):
            raise MediaError("invalid_contract", "artifact MIME type is invalid")
        if isinstance(self.byte_length, bool) or self.byte_length < 1:
            raise MediaError("invalid_contract", "artifact byte length is invalid")
        if not _HEX64_RE.fullmatch(self.sha256):
            raise MediaError("invalid_contract", "artifact digest is invalid")
        _timestamp(self.expires_at, "artifact expiry")

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "jobId": self.job_id,
            "mediaType": self.media_type,
            "byteLength": self.byte_length,
            "sha256": self.sha256,
            "workflow": {"id": self.workflow_id, "version": self.workflow_version},
            "expiresAt": self.expires_at.isoformat(),
            "resource": f"/artifacts/{self.id}",
        }


@dataclass(frozen=True)
class JobEvent:
    sequence: int
    state: JobState
    at: dt.datetime
    reason: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or self.sequence < 1:
            raise MediaError("invalid_contract", "job event sequence is invalid")
        _timestamp(self.at, "job event timestamp")
        if len(self.reason) > 256:
            raise MediaError("invalid_contract", "job event reason is too long")


@dataclass(frozen=True)
class MediaJob:
    id: str
    principal: str
    workflow_id: str
    workflow_version: str
    state: JobState
    created_at: dt.datetime
    updated_at: dt.datetime
    events: tuple[JobEvent, ...]
    artifacts: tuple[MediaArtifact, ...] = ()
    approval: Mapping[str, Any] | None = None
    backend_prompt_id: str = field(default="", repr=False, compare=False)
    input_digest: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if not _OPAQUE_RE.fullmatch(self.id):
            raise MediaError("invalid_contract", "job identity is invalid")
        _principal(self.principal, "job principal")
        _identifier(self.workflow_id, "job workflow")
        _identifier(self.workflow_version, "job workflow version")
        _timestamp(self.created_at, "job created_at")
        _timestamp(self.updated_at, "job updated_at")
        if not self.events or self.events[-1].state != self.state:
            raise MediaError("invalid_contract", "job event history does not match state")
        if tuple(event.sequence for event in self.events) != tuple(range(1, len(self.events) + 1)):
            raise MediaError("invalid_contract", "job event sequence is not contiguous")
        if self.input_digest and not _HEX64_RE.fullmatch(self.input_digest):
            raise MediaError("invalid_contract", "job input digest is invalid")

    def transition(self, state: JobState, *, reason: str = "", at: dt.datetime | None = None) -> "MediaJob":
        if not isinstance(state, JobState):
            state = JobState(state)
        if state not in _TRANSITIONS[self.state]:
            raise MediaError(
                "invalid_transition",
                f"job cannot transition from {self.state.value} to {state.value}",
                status=409,
            )
        changed = _timestamp(at or utc_now(), "job transition timestamp")
        if changed < self.updated_at:
            raise MediaError("invalid_transition", "job transition timestamp moved backwards", status=409)
        event = JobEvent(len(self.events) + 1, state, changed, reason)
        return replace(self, state=state, updated_at=changed, events=self.events + (event,))

    def as_public_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "workflow": {"id": self.workflow_id, "version": self.workflow_version},
            "state": self.state.value,
            "sequence": self.events[-1].sequence,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "artifacts": [artifact.as_public_dict() for artifact in self.artifacts],
        }
        if self.approval is not None:
            result["approval"] = dict(self.approval)
        if self.state in TERMINAL_STATES and self.events[-1].reason:
            result["terminalReason"] = self.events[-1].reason
        return result


@runtime_checkable
class MediaBackend(Protocol):
    def submit(self, workflow: RenderedWorkflow, *, job_id: str) -> str: ...
    def find_prompt(self, job_id: str) -> str | None: ...
    def status(self, prompt_id: str) -> Mapping[str, Any]: ...
    def cancel(self, prompt_id: str) -> bool: ...


__all__ = [
    "JobEvent",
    "JobState",
    "MediaArtifact",
    "MediaBackend",
    "MediaJob",
    "ParameterBinding",
    "ParameterSpec",
    "RenderedWorkflow",
    "SUBMISSION_RECOVERY_GRACE_SECONDS",
    "TERMINAL_STATES",
    "WorkflowDescriptor",
    "utc_now",
]
