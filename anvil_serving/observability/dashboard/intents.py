"""Private web intent correlation, not a replacement for owner operation state."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .contracts import ObservatoryError, canonical, digest, timestamp

TERMINAL = frozenset({"succeeded", "failed", "recovered"})
PUBLIC_OPERATION = frozenset({"id", "resource_id", "host_id", "action_id", "label", "actor", "service_identity", "submitted_at", "updated_at", "status", "native_state", "owner_operation_id", "execution_outcome", "verification", "recovery", "events", "evidence_id", "candidate_digest", "baseline_digest"})


class IntentStore:
    def __init__(self, path: str | Path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("journal must not be a symlink")
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("unsupported journal schema; use the recorded recovery procedure")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, actor TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY, actor TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY, intent_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                actor TEXT NOT NULL, resource TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, resource TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
            PRAGMA user_version=1;
        """)
        # A web process dying is ambiguous delivery. Never re-dispatch these
        # records on startup. Owner lookups may subsequently resolve them.
        for item in self.list_private():
            if item["status"] in {"submitting", "running", "verifying", "recovering"}:
                self.update(item["id"], status="outcome_unknown", event=("facade", "reconciling", "Web service restarted; checking the existing owner operation."))

    @staticmethod
    def public(item: dict) -> dict:
        return {key: value for key, value in item.items() if key in PUBLIC_OPERATION}

    def close(self):
        with self._lock:
            self.db.close()

    def save_draft(self, actor: str, body: dict, *, previous=None) -> dict:
        with self._lock:
            version = 1
            if previous:
                old = self.draft(previous, actor)
                if old["resource_id"] != body["resource_id"]:
                    raise ObservatoryError("draft_conflict", "A draft belongs to one resource.", 409)
                version = old["version"] + 1
            count = self.db.execute("SELECT count(*) FROM drafts WHERE actor=?", (actor,)).fetchone()[0]
            if count >= 1000:
                raise ObservatoryError("draft_limit", "The private draft limit has been reached.", 429)
            item = {**body, "id": str(uuid.uuid4()), "version": version}
            self.db.execute("INSERT INTO drafts VALUES(?,?,?,?)", (item["id"], actor, canonical(item).decode(), self.clock()))
            return item

    def draft(self, key: str, actor: str) -> dict:
        return self._owned("drafts", key, actor)

    def save_preview(self, actor: str, preview: dict) -> dict:
        with self._lock:
            self.db.execute("DELETE FROM previews WHERE created < ?", (self.clock() - 600,))
            item = {**preview, "id": str(uuid.uuid4())}
            self.db.execute("INSERT INTO previews VALUES(?,?,?,?)", (item["id"], actor, canonical(item).decode(), self.clock()))
            return item

    def preview(self, key: str, actor: str) -> dict:
        return self._owned("previews", key, actor)

    def _owned(self, table: str, key: str, actor: str) -> dict:
        if table not in {"drafts", "previews"}:
            raise ValueError("invalid journal collection")
        with self._lock:
            row = self.db.execute(f"SELECT body FROM {table} WHERE id=? AND actor=?", (key, actor)).fetchone()
        if row is None:
            raise ObservatoryError("not_found", "The saved review is unavailable; create a fresh preview.", 404)
        return json.loads(row["body"])

    def existing(self, intent_key: str, actor: str, preview_id: str) -> dict | None:
        fingerprint = digest({"actor": actor, "preview_id": preview_id})
        with self._lock:
            row = self.db.execute("SELECT fingerprint,body FROM intents WHERE intent_key=?", (intent_key,)).fetchone()
        if not row:
            return None
        if row["fingerprint"] != fingerprint:
            raise ObservatoryError("intent_conflict", "This confirmation key was already used for different content.", 409)
        return json.loads(row["body"])

    def accept(self, preview: dict, actor: str, intent_key: str) -> tuple[dict, bool]:
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                old = self.existing(intent_key, actor, preview["id"])
                if old:
                    self.db.execute("COMMIT")
                    return old, False
                active = [i for i in self.list_private() if i["status"] not in TERMINAL]
                if len(active) >= 16:
                    raise ObservatoryError("operation_limit", "Resolve current operations before submitting more.", 429)
                # This facade fence limits accidental dispatch while an outcome
                # is unresolved. The controller still owns cross-client locking.
                if any(i["resource_id"] == preview["resource_id"] for i in active):
                    raise ObservatoryError("operation_conflict", "An operation on this resource still requires attention.", 409)
                now = timestamp()
                item = {"id": str(uuid.uuid4()), "resource_id": preview["resource_id"], "host_id": preview["host_id"],
                        "action_id": preview["action_id"], "label": preview["label"], "actor": actor,
                        "service_identity": preview.get("service_identity", "configured-controller-principal"),
                        "submitted_at": now, "updated_at": now, "status": "submitting", "native_state": None,
                        "owner_operation_id": intent_key, "execution_outcome": "unknown",
                        "verification": {"status": "pending", "message": "The owner has not been verified."},
                        "recovery": {"status": "not_attempted", "message": preview.get("recovery", "No automatic recovery has run.")},
                        "events": [{"at": now, "source": "facade", "phase": "submitting", "message": "Confirmation saved before controller delivery."}],
                        "evidence_id": None, "candidate_digest": preview["candidate_digest"], "baseline_digest": preview["baseline_digest"],
                        "private_preview": preview, "intent_key": intent_key}
                self.db.execute("INSERT INTO intents VALUES(?,?,?,?,?,?,?,?)", (item["id"], intent_key,
                    digest({"actor": actor, "preview_id": preview["id"]}), actor, item["resource_id"], canonical(item).decode(), self.clock(), self.clock()))
                self.db.execute("COMMIT")
                return item, True
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def get(self, operation_id: str) -> dict:
        with self._lock:
            row = self.db.execute("SELECT body FROM intents WHERE id=?", (operation_id,)).fetchone()
        if row is None:
            raise ObservatoryError("not_found", "This operation is unavailable.", 404)
        return json.loads(row["body"])

    def update(self, operation_id: str, *, event=None, **changes) -> dict:
        with self._lock:
            item = self.get(operation_id)
            item.update(changes)
            item["updated_at"] = timestamp()
            if event:
                source, phase, message = event
                item["events"] = (item["events"] + [{"at": item["updated_at"], "source": source, "phase": phase, "message": message}])[-100:]
            self.db.execute("UPDATE intents SET body=?, updated=? WHERE id=?", (canonical(item).decode(), self.clock(), operation_id))
            return item

    def list_private(self) -> list[dict]:
        with self._lock:
            return [json.loads(row[0]) for row in self.db.execute("SELECT body FROM intents ORDER BY created DESC LIMIT 10016")]

    def save_evidence(self, resource: str, body: dict) -> str:
        key = str(uuid.uuid4())
        with self._lock:
            self.db.execute("INSERT INTO evidence VALUES(?,?,?,?)", (key, resource, canonical(body).decode(), self.clock()))
        return key

    def evidence(self, key: str) -> tuple[str, dict]:
        with self._lock:
            row = self.db.execute("SELECT resource,body FROM evidence WHERE id=?", (key,)).fetchone()
        if row is None:
            raise ObservatoryError("not_found", "Evidence is unavailable.", 404)
        return row[0], json.loads(row[1])

    def prune(self):
        """Expire only terminal correlation records; preserve linked evidence."""
        with self._lock:
            terminal = []
            for row in self.db.execute("SELECT id,body,updated FROM intents ORDER BY updated DESC"):
                if json.loads(row["body"])["status"] in TERMINAL:
                    terminal.append(row)
            for i, row in enumerate(terminal):
                if i >= 10000 or row["updated"] < self.clock() - 30 * 86400:
                    self.db.execute("DELETE FROM intents WHERE id=?", (row["id"],))
            self.db.execute("DELETE FROM drafts WHERE created<?", (self.clock() - 7 * 86400,))
