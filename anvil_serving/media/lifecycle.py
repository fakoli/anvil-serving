"""Owned, confirmation-gated lifecycle transactions for media workers."""

from __future__ import annotations

import datetime as dt
import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contracts import JobState, TERMINAL_STATES, utc_now
from .errors import MediaError
from .jobs import MediaJobStore


ManageOperation = Callable[[Mapping[str, Any]], Mapping[str, Any]]
StatusOperation = Callable[[Mapping[str, Any]], Mapping[str, Any]]
PHASE_LEASE_SECONDS = 900


@dataclass(frozen=True)
class MediaLifecycleReceipt:
    transaction_id: str
    service: str
    action: str
    applied: bool
    owns_instance: bool
    preexisting: bool
    human_required: bool
    controller_receipt: Mapping[str, Any]
    manifest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "transactionId": self.transaction_id,
            "service": self.service,
            "action": self.action,
            "applied": self.applied,
            "ownsInstance": self.owns_instance,
            "preexisting": self.preexisting,
            "humanRequired": self.human_required,
            "controllerReceipt": dict(self.controller_receipt),
            "manifest": self.manifest,
        }


class MediaWorkerLifecycle:
    """Coordinate managed serves without assuming ownership of existing work."""

    def __init__(
        self,
        store: MediaJobStore,
        *,
        status_operation: StatusOperation,
        manage_operation: ManageOperation,
    ) -> None:
        self.store = store
        self._status_operation = status_operation
        self._manage_operation = manage_operation
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.store.path), timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS media_lifecycle_transactions (
                    id TEXT PRIMARY KEY,
                    service TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owns_instance INTEGER NOT NULL CHECK(owns_instance IN (0,1)),
                    preexisting INTEGER NOT NULL CHECK(preexisting IN (0,1)),
                    receipt_json TEXT NOT NULL,
                    manifest TEXT NOT NULL DEFAULT '',
                    lease_expires_at TEXT NOT NULL DEFAULT '',
                    generation INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS media_lifecycle_jobs (
                    transaction_id TEXT NOT NULL
                        REFERENCES media_lifecycle_transactions(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
                    PRIMARY KEY(transaction_id, job_id)
                );
                DROP INDEX IF EXISTS media_lifecycle_active_service;
                CREATE UNIQUE INDEX IF NOT EXISTS media_lifecycle_active_service
                    ON media_lifecycle_transactions(service)
                    WHERE status IN ('preparing','active','releasing');
                """
            )
            columns = {
                row["name"]
                for row in db.execute(
                    "PRAGMA table_info(media_lifecycle_transactions)"
                ).fetchall()
            }
            if "lease_expires_at" not in columns:
                db.execute(
                    "ALTER TABLE media_lifecycle_transactions ADD COLUMN lease_expires_at TEXT NOT NULL DEFAULT ''"
                )
            if "generation" not in columns:
                db.execute(
                    "ALTER TABLE media_lifecycle_transactions ADD COLUMN generation INTEGER NOT NULL DEFAULT 1"
                )
            if "manifest" not in columns:
                db.execute(
                    "ALTER TABLE media_lifecycle_transactions ADD COLUMN manifest TEXT NOT NULL DEFAULT ''"
                )

    def prepare(
        self,
        job_id: str,
        *,
        principal: str,
        service: str,
        manifest: str = "",
        transaction_id: str = "",
        confirm: bool = False,
        human_approved: bool = False,
    ) -> MediaLifecycleReceipt:
        # The in-process lock keeps one lifecycle object serialized; the
        # SQLite preparing/active/releasing reservation below provides the
        # same guarantee across controller requests and object instances.
        with self._lock:
            job = self.store.get(job_id, principal=principal)
            if job.state in TERMINAL_STATES:
                raise MediaError("job_terminal", "terminal media jobs cannot prepare a worker", status=409)
            approved = bool(confirm and human_approved)
            recovering_approval = False
            if approved:
                recovering_approval = _require_persisted_approval(
                    job,
                    transaction_id=transaction_id,
                    principal=principal,
                    service=service,
                    manifest=manifest,
                )
                if not recovering_approval:
                    approved_transaction = self._transaction_for_approval(
                        transaction_id,
                        job_id=job_id,
                    )
                    recovering_approval = (
                        approved_transaction["status"] != "awaiting_approval"
                    )
            existing = self._reserved_for_service(service)
            if existing is not None:
                _require_manifest_match(existing, manifest)
            status = self._status_operation({"manifest": manifest, "names": [service]})
            status_row = _selected_status(status, service)
            running = status_row.get("running") is True and status_row.get("health_status") is not None
            if recovering_approval:
                return self._recover_consumed_prepare(
                    job_id,
                    principal=principal,
                    service=service,
                    manifest=manifest,
                    transaction_id=transaction_id,
                    existing=existing,
                    running=running,
                )
            if not approved and job.state == JobState.PREPARING:
                approval = job.approval
                approved_transaction_id = (
                    approval.get("transactionId")
                    if isinstance(approval, Mapping)
                    else ""
                )
                _require_persisted_approval(
                    job,
                    transaction_id=approved_transaction_id,
                    principal=principal,
                    service=service,
                    manifest=manifest,
                )
                prior = self._transaction_for_approval(
                    approved_transaction_id,
                    job_id=job_id,
                )
                if prior["status"] in {"preparing", "failed"} and running:
                    return self._recover_consumed_prepare(
                        job_id,
                        principal=principal,
                        service=service,
                        manifest=manifest,
                        transaction_id=approved_transaction_id,
                        existing=existing,
                        running=True,
                    )
                if prior["status"] == "preparing":
                    if not _lease_expired(prior):
                        return _receipt_from_row(prior, action="prepare")
                    self._set_transaction_status(
                        prior["id"],
                        expected="preparing",
                        status="failed",
                        controller={
                            "applied": False,
                            "recovered": True,
                            "observedRunning": False,
                            "previousPhase": "preparing",
                        },
                    )
                    if existing is not None and existing["id"] == prior["id"]:
                        existing = None
            if existing is not None:
                existing = self._recover_phase(
                    existing,
                    running=running,
                    for_teardown=False,
                )
                if existing["status"] in {"failed", "released"}:
                    existing = None
            if existing is not None and existing["status"] == "active" and not running:
                existing = self._set_transaction_status(
                    existing["id"],
                    expected="active",
                    status="failed",
                    controller={"applied": False, "recovered": True, "observedRunning": False},
                )
                existing = None

            if existing is not None and existing["status"] == "releasing":
                raise MediaError(
                    "media_worker_transition",
                    "media worker teardown is already in progress",
                    status=409,
                )
            if running:
                if existing is not None:
                    if approved and existing["id"] != transaction_id:
                        self._consume_preview(
                            transaction_id,
                            job_id=job_id,
                            service=service,
                            manifest=manifest,
                            linked_transaction_id=existing["id"],
                        )
                    self._link(existing["id"], job_id)
                    receipt = _receipt_from_row(existing, action="prepare")
                elif approved:
                    observed = self._observe_preview(
                        transaction_id,
                        job_id=job_id,
                        service=service,
                        manifest=manifest,
                        controller=status,
                    )
                    receipt = _receipt_from_row(observed, action="prepare")
                else:
                    receipt = self._record(
                        job_id,
                        service,
                        manifest=manifest,
                        owns=False,
                        preexisting=True,
                        controller=status,
                    )
                if approved and job.state in {
                    JobState.ACCEPTED,
                    JobState.AWAITING_APPROVAL,
                }:
                    self.store.transition(
                        job_id,
                        JobState.PREPARING,
                        principal=principal,
                        reason="media_worker_start_observed",
                        approval=_approval_request(job_id, principal, receipt),
                    )
                return receipt

            if not approved:
                controller = self._manage_operation(
                    {
                        "action": "up",
                        "manifest": manifest,
                        "names": [service],
                        "dry_run": True,
                        "confirm": False,
                    }
                )
                receipt = self._record_preview(
                    job_id,
                    service,
                    manifest=manifest,
                    controller=controller,
                )
                pending_transaction = (
                    job.approval.get("transactionId")
                    if isinstance(job.approval, Mapping)
                    else None
                )
                if job.state in {JobState.ACCEPTED, JobState.PREPARING} or (
                    job.state == JobState.AWAITING_APPROVAL
                    and pending_transaction != receipt.transaction_id
                ):
                    self.store.transition(
                        job_id,
                        JobState.AWAITING_APPROVAL,
                        principal=principal,
                        reason=(
                            "media_worker_start_retry_requires_approval"
                            if job.state
                            in {JobState.PREPARING, JobState.AWAITING_APPROVAL}
                            else "media_worker_start_requires_approval"
                        ),
                        approval=_approval_request(job_id, principal, receipt),
                    )
                return receipt

            claimed, owner = self._claim_prepare(
                job_id,
                service,
                manifest,
                transaction_id=transaction_id,
            )
            if not owner:
                if claimed["status"] == "releasing":
                    raise MediaError(
                        "media_worker_transition",
                        "media worker teardown is already in progress",
                        status=409,
                    )
                receipt = _receipt_from_row(claimed, action="prepare")
                if job.state in {JobState.ACCEPTED, JobState.AWAITING_APPROVAL}:
                    self.store.transition(
                        job_id,
                        JobState.PREPARING,
                        principal=principal,
                        reason="media_worker_start_in_progress",
                        approval=_approval_request(job_id, principal, receipt),
                    )
                return receipt

            preparing_receipt = _receipt_from_row(claimed, action="prepare")
            if job.state == JobState.AWAITING_APPROVAL:
                self.store.transition(
                    job_id,
                    JobState.PREPARING,
                    principal=principal,
                    reason="media_worker_start_approved",
                    approval=_approval_request(
                        job_id,
                        principal,
                        preparing_receipt,
                    ),
                )
            try:
                controller = self._manage_operation(
                    {
                        "action": "up",
                        "manifest": manifest,
                        "names": [service],
                        "dry_run": False,
                        "confirm": True,
                    }
                )
            except Exception:
                try:
                    self._set_transaction_status(
                        claimed["id"],
                        expected="preparing",
                        status="failed",
                        controller={"applied": False, "error": "manage_failed"},
                    )
                except MediaError as transition_error:
                    current = self._transaction_for_approval(
                        claimed["id"],
                        job_id=job_id,
                    )
                    if current["status"] != "active":
                        raise transition_error
                raise
            try:
                claimed = self._set_transaction_status(
                    claimed["id"],
                    expected="preparing",
                    status="active",
                    controller=controller,
                )
            except MediaError as transition_error:
                claimed = self._transaction_for_approval(
                    claimed["id"],
                    job_id=job_id,
                )
                if claimed["status"] != "active":
                    raise transition_error
            receipt = _receipt_from_row(claimed, action="prepare")
            return receipt

    def teardown(
        self,
        job_id: str,
        *,
        principal: str,
        manifest: str = "",
        confirm: bool = False,
        human_approved: bool = False,
    ) -> MediaLifecycleReceipt:
        with self._lock:
            self.store.get(job_id, principal=principal)
            row = self._transaction_for_job(job_id)
            if row is None:
                return self._ephemeral(
                    "", "teardown", {"reason": "not_owned"}, manifest=manifest
                )
            service = row["service"]
            _require_manifest_match(row, manifest)
            bound_manifest = row["manifest"]
            status = self._status_operation(
                {"manifest": bound_manifest, "names": [service]}
            )
            status_row = _selected_status(status, service)
            running = status_row.get("running") is True and status_row.get("health_status") is not None
            row = self._recover_phase(row, running=running, for_teardown=True)
            if row["status"] == "active" and not running:
                row = self._set_transaction_status(
                    row["id"],
                    expected="active",
                    status="released",
                    controller={"applied": True, "recovered": True, "observedRunning": False},
                )
            if row["status"] == "released":
                return _receipt_from_row(row, action="teardown", applied=True)
            if not bool(row["owns_instance"]) or bool(row["preexisting"]):
                return _receipt_from_row(row, action="teardown", applied=False)
            if row["status"] == "releasing":
                return self._ephemeral(
                    service,
                    "teardown",
                    {"reason": "release_in_progress"},
                    transaction_id=row["id"],
                    owns=True,
                    manifest=bound_manifest,
                )
            if not self._all_jobs_terminal(row["id"]):
                return self._ephemeral(
                    service,
                    "teardown",
                    {"reason": "owned_jobs_nonterminal"},
                    transaction_id=row["id"],
                    manifest=bound_manifest,
                )
            approved = bool(confirm and human_approved)
            if not approved:
                controller = self._manage_operation(
                    {
                        "action": "down",
                        "manifest": bound_manifest,
                        "names": [service],
                        "dry_run": True,
                        "confirm": False,
                    }
                )
                return self._ephemeral(
                    service,
                    "teardown",
                    controller,
                    human_required=True,
                    transaction_id=row["id"],
                    owns=True,
                    manifest=bound_manifest,
                )
            claimed = self._claim_release(row["id"])
            if not claimed:
                return self._ephemeral(
                    service,
                    "teardown",
                    {"reason": "release_in_progress"},
                    transaction_id=row["id"],
                    owns=True,
                    manifest=bound_manifest,
                )
            try:
                controller = self._manage_operation(
                    {
                        "action": "down",
                        "manifest": bound_manifest,
                        "names": [service],
                        "dry_run": False,
                        "confirm": True,
                    }
                )
            except Exception:
                self._set_transaction_status(
                    row["id"],
                    expected="releasing",
                    status="active",
                    controller={"applied": False, "error": "manage_failed"},
                )
                raise
            self._set_transaction_status(
                row["id"],
                expected="releasing",
                status="released",
                controller=controller,
            )
            return MediaLifecycleReceipt(
                row["id"],
                service,
                "teardown",
                True,
                True,
                False,
                False,
                controller,
                bound_manifest,
            )

    def status(self, job_id: str, *, principal: str) -> dict[str, Any]:
        self.store.get(job_id, principal=principal)
        row = self._transaction_for_job(job_id)
        if row is None:
            return {"owned": False, "transaction": None}
        receipt = _receipt_from_row(row, action="status", applied=False)
        return {"owned": bool(row["owns_instance"]), "transaction": receipt.as_dict()}

    def _record(
        self,
        job_id: str,
        service: str,
        *,
        manifest: str,
        owns: bool,
        preexisting: bool,
        controller: Mapping[str, Any],
    ) -> MediaLifecycleReceipt:
        transaction_id = secrets.token_urlsafe(18)
        now = utc_now().isoformat()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if owns:
                existing = db.execute(
                    "SELECT * FROM media_lifecycle_transactions WHERE service=? AND status='active'",
                    (service,),
                ).fetchone()
                if existing is not None:
                    _require_manifest_match(existing, manifest)
                    db.execute(
                        "INSERT OR IGNORE INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                        (existing["id"], job_id),
                    )
                    db.execute("COMMIT")
                    return _receipt_from_row(existing, action="prepare")
            db.execute(
                "INSERT INTO media_lifecycle_transactions(id,service,status,owns_instance,preexisting,receipt_json,manifest,lease_expires_at,generation,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (transaction_id, service, "active" if owns else "observed", int(owns), int(preexisting), _json(controller), manifest, "", 1, now, now),
            )
            db.execute(
                "INSERT INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                (transaction_id, job_id),
            )
            db.execute("COMMIT")
        return MediaLifecycleReceipt(
            transaction_id,
            service,
            "prepare",
            _controller_applied(controller, default=owns),
            owns,
            preexisting, False, controller, manifest
        )

    def _record_preview(
        self,
        job_id: str,
        service: str,
        *,
        manifest: str,
        controller: Mapping[str, Any],
    ) -> MediaLifecycleReceipt:
        transaction_id = secrets.token_urlsafe(18)
        now = utc_now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT t.* FROM media_lifecycle_transactions t "
                "JOIN media_lifecycle_jobs j ON j.transaction_id=t.id "
                "WHERE j.job_id=? AND t.status='awaiting_approval' "
                "ORDER BY t.created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if existing is not None:
                _require_manifest_match(existing, manifest)
                if existing["service"] != service:
                    db.execute("ROLLBACK")
                    raise MediaError(
                        "media_lifecycle_approval_mismatch",
                        "pending media worker approval targets another service",
                        status=409,
                    )
                db.execute("COMMIT")
                receipt = _receipt_from_row(existing, action="prepare")
                return MediaLifecycleReceipt(
                    receipt.transaction_id,
                    receipt.service,
                    receipt.action,
                    False,
                    False,
                    False,
                    True,
                    receipt.controller_receipt,
                    receipt.manifest,
                )
            db.execute(
                "INSERT INTO media_lifecycle_transactions"
                "(id,service,status,owns_instance,preexisting,receipt_json,manifest,"
                "lease_expires_at,generation,created_at,updated_at) "
                "VALUES (?,?, 'awaiting_approval',0,0,?,?, '',1,?,?)",
                (transaction_id, service, _json(controller), manifest, now, now),
            )
            db.execute(
                "INSERT INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                (transaction_id, job_id),
            )
            db.execute("COMMIT")
        return MediaLifecycleReceipt(
            transaction_id,
            service,
            "prepare",
            False,
            False,
            False,
            True,
            controller,
            manifest,
        )

    def _ephemeral(
        self,
        service: str,
        action: str,
        controller: Mapping[str, Any],
        *,
        human_required: bool = False,
        transaction_id: str = "",
        owns: bool = False,
        manifest: str = "",
    ) -> MediaLifecycleReceipt:
        return MediaLifecycleReceipt(
            transaction_id or secrets.token_urlsafe(18), service, action, False, owns, False,
            human_required, controller, manifest
        )

    def _reserved_for_service(self, service: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM media_lifecycle_transactions WHERE service=? AND status IN ('preparing','active','releasing')",
                (service,),
            ).fetchone()

    def _claim_prepare(
        self,
        job_id: str,
        service: str,
        manifest: str = "",
        *,
        transaction_id: str = "",
    ) -> tuple[sqlite3.Row, bool]:
        new_transaction_id = transaction_id or secrets.token_urlsafe(18)
        now = utc_now().isoformat()
        lease_expires_at = (
            utc_now() + dt.timedelta(seconds=PHASE_LEASE_SECONDS)
        ).isoformat()
        pending = {"applied": False, "phase": "preparing"}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            preview = None
            if transaction_id:
                preview = db.execute(
                    "SELECT t.* FROM media_lifecycle_transactions t "
                    "JOIN media_lifecycle_jobs j ON j.transaction_id=t.id "
                    "WHERE t.id=? AND j.job_id=? "
                    "AND t.status='awaiting_approval'",
                    (transaction_id, job_id),
                ).fetchone()
                if preview is None:
                    db.execute("ROLLBACK")
                    raise MediaError(
                        "media_lifecycle_approval_consumed",
                        "media worker approval is missing, consumed, or replayed",
                        status=409,
                    )
                _require_manifest_match(preview, manifest)
                if preview["service"] != service:
                    db.execute("ROLLBACK")
                    raise MediaError(
                        "media_lifecycle_approval_mismatch",
                        "media worker approval targets another service",
                        status=409,
                    )
            existing = db.execute(
                "SELECT * FROM media_lifecycle_transactions WHERE service=? AND status IN ('preparing','active','releasing')",
                (service,),
            ).fetchone()
            if existing is not None:
                _require_manifest_match(existing, manifest)
                if preview is not None:
                    db.execute(
                        "UPDATE media_lifecycle_transactions SET status='consumed',"
                        "updated_at=? WHERE id=? AND status='awaiting_approval'",
                        (now, transaction_id),
                    )
                db.execute(
                    "INSERT OR IGNORE INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                    (existing["id"], job_id),
                )
                db.execute("COMMIT")
                return existing, False
            if preview is not None:
                changed = db.execute(
                    "UPDATE media_lifecycle_transactions SET status='preparing',"
                    "owns_instance=1,preexisting=0,receipt_json=?,lease_expires_at=?,"
                    "generation=generation+1,updated_at=? "
                    "WHERE id=? AND status=?",
                    (
                        _json(pending),
                        lease_expires_at,
                        now,
                        transaction_id,
                        "awaiting_approval",
                    ),
                ).rowcount
                if changed != 1:
                    db.execute("ROLLBACK")
                    raise MediaError(
                        "media_lifecycle_approval_consumed",
                        "media worker approval was consumed concurrently",
                        status=409,
                    )
            else:
                db.execute(
                    "INSERT INTO media_lifecycle_transactions(id,service,status,owns_instance,preexisting,receipt_json,manifest,lease_expires_at,generation,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (new_transaction_id, service, "preparing", 1, 0, _json(pending), manifest, lease_expires_at, 1, now, now),
                )
                db.execute(
                    "INSERT INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                    (new_transaction_id, job_id),
                )
            row = db.execute(
                "SELECT * FROM media_lifecycle_transactions WHERE id=?",
                (new_transaction_id,),
            ).fetchone()
            db.execute("COMMIT")
        return row, True

    def _recover_consumed_prepare(
        self,
        job_id: str,
        *,
        principal: str,
        service: str,
        manifest: str,
        transaction_id: str,
        existing: sqlite3.Row | None,
        running: bool,
    ) -> MediaLifecycleReceipt:
        """Observe an approved apply without ever replaying its mutation."""

        consumed = self._transaction_for_approval(transaction_id, job_id=job_id)
        _require_manifest_match(consumed, manifest)
        if consumed["service"] != service:
            raise MediaError(
                "media_lifecycle_approval_mismatch",
                "media worker approval targets another service",
                status=409,
            )
        consumed_status = consumed["status"]
        if consumed_status not in {"preparing", "failed"}:
            raise MediaError(
                "media_lifecycle_approval_consumed",
                "media worker approval is missing, consumed, or replayed",
                status=409,
            )
        if existing is not None and existing["id"] != transaction_id:
            existing = self._recover_phase(
                existing,
                running=running,
                for_teardown=False,
            )
            if existing["status"] == "releasing":
                raise MediaError(
                    "media_worker_transition",
                    "media worker teardown is already in progress",
                    status=409,
                )
            if existing["status"] == "active" and not running:
                self._set_transaction_status(
                    existing["id"],
                    expected="active",
                    status="failed",
                    controller={
                        "applied": False,
                        "recovered": True,
                        "observedRunning": False,
                    },
                )
                raise MediaError(
                    "media_lifecycle_approval_consumed",
                    "media worker approval was consumed; a fresh preview is required",
                    status=409,
                )
            if existing["status"] in {"preparing", "active"}:
                self._link(existing["id"], job_id)
                return _receipt_from_row(existing, action="prepare")
        if not running:
            if consumed_status == "preparing" and not _lease_expired(consumed):
                return _receipt_from_row(consumed, action="prepare")
            if consumed_status == "preparing":
                self._set_transaction_status(
                    transaction_id,
                    expected="preparing",
                    status="failed",
                    controller={
                        "applied": False,
                        "recovered": True,
                        "observedRunning": False,
                        "previousPhase": "preparing",
                    },
                )
            raise MediaError(
                "media_lifecycle_approval_consumed",
                "media worker approval was consumed; a fresh preview is required",
                status=409,
            )
        recovered = self._set_transaction_status(
            transaction_id,
            expected=consumed_status,
            status="active",
            controller={
                "applied": True,
                "recovered": True,
                "observedRunning": True,
                "previousPhase": consumed_status,
            },
        )
        receipt = _receipt_from_row(recovered, action="prepare")
        current = self.store.get(job_id, principal=principal)
        if current.state == JobState.AWAITING_APPROVAL:
            self.store.transition(
                job_id,
                JobState.PREPARING,
                principal=principal,
                reason="media_worker_start_recovered",
                approval=_approval_request(job_id, principal, receipt),
            )
        return receipt

    def _transaction_for_approval(
        self,
        transaction_id: str,
        *,
        job_id: str,
    ) -> sqlite3.Row:
        with self._connect() as db:
            row = db.execute(
                "SELECT t.* FROM media_lifecycle_transactions t "
                "JOIN media_lifecycle_jobs j ON j.transaction_id=t.id "
                "WHERE t.id=? AND j.job_id=?",
                (transaction_id, job_id),
            ).fetchone()
        if row is None:
            raise MediaError(
                "media_lifecycle_approval_consumed",
                "media worker approval is missing, consumed, or replayed",
                status=409,
            )
        return row

    def _consume_preview(
        self,
        transaction_id: str,
        *,
        job_id: str,
        service: str,
        manifest: str,
        linked_transaction_id: str,
    ) -> None:
        now = utc_now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            preview = db.execute(
                "SELECT t.* FROM media_lifecycle_transactions t "
                "JOIN media_lifecycle_jobs j ON j.transaction_id=t.id "
                "WHERE t.id=? AND j.job_id=? AND t.status='awaiting_approval'",
                (transaction_id, job_id),
            ).fetchone()
            if preview is None:
                db.execute("ROLLBACK")
                raise MediaError(
                    "media_lifecycle_approval_consumed",
                    "media worker approval is missing, consumed, or replayed",
                    status=409,
                )
            _require_manifest_match(preview, manifest)
            if preview["service"] != service:
                db.execute("ROLLBACK")
                raise MediaError(
                    "media_lifecycle_approval_mismatch",
                    "media worker approval targets another service",
                    status=409,
                )
            db.execute(
                "UPDATE media_lifecycle_transactions SET status='consumed',updated_at=? "
                "WHERE id=? AND status='awaiting_approval'",
                (now, transaction_id),
            )
            db.execute(
                "INSERT OR IGNORE INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                (linked_transaction_id, job_id),
            )
            db.execute("COMMIT")

    def _observe_preview(
        self,
        transaction_id: str,
        *,
        job_id: str,
        service: str,
        manifest: str,
        controller: Mapping[str, Any],
    ) -> sqlite3.Row:
        now = utc_now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT t.* FROM media_lifecycle_transactions t "
                "JOIN media_lifecycle_jobs j ON j.transaction_id=t.id "
                "WHERE t.id=? AND j.job_id=? AND t.status='awaiting_approval'",
                (transaction_id, job_id),
            ).fetchone()
            if row is None:
                db.execute("ROLLBACK")
                raise MediaError(
                    "media_lifecycle_approval_consumed",
                    "media worker approval is missing, consumed, or replayed",
                    status=409,
                )
            _require_manifest_match(row, manifest)
            if row["service"] != service:
                db.execute("ROLLBACK")
                raise MediaError(
                    "media_lifecycle_approval_mismatch",
                    "media worker approval targets another service",
                    status=409,
                )
            changed = db.execute(
                "UPDATE media_lifecycle_transactions SET status='observed',"
                "owns_instance=0,preexisting=1,receipt_json=?,updated_at=? "
                "WHERE id=? AND status='awaiting_approval'",
                (_json(controller), now, transaction_id),
            ).rowcount
            observed = db.execute(
                "SELECT * FROM media_lifecycle_transactions WHERE id=?",
                (transaction_id,),
            ).fetchone()
            db.execute("COMMIT")
        if changed != 1 or observed is None:
            raise MediaError(
                "media_lifecycle_approval_consumed",
                "media worker approval was consumed concurrently",
                status=409,
            )
        return observed

    def _claim_release(self, transaction_id: str) -> bool:
        now = utc_now().isoformat()
        lease_expires_at = (
            utc_now() + dt.timedelta(seconds=PHASE_LEASE_SECONDS)
        ).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE media_lifecycle_transactions SET status='releasing',lease_expires_at=?,generation=generation+1,updated_at=? WHERE id=? AND status='active'",
                (lease_expires_at, now, transaction_id),
            ).rowcount
            db.execute("COMMIT")
        return changed == 1

    def _set_transaction_status(
        self,
        transaction_id: str,
        *,
        expected: str,
        status: str,
        controller: Mapping[str, Any],
    ) -> sqlite3.Row:
        now = utc_now().isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE media_lifecycle_transactions SET status=?,receipt_json=?,lease_expires_at='',updated_at=? WHERE id=? AND status=?",
                (status, _json(controller), now, transaction_id, expected),
            ).rowcount
            row = db.execute(
                "SELECT * FROM media_lifecycle_transactions WHERE id=?",
                (transaction_id,),
            ).fetchone()
            db.execute("COMMIT")
        if changed != 1 or row is None:
            raise MediaError(
                "media_worker_transition",
                "media worker lifecycle transaction changed concurrently",
                status=409,
            )
        return row

    def _recover_phase(
        self,
        row: sqlite3.Row,
        *,
        running: bool,
        for_teardown: bool,
    ) -> sqlite3.Row:
        status = row["status"]
        if status not in {"preparing", "releasing"} or not _lease_expired(row):
            return row
        if status == "preparing":
            recovered = "active" if running else ("released" if for_teardown else "failed")
        else:
            recovered = "active" if running else "released"
        return self._set_transaction_status(
            row["id"],
            expected=status,
            status=recovered,
            controller={
                "applied": recovered in {"active", "released"},
                "recovered": True,
                "observedRunning": running,
                "previousPhase": status,
            },
        )

    def _transaction_for_job(self, job_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT t.* FROM media_lifecycle_transactions t JOIN media_lifecycle_jobs j ON j.transaction_id=t.id WHERE j.job_id=? AND t.status!='consumed' ORDER BY t.created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()

    def _link(self, transaction_id: str, job_id: str) -> None:
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                (transaction_id, job_id),
            )

    def _all_jobs_terminal(self, transaction_id: str) -> bool:
        with self._connect() as db:
            rows = db.execute(
                "SELECT m.state FROM media_jobs m JOIN media_lifecycle_jobs j ON j.job_id=m.id WHERE j.transaction_id=?",
                (transaction_id,),
            ).fetchall()
        return bool(rows) and all(JobState(row["state"]) in TERMINAL_STATES for row in rows)


def _selected_status(result: Mapping[str, Any], service: str) -> Mapping[str, Any]:
    payload = result.get("data", result.get("result", result))
    rows = payload.get("serves", []) if isinstance(payload, Mapping) else []
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("name") == service]
    if len(matches) != 1:
        raise MediaError("media_worker_status", "managed serve status did not identify one media worker", status=503)
    return matches[0]


def _lease_expired(row: Mapping[str, Any]) -> bool:
    raw = row["lease_expires_at"]
    if not raw:
        return False
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise MediaError(
            "media_worker_state",
            "media worker lifecycle lease is invalid",
            status=500,
        ) from exc
    if parsed.tzinfo is None:
        raise MediaError(
            "media_worker_state",
            "media worker lifecycle lease lacks a timezone",
            status=500,
        )
    return parsed.astimezone(dt.timezone.utc) <= utc_now()


def _approval_request(
    job_id: str,
    principal: str,
    receipt: MediaLifecycleReceipt,
) -> dict[str, Any]:
    """Project an operator action without exposing controller-private state."""

    return {
        "schema": "anvil-serving.media-lifecycle-approval/v1",
        "transactionId": receipt.transaction_id,
        "service": receipt.service,
        "action": receipt.action,
        "humanRequired": True,
        "approved": not receipt.human_required,
        "operatorAction": {
            "tool": "media_worker_prepare",
            "arguments": {
                "job_id": job_id,
                "principal": principal,
                "service": receipt.service,
                "manifest": receipt.manifest,
                "transaction_id": receipt.transaction_id,
                "dry_run": False,
                "confirm": True,
                "human_approved": True,
            },
        },
    }


def _require_persisted_approval(
    job,
    *,
    transaction_id: str,
    principal: str,
    service: str,
    manifest: str,
) -> bool:
    expected_arguments = {
        "job_id": job.id,
        "principal": principal,
        "service": service,
        "manifest": manifest,
        "transaction_id": transaction_id,
        "dry_run": False,
        "confirm": True,
        "human_approved": True,
    }
    approval = job.approval
    operator_action = (
        approval.get("operatorAction") if isinstance(approval, Mapping) else None
    )
    arguments = (
        operator_action.get("arguments")
        if isinstance(operator_action, Mapping)
        else None
    )
    if job.state not in {JobState.AWAITING_APPROVAL, JobState.PREPARING}:
        raise MediaError(
            "media_lifecycle_approval_consumed",
            "media worker approval is missing, consumed, or replayed",
            status=409,
        )
    recovering = job.state == JobState.PREPARING
    if (
        not transaction_id
        or not isinstance(approval, Mapping)
        or approval.get("schema") != "anvil-serving.media-lifecycle-approval/v1"
        or approval.get("transactionId") != transaction_id
        or approval.get("service") != service
        or approval.get("action") != "prepare"
        or approval.get("humanRequired") is not True
        or approval.get("approved") is not recovering
        or not isinstance(operator_action, Mapping)
        or operator_action.get("tool") != "media_worker_prepare"
        or arguments != expected_arguments
    ):
        raise MediaError(
            "media_lifecycle_approval_mismatch",
            "approved media worker action does not match the persisted preview",
            status=409,
        )
    return recovering


def _receipt_from_row(
    row: Mapping[str, Any], *, action: str, applied: bool | None = None
) -> MediaLifecycleReceipt:
    controller = json.loads(row["receipt_json"])
    return MediaLifecycleReceipt(
        row["id"], row["service"], action,
        _controller_applied(controller) if applied is None else applied,
        bool(row["owns_instance"]), bool(row["preexisting"]), False, controller,
        row["manifest"],
    )


def _controller_applied(
    controller: Mapping[str, Any],
    *,
    default: bool = False,
) -> bool:
    applied = controller.get("applied")
    if isinstance(applied, bool):
        return applied
    data = controller.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("applied"), bool):
        return data["applied"]
    return default


def _require_manifest_match(row: Mapping[str, Any], manifest: str) -> None:
    if row["manifest"] != manifest:
        raise MediaError(
            "media_worker_manifest_mismatch",
            "media worker lifecycle manifest does not match the reserved transaction",
            status=409,
        )


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = ["MediaLifecycleReceipt", "MediaWorkerLifecycle"]
