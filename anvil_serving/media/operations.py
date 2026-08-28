"""Protocol-neutral media operations shared by CLI, MCP, and A2A adapters."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Mapping

from .admission import MediaAdmissionService
from .artifacts import ArtifactStore
from .cancellation import MediaCancellationService
from .comfyui import ComfyUIClient
from .contracts import JobState
from .errors import MediaError
from .jobs import MediaJobStore
from .workflows import WorkflowRegistry


class MediaOperations:
    """Small bounded application service; adapters only translate protocols."""

    def __init__(
        self,
        registry: WorkflowRegistry,
        jobs: MediaJobStore,
        artifacts: ArtifactStore,
    ) -> None:
        self.registry = registry
        self.jobs = jobs
        self.artifacts = artifacts

    def capabilities(self) -> dict[str, Any]:
        workflows = self.registry.list()
        return {
            "capabilities": ["image-generation", "video-generation", "named-workflow"],
            "workflows": workflows,
            "operations": [
                "workflow.list",
                "workflow.show",
                "workflow.validate",
                "workflow.run",
                "job.status",
                "job.cancel",
                "artifact.inspect",
            ],
        }

    def workflow_list(self) -> dict[str, Any]:
        return {"workflows": self.registry.list()}

    def workflow_show(self, workflow_id: str, version: str) -> dict[str, Any]:
        return {"workflow": self.registry.get(workflow_id, version).as_public_dict()}

    def workflow_validate(
        self, workflow_id: str, version: str, *, backend: ComfyUIClient
    ) -> dict[str, Any]:
        workflow = self.registry.get(workflow_id, version)
        return {"compatibility": backend.compatibility(workflow).as_public_dict()}

    def workflow_run(
        self,
        workflow_id: str,
        version: str,
        parameters: Mapping[str, Any],
        *,
        principal: str,
        idempotency_key: str,
        backend: ComfyUIClient,
        qualification: bool = False,
    ) -> dict[str, Any]:
        rendered = self.registry.render(workflow_id, version, parameters)
        if qualification:
            candidate = replace(rendered.descriptor, available=True, unavailable_reasons=())
            rendered = replace(rendered, descriptor=candidate)
        existing = self.jobs.lookup_idempotency(
            principal=principal,
            workflow_id=workflow_id,
            workflow_version=version,
            input_digest=rendered.parameters_digest,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return {"job": existing.as_public_dict(), "created": False}
        compatibility = (
            backend.compatibility(rendered.descriptor, qualification=True)
            if qualification
            else backend.compatibility(rendered.descriptor)
        )
        decision = MediaAdmissionService(self.jobs).evaluate(
            rendered.descriptor,
            parameters,
            principal=principal,
            backend_ready=compatibility.available,
        )
        if not decision.allowed:
            raise MediaError(
                "media_admission_rejected",
                "media request was not admitted",
                status=409 if decision.state == "rejected" else 503,
                details=decision.as_dict(),
            )
        job, created = self.jobs.create(
            principal=principal,
            workflow_id=workflow_id,
            workflow_version=version,
            input_digest=rendered.parameters_digest,
            idempotency_key=idempotency_key,
        )
        if not created:  # a concurrent identical request won the unique key
            return {"job": job.as_public_dict(), "created": False}
        try:
            prompt_id = backend.submit(rendered, job_id=job.id)
            self.jobs.set_backend_prompt(job.id, prompt_id, principal=principal)
            job = self.jobs.transition(
                job.id,
                JobState.QUEUED,
                principal=principal,
                reason="submitted_to_media_backend",
            )
        except MediaError as exc:
            self.jobs.transition(
                job.id,
                JobState.FAILED,
                principal=principal,
                reason=exc.code,
            )
            raise
        return {"job": job.as_public_dict(), "created": True}

    def job_status(self, job_id: str, *, principal: str) -> dict[str, Any]:
        return {"job": self.jobs.get(job_id, principal=principal).as_public_dict()}

    def job_cancel(
        self, job_id: str, *, principal: str, backend: ComfyUIClient
    ) -> dict[str, Any]:
        cancellation = MediaCancellationService(
            self.jobs,
            delete_queued=backend.delete_queued_prompt,
            interrupt_exclusive=backend.interrupt_exclusive_prompt,
            # A generic CLI observer cannot prove prompt-specific exclusive
            # slot ownership from aggregate queue counts, so it fails closed.
            owns_active_slot=lambda _job: False,
        )
        return cancellation.cancel(job_id, principal=principal).as_public_dict()

    def artifact_inspect(self, artifact_id: str, *, principal: str) -> dict[str, Any]:
        return {"artifact": self.artifacts.metadata(artifact_id, principal=principal).as_public_dict()}


def parameters_from_json(raw: str, *, max_bytes: int = 65536) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > max_bytes:
        raise MediaError("invalid_parameters", "workflow parameters exceed the CLI input limit")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MediaError("invalid_parameters", "workflow parameters are not valid JSON") from exc
    if not isinstance(value, dict):
        raise MediaError("invalid_parameters", "workflow parameters must be a JSON object")
    return value


def stable_request_key(workflow_id: str, version: str, parameters: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"workflow": workflow_id, "version": version, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "cli-" + hashlib.sha256(payload).hexdigest()


__all__ = ["MediaOperations", "parameters_from_json", "stable_request_key"]
