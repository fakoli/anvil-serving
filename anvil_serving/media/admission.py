"""Bounded media queue and lifecycle admission."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import WorkflowDescriptor
from .errors import MediaError
from .jobs import MediaJobStore


@dataclass(frozen=True)
class MediaAdmissionDecision:
    allowed: bool
    state: str
    reason: str
    preview: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "allowed": self.allowed,
            "state": self.state,
            "reason": self.reason,
        }
        if self.preview is not None:
            result["preview"] = dict(self.preview)
        return result


class MediaAdmissionService:
    def __init__(self, store: MediaJobStore) -> None:
        self.store = store

    def evaluate(
        self,
        workflow: WorkflowDescriptor,
        parameters: Mapping[str, Any],
        *,
        principal: str,
        backend_ready: bool,
        lifecycle_preview: Mapping[str, Any] | None = None,
        lifecycle_approved: bool = False,
    ) -> MediaAdmissionDecision:
        validated = workflow.validate_parameters(parameters)
        try:
            encoded = json.dumps(validated, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise MediaError("invalid_parameters", "workflow parameters are not JSON serializable") from exc
        if len(encoded) > workflow.max_request_bytes:
            return MediaAdmissionDecision(False, "rejected", "request_bytes")
        counts = self.store.active_counts(workflow.id, principal=principal)
        if counts["principal"] >= 2:
            return MediaAdmissionDecision(False, "rejected", "principal_queue_depth")
        if counts["total"] >= workflow.max_queue_depth:
            return MediaAdmissionDecision(False, "rejected", "workflow_queue_depth")
        if counts["running"] >= workflow.max_concurrency:
            return MediaAdmissionDecision(False, "rejected", "workflow_concurrency")
        if not workflow.available:
            return MediaAdmissionDecision(False, "unavailable", "workflow_unqualified")
        if not backend_ready:
            if lifecycle_preview is None:
                return MediaAdmissionDecision(False, "failed", "media_service_unavailable")
            if not lifecycle_approved:
                return MediaAdmissionDecision(False, "awaiting_approval", "lifecycle_approval_required", dict(lifecycle_preview))
            return MediaAdmissionDecision(True, "preparing", "lifecycle_approved", dict(lifecycle_preview))
        return MediaAdmissionDecision(True, "accepted", "admitted")


__all__ = ["MediaAdmissionDecision", "MediaAdmissionService"]
