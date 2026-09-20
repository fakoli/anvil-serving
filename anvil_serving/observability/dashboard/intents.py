"""Private web intent correlation, not a replacement for owner operation state."""

from __future__ import annotations

import json
import os
import hashlib
import base64
import math
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .contracts import ObservatoryError, canonical, digest, identifier, timestamp

TERMINAL = frozenset({"succeeded", "failed", "recovered"})
PUBLIC_OPERATION = frozenset({"id", "resource_id", "host_id", "action_id", "label", "actor", "service_identity", "submitted_at", "updated_at", "status", "native_state", "owner_operation_id", "execution_outcome", "verification", "recovery", "events", "evidence_id", "benchmark_job_ref", "candidate_digest", "baseline_digest"})
_RUN_RECORD_BYTES = 64 * 1024
_RUN_PAGE_BYTES = 48 * 1024
_RUN_SCAN_ROWS = 128
_RUN_QUERY_ROWS = 16
_RUN_READ_SECONDS = 1.0


def _encode_run_cursor(frontier: tuple[float, str] | None) -> str | None:
    if frontier is None:
        return None
    payload = json.dumps({"created": frontier[0], "id": frontier[1]}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _decode_run_cursor(value: str | None) -> tuple[float, str] | None:
    if value is None:
        return None
    if type(value) is not str or not 1 <= len(value) <= 128:
        raise ObservatoryError("invalid_run_list", "Select a valid run cursor.")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw)
        created = decoded["created"]
        native_id = identifier(decoded["id"])
        if type(created) not in {int, float} or isinstance(created, bool) or not math.isfinite(created):
            raise ValueError
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, json.JSONDecodeError, ObservatoryError):
        raise ObservatoryError("invalid_run_list", "Select a valid run cursor.") from None
    return float(created), native_id


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
        # ``connect_profiles`` is an additive table. Keep the established v1
        # journal marker so an operator can roll back to the prior Observatory
        # binary without replacing, resetting, or downgrading intent state.
        if version not in (0, 1):
            self.db.close()
            raise ValueError("unsupported journal schema; use the recorded recovery procedure")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, actor TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS previews(id TEXT PRIMARY KEY, actor TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS intents(id TEXT PRIMARY KEY, intent_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                actor TEXT NOT NULL, resource TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY, resource TEXT NOT NULL, body TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS connect_profiles(subject_digest TEXT PRIMARY KEY, profile_id TEXT UNIQUE NOT NULL,
                body TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
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

    def connect_profile(self, subject: str) -> dict:
        """Create one empty local workspace profile for one verified opaque subject.

        The journal retains only a digest of the Connect subject. It does not
        copy IdP attributes, grants, or session credentials into Observatory.
        """
        if type(subject) is not str or "\x00" in subject or "\r" in subject or "\n" in subject or "\t" in subject:
            raise ValueError("invalid Connect subject")
        try:
            raw_subject = subject.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("invalid Connect subject") from exc
        if not 1 <= len(raw_subject) <= 192:
            raise ValueError("invalid Connect subject")
        subject_digest = hashlib.sha256(raw_subject).hexdigest()
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT body FROM connect_profiles WHERE subject_digest=?", (subject_digest,)).fetchone()
                if row is not None:
                    self.db.execute("COMMIT")
                    return json.loads(row["body"])
                count = self.db.execute("SELECT count(*) FROM connect_profiles").fetchone()[0]
                if count >= 256:
                    raise ObservatoryError("connect_profile_limit", "Anvil Connect workspace enrollment is temporarily unavailable.", 503)
                now = self.clock()
                profile = {"schema": "anvil-observatory/connect-profile/v1", "id": "connect-" + subject_digest[:32],
                           "preferences": {}, "workspace": {}}
                self.db.execute("INSERT INTO connect_profiles VALUES(?,?,?,?,?)", (subject_digest, profile["id"], canonical(profile).decode(), now, now))
                self.db.execute("COMMIT")
                return profile
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

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
                conflicts = [i for i in active if i["resource_id"] == preview["resource_id"]]
                recovery_of = preview.get("private_recovery_of")
                permitted_recovery = (preview["action_id"] == "operation.recover" and recovery_of
                    and len(conflicts) == 1 and conflicts[0]["id"] == recovery_of
                    and conflicts[0]["action_id"] == "experiment.start"
                    and conflicts[0]["intent_key"] == preview.get("private_parameters", {}).get("run_id")
                    and conflicts[0]["status"] in {"manual_recovery_required", "outcome_unknown"})
                if conflicts and not permitted_recovery:
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

    def list_run_page(
        self, resources: frozenset[str], *, limit: int = 100, cursor: str | None = None,
    ) -> dict:
        """Return one bounded, authorization-scoped operation page.

        The caller supplies already-authorized resources; SQLite filters them before
        returning a row or using it as a pagination frontier.  Oversized/corrupt
        journal rows are skipped visibly rather than materialized into the browser
        response.
        """
        if (not isinstance(resources, frozenset) or any(type(value) is not str for value in resources)
                or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100):
            raise ValueError("invalid operation run page")
        if not resources:
            return {"items": [], "next_cursor": None, "partial": False, "truncated": False}
        after = _decode_run_cursor(cursor)
        allowed = None if "*" in resources else tuple(sorted(resources))
        items: list[dict] = []
        page_bytes = 0
        partial = False
        scanned = 0
        frontier = after
        exhausted = False
        deadline = time.monotonic() + _RUN_READ_SECONDS
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._lock.acquire(timeout=remaining):
            raise ObservatoryError("operation_source_unavailable", "The operation source is busy.", 503)
        previous_busy_timeout = None
        try:
            previous_busy_timeout = self.db.execute("PRAGMA busy_timeout").fetchone()[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise sqlite3.OperationalError
            self.db.execute("PRAGMA busy_timeout=" + str(max(1, int(remaining * 1000))))
            self.db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            while scanned < _RUN_SCAN_ROWS and len(items) < limit:
                predicates: list[str] = []
                values: list[object] = []
                if allowed is not None:
                    predicates.append("resource IN (" + ",".join("?" for _ in allowed) + ")")
                    values.extend(allowed)
                if frontier is not None:
                    predicates.append("(created < ? OR (created = ? AND id < ?))")
                    values.extend((frontier[0], frontier[0], frontier[1]))
                where = " WHERE " + " AND ".join(predicates) if predicates else ""
                rows = self.db.execute(
                    "SELECT id,resource,created,CASE WHEN length(CAST(body AS BLOB)) <= ? THEN body ELSE NULL END AS body"
                    + " FROM intents" + where + " ORDER BY created DESC,id DESC LIMIT ?",
                    (_RUN_RECORD_BYTES, *values, min(_RUN_QUERY_ROWS, _RUN_SCAN_ROWS - scanned)),
                ).fetchall()
                if not rows:
                    exhausted = True
                    break
                consumed = 0
                page_full = False
                for row in rows:
                    consumed += 1
                    scanned += 1
                    candidate = (float(row["created"]), row["id"])
                    raw = row["body"]
                    if type(raw) is not str:
                        partial = True
                        frontier = candidate
                        continue
                    try:
                        private = json.loads(raw)
                        if (private.get("id") != row["id"] or private.get("resource_id") != row["resource"]
                                or any(type(private.get(key)) is not str for key in (
                                    "id", "resource_id", "label", "status", "submitted_at", "updated_at",
                                ))):
                            raise ValueError
                        identifier(private["id"])
                        identifier(private["resource_id"])
                        item = {key: private.get(key) for key in (
                            "id", "resource_id", "host_id", "action_id", "label", "status",
                            "native_state", "submitted_at", "updated_at", "evidence_id", "benchmark_job_ref",
                        )}
                        encoded = canonical(item)
                    except Exception:
                        partial = True
                        frontier = candidate
                        continue
                    if page_bytes + len(encoded) > _RUN_PAGE_BYTES:
                        page_full = True
                        break
                    items.append(item)
                    page_bytes += len(encoded)
                    frontier = candidate
                    if len(items) == limit:
                        break
                if page_full:
                    break
                if consumed < len(rows):
                    break
                if len(rows) < _RUN_QUERY_ROWS:
                    exhausted = True
                    break
        except sqlite3.OperationalError:
            if time.monotonic() >= deadline:
                raise ObservatoryError("operation_source_unavailable", "The operation source exceeded its read deadline.", 503) from None
            raise
        finally:
            self.db.set_progress_handler(None, 0)
            if previous_busy_timeout is not None:
                self.db.execute("PRAGMA busy_timeout=" + str(previous_busy_timeout))
            self._lock.release()
        next_cursor = None if exhausted else _encode_run_cursor(frontier)
        return {
            "items": items,
            "next_cursor": next_cursor,
            "partial": partial,
            "truncated": next_cursor is not None,
        }

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
