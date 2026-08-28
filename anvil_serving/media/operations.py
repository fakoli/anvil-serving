"""Protocol-neutral media operations shared by CLI, MCP, and A2A adapters."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import replace
from typing import Any, Callable, Mapping

from .admission import MediaAdmissionService
from .artifacts import ArtifactStore
from .cancellation import MediaCancellationService
from .comfyui import ComfyUIClient
from .contracts import JobState, SUBMISSION_RECOVERY_GRACE_SECONDS, utc_now
from .errors import MediaError
from .jobs import MediaJobStore
from .workflows import WorkflowRegistry


LifecyclePreview = Callable[[str, str, str], Mapping[str, Any]]


class MediaOperations:
    """Small bounded application service; adapters only translate protocols."""

    def __init__(
        self,
        registry: WorkflowRegistry,
        jobs: MediaJobStore,
        artifacts: ArtifactStore,
        *,
        lifecycle_preview: LifecyclePreview | None = None,
    ) -> None:
        self.registry = registry
        self.jobs = jobs
        self.artifacts = artifacts
        self._lifecycle_preview = lifecycle_preview
        self._submit_lock = threading.RLock()

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
        quality_profile: str | None = None,
    ) -> dict[str, Any]:
        if quality_profile is None:
            rendered = self.registry.render(workflow_id, version, parameters)
        else:
            selected_profile = quality_profile
            if not selected_profile:
                selected_profile = self.registry.get(
                    workflow_id,
                    version,
                ).default_quality_profile
            if selected_profile:
                rendered = self.registry.render(
                    workflow_id,
                    version,
                    parameters,
                    quality_profile=selected_profile,
                )
            else:
                rendered = self.registry.render(workflow_id, version, parameters)
        resolved_parameters = rendered.resolved_parameters or parameters
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
            return self._resume_existing(
                existing.id,
                rendered,
                principal=principal,
                backend=backend,
                qualification=qualification,
            )
        try:
            compatibility = (
                backend.compatibility(rendered.descriptor, qualification=True)
                if qualification
                else backend.compatibility(rendered.descriptor)
            )
        except MediaError as exc:
            if exc.code != "backend_unavailable" or qualification:
                raise
            return self._request_lifecycle_approval(
                rendered,
                resolved_parameters,
                principal=principal,
                idempotency_key=idempotency_key,
            )
        if not compatibility.ready and not qualification:
            return self._request_lifecycle_approval(
                rendered,
                resolved_parameters,
                principal=principal,
                idempotency_key=idempotency_key,
            )
        decision, job, created = MediaAdmissionService(self.jobs).admit(
            rendered.descriptor,
            resolved_parameters,
            principal=principal,
            backend_ready=compatibility.available,
            input_digest=rendered.parameters_digest,
            idempotency_key=idempotency_key,
            quality_profile=rendered.quality_profile,
        )
        if not decision.allowed:
            raise MediaError(
                "media_admission_rejected",
                "media request was not admitted",
                status=409 if decision.state == "rejected" else 503,
                details=decision.as_dict(),
            )
        if job is None:
            raise MediaError("media_admission_rejected", "media request was not admitted", status=503)
        if not created:  # a concurrent identical request won the unique key
            return self._resume_existing(
                job.id,
                rendered,
                principal=principal,
                backend=backend,
                qualification=qualification,
            )
        job = self._submit_rendered(job.id, rendered, principal=principal, backend=backend)
        return {"job": job.as_public_dict(), "created": True}

    def _request_lifecycle_approval(
        self,
        rendered,
        parameters: Mapping[str, Any],
        *,
        principal: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        decision, job, created = MediaAdmissionService(self.jobs).admit(
            rendered.descriptor,
            parameters,
            principal=principal,
            # This branch reserves bounded queue capacity while the selected
            # worker is cold. It does not claim that the backend is usable.
            backend_ready=True,
            input_digest=rendered.parameters_digest,
            idempotency_key=idempotency_key,
            quality_profile=rendered.quality_profile,
        )
        if not decision.allowed:
            raise MediaError(
                "media_admission_rejected",
                "media request was not admitted",
                status=409 if decision.state == "rejected" else 503,
                details=decision.as_dict(),
            )
        if job is None:
            raise MediaError("media_admission_rejected", "media request was not admitted", status=503)
        return self._preview_lifecycle(
            job.id,
            rendered,
            principal=principal,
            created=created,
        )

    def _preview_lifecycle(
        self,
        job_id: str,
        rendered,
        *,
        principal: str,
        created: bool,
    ) -> dict[str, Any]:
        """Persist or recover the exact lifecycle preview for an accepted job."""

        with self._submit_lock:
            current = self.jobs.get(job_id, principal=principal)
            if current.state != JobState.ACCEPTED:
                return {"job": current.as_public_dict(), "created": False}
            try:
                if self._lifecycle_preview is None:
                    raise MediaError(
                        "media_lifecycle_unconfigured",
                        "the selected media worker is cold and no controller lifecycle is configured",
                        status=503,
                    )
                receipt = self._lifecycle_preview(
                    job_id,
                    principal,
                    rendered.descriptor.service_target,
                )
                approval = _approval_request(
                    job_id,
                    principal,
                    rendered.descriptor.service_target,
                    receipt,
                )
                current = self.jobs.get(job_id, principal=principal)
                if current.state == JobState.ACCEPTED:
                    current = self.jobs.transition(
                        job_id,
                        JobState.AWAITING_APPROVAL,
                        principal=principal,
                        reason="media_worker_start_requires_approval",
                        approval=approval,
                    )
                elif current.state != JobState.AWAITING_APPROVAL:
                    raise MediaError(
                        "media_worker_transition",
                        "controller lifecycle preview produced an unexpected job state",
                        status=409,
                    )
                return {"job": current.as_public_dict(), "created": created}
            except Exception as exc:
                current = self.jobs.get(job_id, principal=principal)
                if current.state in {
                    JobState.ACCEPTED,
                    JobState.AWAITING_APPROVAL,
                }:
                    self.jobs.transition(
                        job_id,
                        JobState.FAILED,
                        principal=principal,
                        reason=(
                            exc.code
                            if isinstance(exc, MediaError)
                            else "media_lifecycle_preview_failed"
                        ),
                    )
                if isinstance(exc, MediaError):
                    raise
                raise MediaError(
                    "media_lifecycle_preview_failed",
                    "the managed media worker preview failed",
                    status=503,
                ) from exc

    def _resume_existing(
        self,
        job_id: str,
        rendered,
        *,
        principal: str,
        backend: ComfyUIClient,
        qualification: bool,
    ) -> dict[str, Any]:
        current = self.jobs.get(job_id, principal=principal)
        if current.state == JobState.ACCEPTED:
            return self._resume_accepted(
                job_id,
                rendered,
                principal=principal,
                backend=backend,
                qualification=qualification,
            )
        if current.state == JobState.PREPARING:
            return self._resume_preparing(
                job_id,
                rendered,
                principal=principal,
                backend=backend,
                qualification=qualification,
            )
        if current.state == JobState.SUBMITTING:
            current = self._resume_submitting(
                job_id,
                principal=principal,
                backend=backend,
            )
        return {"job": current.as_public_dict(), "created": False}

    def _resume_accepted(
        self,
        job_id: str,
        rendered,
        *,
        principal: str,
        backend: ComfyUIClient,
        qualification: bool,
    ) -> dict[str, Any]:
        with self._submit_lock:
            current = self.jobs.get(job_id, principal=principal)
            if current.state != JobState.ACCEPTED:
                return {"job": current.as_public_dict(), "created": False}
            try:
                compatibility = (
                    backend.compatibility(rendered.descriptor, qualification=True)
                    if qualification
                    else backend.compatibility(rendered.descriptor)
                )
            except MediaError as exc:
                if exc.code != "backend_unavailable" or qualification:
                    raise
                return self._preview_lifecycle(
                    job_id,
                    rendered,
                    principal=principal,
                    created=False,
                )
            if not compatibility.ready and not qualification:
                return self._preview_lifecycle(
                    job_id,
                    rendered,
                    principal=principal,
                    created=False,
                )
            if not compatibility.ready or not compatibility.available:
                raise MediaError(
                    "media_service_unavailable",
                    "the media worker is not ready for the selected workflow",
                    status=503,
                    details={"compatibility": compatibility.as_public_dict()},
                )
            current = self._submit_rendered(
                job_id,
                rendered,
                principal=principal,
                backend=backend,
            )
            return {"job": current.as_public_dict(), "created": False}

    def _resume_preparing(
        self,
        job_id: str,
        rendered,
        *,
        principal: str,
        backend: ComfyUIClient,
        qualification: bool,
    ) -> dict[str, Any]:
        with self._submit_lock:
            current = self.jobs.get(job_id, principal=principal)
            if current.state != JobState.PREPARING:
                return {"job": current.as_public_dict(), "created": False}
            compatibility = (
                backend.compatibility(rendered.descriptor, qualification=True)
                if qualification
                else backend.compatibility(rendered.descriptor)
            )
            if not compatibility.ready or not compatibility.available:
                raise MediaError(
                    "media_service_unavailable",
                    "the approved media worker is not ready for the selected workflow",
                    status=503,
                    details={"compatibility": compatibility.as_public_dict()},
                )
            current = self._submit_rendered(
                job_id,
                rendered,
                principal=principal,
                backend=backend,
            )
            return {"job": current.as_public_dict(), "created": False}

    def _submit_rendered(
        self,
        job_id: str,
        rendered,
        *,
        principal: str,
        backend: ComfyUIClient,
    ):
        with self._submit_lock:
            current = self.jobs.get(job_id, principal=principal)
            if current.state == JobState.SUBMITTING:
                return self._recover_submitting_locked(
                    current,
                    principal=principal,
                    backend=backend,
                )
            if current.state == JobState.ACCEPTED:
                current = self.jobs.transition(
                    job_id,
                    JobState.PREPARING,
                    principal=principal,
                    reason="backend_submission_preparing",
                )
            if current.state != JobState.PREPARING:
                return current
            self.jobs.transition(
                job_id,
                JobState.SUBMITTING,
                principal=principal,
                reason="backend_submission_started",
            )
            # SUBMITTING is durable before the remote request. Any exception
            # after this point is outcome-ambiguous and must never cause an
            # automatic second submission.
            prompt_id = backend.submit(rendered, job_id=job_id)
            self.jobs.set_backend_prompt(job_id, prompt_id, principal=principal)
            return self.jobs.transition(
                job_id,
                JobState.QUEUED,
                principal=principal,
                reason="submitted_to_media_backend",
            )

    def _resume_submitting(
        self,
        job_id: str,
        *,
        principal: str,
        backend: ComfyUIClient,
    ):
        with self._submit_lock:
            current = self.jobs.get(job_id, principal=principal)
            if current.state != JobState.SUBMITTING:
                return current
            return self._recover_submitting_locked(
                current,
                principal=principal,
                backend=backend,
            )

    def _recover_submitting_locked(
        self,
        current,
        *,
        principal: str,
        backend: ComfyUIClient,
    ):
        if current.backend_prompt_id:
            return self.jobs.transition(
                current.id,
                JobState.QUEUED,
                principal=principal,
                reason="backend_submission_recovered",
            )
        finder = getattr(backend, "find_prompt", None)
        if not callable(finder):
            raise MediaError(
                "backend_recovery_unsupported",
                "the media backend cannot recover an ambiguous submission",
                status=503,
            )
        age = (utc_now() - current.updated_at).total_seconds()
        try:
            prompt_id = finder(current.id)
        except MediaError:
            if age < SUBMISSION_RECOVERY_GRACE_SECONDS:
                raise
            return self.jobs.transition(
                current.id,
                JobState.FAILED,
                principal=principal,
                reason="backend_submission_recovery_failed",
            )
        if prompt_id is not None:
            self.jobs.set_backend_prompt(current.id, prompt_id, principal=principal)
            return self.jobs.transition(
                current.id,
                JobState.QUEUED,
                principal=principal,
                reason="backend_submission_recovered",
            )
        if age < SUBMISSION_RECOVERY_GRACE_SECONDS:
            return current
        return self.jobs.transition(
            current.id,
            JobState.FAILED,
            principal=principal,
            reason="backend_submission_outcome_unknown",
        )

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


def stable_request_key(
    workflow_id: str,
    version: str,
    parameters: Mapping[str, Any],
    *,
    quality_profile: str = "",
) -> str:
    request = {"workflow": workflow_id, "version": version, "parameters": parameters}
    if quality_profile:
        request["qualityProfile"] = quality_profile
    payload = json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "cli-" + hashlib.sha256(payload).hexdigest()


def _approval_request(
    job_id: str,
    principal: str,
    service: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    transaction_id = receipt.get("transactionId")
    manifest = receipt.get("manifest")
    if (
        not isinstance(transaction_id, str)
        or not transaction_id
        or not isinstance(manifest, str)
        or receipt.get("service") != service
        or receipt.get("action") != "prepare"
        or receipt.get("humanRequired") is not True
    ):
        raise MediaError(
            "media_lifecycle_preview_invalid",
            "controller returned an invalid media worker preview",
            status=502,
        )
    return {
        "schema": "anvil-serving.media-lifecycle-approval/v1",
        "transactionId": transaction_id,
        "service": service,
        "action": "prepare",
        "humanRequired": True,
        "approved": False,
        "operatorAction": {
            "tool": "media_worker_prepare",
            "arguments": {
                "job_id": job_id,
                "principal": principal,
                "service": service,
                "manifest": manifest,
                "transaction_id": transaction_id,
                "dry_run": False,
                "confirm": True,
                "human_approved": True,
            },
        },
    }


__all__ = ["MediaOperations", "parameters_from_json", "stable_request_key"]
