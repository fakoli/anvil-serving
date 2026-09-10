"""Bounded background task evidence operations with durable, private outcomes."""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..observability.dashboard.contracts import ObservatoryError


class EvidenceJobs:
    def __init__(self, projects, store, lock):
        self.projects, self.store, self.lock = projects, store, lock
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="workbench-evidence")
        self.active = {}
        self.closed = False
        self.store.recover("artifact-job", {"running"})

    def busy(self, binding_id):
        with self.lock:
            return binding_id in self.active

    def ensure_idle(self, binding_id):
        if self.busy(binding_id):
            raise ObservatoryError("task_evidence_busy", "Wait for this task's evidence operation to finish.", 409)

    def latest(self, session, binding_id):
        self.projects.binding(session, binding_id)
        return next((row for row in self.store.list("artifact-job", session.principal.identity)
                     if row["binding_id"] == binding_id), None)

    def start(self, session, binding_id, action, artifact_digest=None):
        with self.lock:
            self.projects.binding(session, binding_id, execute=True)
            self.ensure_idle(binding_id)
            if self.closed or len(self.active) >= 2:
                raise ObservatoryError("evidence_capacity", "Both evidence workers are busy. Try again when one finishes.", 409)
            previous = self.latest(session, binding_id)
            if action == "submit" and previous and previous["action"] == action and previous["status"] == "interrupted":
                raise ObservatoryError("submission_uncertain", "The service stopped during submission. Reconcile the task's Anvil evidence before another submission.", 409)
            row = {"id": str(uuid.uuid4()), "binding_id": binding_id, "action": action,
                   "artifact_digest": artifact_digest, "status": "running", "started_at": time.time()}
            self.store.put("artifact-job", session.principal.identity, row["id"], row)
            self.active[binding_id] = row["id"]
            try:
                self.pool.submit(self._run, session, row)
            except Exception:
                self.active.pop(binding_id, None)
                row.update(status="failed", error="The evidence worker could not start.")
                self.store.put("artifact-job", session.principal.identity, row["id"], row)
                raise
            return {"accepted": True, "job": row}

    def _run(self, session, row):
        try:
            methods = {"review": self.projects.review_evidence, "verify": self.projects.verify_evidence,
                       "submit": self.projects.submit_evidence, "release": self.projects.release}
            args = (session, row["binding_id"])
            if row["action"] in {"verify", "submit"}:
                args += (row["artifact_digest"],)
            methods[row["action"]](*args)
            row.update(status="complete", finished_at=time.time())
        except ObservatoryError as error:
            row.update(status="failed", finished_at=time.time(), code=error.code, error=error.message)
        except Exception:
            # Upstream output can contain private paths, prompts or credentials.
            row.update(status="failed", finished_at=time.time(), code="evidence_failed",
                       error="The evidence operation did not complete. Inspect the retained task state before retrying.")
        finally:
            with self.lock:
                try:
                    self.store.put("artifact-job", session.principal.identity, row["id"], row)
                finally:
                    self.active.pop(row["binding_id"], None)

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=True)
