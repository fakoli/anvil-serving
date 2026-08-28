"""Ownership-safe cancellation for durable media jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .contracts import JobState, MediaJob, TERMINAL_STATES
from .errors import MediaError
from .jobs import MediaJobStore


@dataclass(frozen=True)
class CancellationResult:
    job: MediaJob
    canceled: bool
    backend_interrupted: bool = False
    reason: str = ""

    def as_public_dict(self) -> dict:
        return {
            "job": self.job.as_public_dict(),
            "canceled": self.canceled,
            "backendInterrupted": self.backend_interrupted,
            "reason": self.reason,
        }


class MediaCancellationService:
    def __init__(
        self,
        store: MediaJobStore,
        *,
        delete_queued: Callable[[str], None],
        interrupt_exclusive: Callable[[], None],
        owns_active_slot: Callable[[MediaJob], bool],
    ) -> None:
        self.store = store
        self.delete_queued = delete_queued
        self.interrupt_exclusive = interrupt_exclusive
        self.owns_active_slot = owns_active_slot

    def cancel(self, job_id: str, *, principal: str) -> CancellationResult:
        job = self.store.get(job_id, principal=principal)
        if job.state in TERMINAL_STATES:
            return CancellationResult(job, False, reason="already_terminal")
        if job.state == JobState.RUNNING:
            if not job.backend_prompt_id or not self.owns_active_slot(job):
                return CancellationResult(job, False, reason="running_not_exclusively_owned")
            self.interrupt_exclusive()
            changed = self.store.transition(
                job.id,
                JobState.CANCELED,
                principal=principal,
                reason="exclusive_running_prompt_interrupted",
            )
            return CancellationResult(changed, True, True, "canceled")
        if job.state == JobState.QUEUED and job.backend_prompt_id:
            self.delete_queued(job.backend_prompt_id)
        try:
            changed = self.store.transition(
                job.id,
                JobState.CANCELED,
                principal=principal,
                reason="canceled_by_principal",
            )
        except MediaError as exc:
            if exc.code == "invalid_transition":
                current = self.store.get(job.id, principal=principal)
                return CancellationResult(current, False, reason="state_changed")
            raise
        return CancellationResult(changed, True, False, "canceled")


__all__ = ["CancellationResult", "MediaCancellationService"]
