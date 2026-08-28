"""Owned, confirmation-gated lifecycle transactions for media workers."""

from __future__ import annotations

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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS media_lifecycle_jobs (
                    transaction_id TEXT NOT NULL
                        REFERENCES media_lifecycle_transactions(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL REFERENCES media_jobs(id) ON DELETE CASCADE,
                    PRIMARY KEY(transaction_id, job_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS media_lifecycle_active_service
                    ON media_lifecycle_transactions(service)
                    WHERE status = 'active';
                """
            )

    def prepare(
        self,
        job_id: str,
        *,
        principal: str,
        service: str,
        manifest: str = "",
        confirm: bool = False,
        human_approved: bool = False,
    ) -> MediaLifecycleReceipt:
        job = self.store.get(job_id, principal=principal)
        if job.state in TERMINAL_STATES:
            raise MediaError("job_terminal", "terminal media jobs cannot prepare a worker", status=409)
        status = self._status_operation({"manifest": manifest, "names": [service]})
        row = _selected_status(status, service)
        if row.get("running") is True and row.get("health_status") is not None:
            receipt = self._record(job_id, service, owns=False, preexisting=True, controller=status)
            return receipt

        existing = self._active_for_service(service)
        if existing is not None:
            self._link(existing["id"], job_id)
            return _receipt_from_row(existing, action="prepare")

        approved = bool(confirm and human_approved)
        controller = self._manage_operation(
            {
                "action": "up",
                "manifest": manifest,
                "names": [service],
                "dry_run": not approved,
                "confirm": approved,
            }
        )
        if not approved:
            receipt = self._ephemeral(service, "prepare", controller, human_required=True)
            if job.state == JobState.ACCEPTED:
                self.store.transition(
                    job_id,
                    JobState.AWAITING_APPROVAL,
                    principal=principal,
                    reason="media_worker_start_requires_approval",
                    approval=receipt.as_dict(),
                )
            return receipt

        receipt = self._record(job_id, service, owns=True, preexisting=False, controller=controller)
        if job.state in {JobState.ACCEPTED, JobState.AWAITING_APPROVAL}:
            self.store.transition(
                job_id,
                JobState.PREPARING,
                principal=principal,
                reason="media_worker_start_approved",
                approval=receipt.as_dict(),
            )
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
        self.store.get(job_id, principal=principal)
        row = self._transaction_for_job(job_id)
        if row is None:
            return self._ephemeral("", "teardown", {"reason": "not_owned"})
        service = row["service"]
        if not bool(row["owns_instance"]) or bool(row["preexisting"]):
            return _receipt_from_row(row, action="teardown", applied=False)
        if not self._all_jobs_terminal(row["id"]):
            return self._ephemeral(
                service,
                "teardown",
                {"reason": "owned_jobs_nonterminal"},
                transaction_id=row["id"],
            )
        approved = bool(confirm and human_approved)
        controller = self._manage_operation(
            {
                "action": "down",
                "manifest": manifest,
                "names": [service],
                "dry_run": not approved,
                "confirm": approved,
            }
        )
        if not approved:
            return self._ephemeral(
                service,
                "teardown",
                controller,
                human_required=True,
                transaction_id=row["id"],
                owns=True,
            )
        now = utc_now().isoformat()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE media_lifecycle_transactions SET status='released',receipt_json=?,updated_at=? WHERE id=? AND status='active'",
                (_json(controller), now, row["id"]),
            )
            db.execute("COMMIT")
        return MediaLifecycleReceipt(
            row["id"], service, "teardown", True, True, False, False, controller
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
                    db.execute(
                        "INSERT OR IGNORE INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                        (existing["id"], job_id),
                    )
                    db.execute("COMMIT")
                    return _receipt_from_row(existing, action="prepare")
            db.execute(
                "INSERT INTO media_lifecycle_transactions(id,service,status,owns_instance,preexisting,receipt_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (transaction_id, service, "active" if owns else "observed", int(owns), int(preexisting), _json(controller), now, now),
            )
            db.execute(
                "INSERT INTO media_lifecycle_jobs(transaction_id,job_id) VALUES (?,?)",
                (transaction_id, job_id),
            )
            db.execute("COMMIT")
        return MediaLifecycleReceipt(
            transaction_id, service, "prepare", bool(controller.get("applied", owns)), owns,
            preexisting, False, controller
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
    ) -> MediaLifecycleReceipt:
        return MediaLifecycleReceipt(
            transaction_id or secrets.token_urlsafe(18), service, action, False, owns, False,
            human_required, controller
        )

    def _active_for_service(self, service: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM media_lifecycle_transactions WHERE service=? AND status='active'",
                (service,),
            ).fetchone()

    def _transaction_for_job(self, job_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT t.* FROM media_lifecycle_transactions t JOIN media_lifecycle_jobs j ON j.transaction_id=t.id WHERE j.job_id=? ORDER BY t.created_at DESC LIMIT 1",
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
    payload = result.get("result", result)
    rows = payload.get("serves", []) if isinstance(payload, Mapping) else []
    matches = [row for row in rows if isinstance(row, Mapping) and row.get("name") == service]
    if len(matches) != 1:
        raise MediaError("media_worker_status", "managed serve status did not identify one media worker", status=503)
    return matches[0]


def _receipt_from_row(
    row: Mapping[str, Any], *, action: str, applied: bool | None = None
) -> MediaLifecycleReceipt:
    controller = json.loads(row["receipt_json"])
    return MediaLifecycleReceipt(
        row["id"], row["service"], action,
        bool(controller.get("applied")) if applied is None else applied,
        bool(row["owns_instance"]), bool(row["preexisting"]), False, controller
    )


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = ["MediaLifecycleReceipt", "MediaWorkerLifecycle"]
