"""Private, principal-scoped conversation state, separate from Serving evidence."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path

from ..observability.dashboard.contracts import ObservatoryError, canonical


_TASK_RUN_RECORD_BYTES = 64 * 1024
_TASK_RUN_SCAN_ROWS = 128
_TASK_RUN_QUERY_ROWS = 16


def _regular_state_file(path):
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & 0x400):
        raise ValueError("Workbench state must be a regular file, never a link or reparse point")
    return metadata


class PrivateStore:
    def __init__(self, path, *, retention_days=30):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.is_symlink():
            raise ValueError("Use an absolute private Workbench state file")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for parent in self.path.parents:
            metadata = parent.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                raise ValueError("Workbench state parents must not be links or reparse points")
        try:
            _regular_state_file(self.path)
        except FileNotFoundError:
            pass
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), 0o600)
        try:
            opened = os.fstat(fd)
            current = _regular_state_file(self.path)
            if not stat.S_ISREG(opened.st_mode) or (os.name == "posix" and not os.path.samestat(opened, current)):
                raise ValueError("Workbench state file changed while opening")
            if os.name == "posix":
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        page_size = self.db.execute("PRAGMA page_size").fetchone()[0]
        self.db.execute(f"PRAGMA max_page_count={256 * 1024**2 // page_size}")
        self.db.execute("CREATE TABLE IF NOT EXISTS records (kind TEXT, owner TEXT, id TEXT, updated REAL, body TEXT, PRIMARY KEY(kind,owner,id))")
        self.retention_days = retention_days
        # Claim bindings and idempotency tombstones must survive retention.
        # Only terminal chat contents expire; a retry key never becomes new work.
        self.db.execute("DELETE FROM records WHERE kind='conversation' AND json_extract(body,'$.status') IN ('completed','failed','cancelled','interrupted') AND updated < ?", (time.time() - retention_days * 86400,))
        self.db.commit()

    def put(self, kind, owner, key, body):
        raw = canonical(body).decode()
        if len(raw.encode()) > 512 * 1024:
            raise ObservatoryError("record_limit", "This conversation reached its retention limit. Start a new thread.", 409)
        with self.lock, self.db:
            exists = self.db.execute("SELECT 1 FROM records WHERE kind=? AND owner=? AND id=?", (kind, owner, key)).fetchone()
            if not exists and self.db.execute("SELECT count(*) FROM records").fetchone()[0] >= 10000:
                raise ObservatoryError("record_limit", "Private workspace storage is full. Existing operations remain readable.", 409)
            self.db.execute("INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(kind,owner,id) DO UPDATE SET updated=excluded.updated, body=excluded.body",
                            (kind, owner, key, time.time(), raw))

    def get(self, kind, owner, key):
        with self.lock:
            row = self.db.execute("SELECT body FROM records WHERE kind=? AND owner=? AND id=?", (kind, owner, key)).fetchone()
        if not row:
            raise ObservatoryError("not_found", "This private record is unavailable.", 404)
        return json.loads(row[0])

    def list(self, kind, owner):
        with self.lock:
            rows = self.db.execute("SELECT body FROM records WHERE kind=? AND owner=? ORDER BY updated DESC LIMIT 200", (kind, owner)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def task_binding_run_page(self, owner, projects, *, high_water=None, before=None, limit=100, deadline_seconds=2.0):
        """Read bounded task-binding metadata without materializing private bodies.

        ``projects`` is already authorized by the Workbench service.  SQLite
        filters that set before it extracts any browser-visible field, while the
        record rowid provides an immutable page frontier for bindings that are
        updated in place.
        """
        if (type(owner) is not str or not owner or not isinstance(projects, frozenset)
                or not projects or any(type(value) is not str or not value for value in projects)
                or type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= 100
                or type(deadline_seconds) not in {int, float} or isinstance(deadline_seconds, bool)
                or not 0 < deadline_seconds <= 2):
            raise ValueError("invalid task binding run page")
        if high_water is not None and (type(high_water) is not int or isinstance(high_water, bool) or high_water < 1):
            raise ValueError("invalid task binding run watermark")
        if before is not None and (type(before) is not int or isinstance(before, bool) or before < 1):
            raise ValueError("invalid task binding run frontier")
        deadline = time.monotonic() + float(deadline_seconds)
        if not self.lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise ObservatoryError("workspace_source_unavailable", "The task binding owner is busy.", 503)
        previous_busy_timeout = None
        try:
            allowed = tuple(sorted(projects))
            project_sql = "CASE WHEN json_valid(body) THEN json_extract(body,'$.project_id') END"
            project_where = project_sql + " IN (" + ",".join("?" for _ in allowed) + ")"
            previous_busy_timeout = self.db.execute("PRAGMA busy_timeout").fetchone()[0]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise sqlite3.OperationalError
            self.db.execute("PRAGMA busy_timeout=" + str(max(1, int(remaining * 1000))))
            self.db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            if high_water is None:
                row = self.db.execute(
                    "SELECT max(rowid) FROM records WHERE kind='task-binding' AND owner=? AND " + project_where,
                    (owner, *allowed),
                ).fetchone()
                high_water = row[0] if row else None
                if high_water is None:
                    return {"items": [], "high_water": None, "frontier": None, "partial": False, "truncated": False}
            frontier = before if before is not None else high_water + 1
            fields = ",".join(
                "CASE WHEN length(CAST(body AS BLOB)) <= ? AND json_valid(body) THEN json_extract(body,'$.%s') END AS %s" % (path, alias)
                for path, alias in (
                    ("id", "binding_id"), ("owner", "binding_owner"), ("project_id", "project_id"),
                    ("task_id", "task_id"), ("lease_id", "lease_id"), ("status", "status"),
                    ("provider_id", "provider_id"), ("artifact_digest", "artifact_digest"),
                    ("verification.passed", "verification_passed"),
                )
            )
            items, partial, scanned, exhausted = [], False, 0, False
            while scanned < _TASK_RUN_SCAN_ROWS and len(items) < limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise sqlite3.OperationalError
                rows = self.db.execute(
                    "SELECT rowid,id,updated," + fields + " FROM records WHERE kind='task-binding' AND owner=?"
                    " AND rowid <= ? AND rowid < ? AND " + project_where
                    + " ORDER BY rowid DESC LIMIT ?",
                    (_TASK_RUN_RECORD_BYTES,) * 9 + (owner, high_water, frontier, *allowed,
                                                       min(_TASK_RUN_QUERY_ROWS, _TASK_RUN_SCAN_ROWS - scanned)),
                ).fetchall()
                if not rows:
                    exhausted = True
                    break
                consumed = 0
                for row in rows:
                    consumed += 1
                    scanned += 1
                    frontier = row[0]
                    values = dict(zip(("binding_id", "binding_owner", "project_id", "task_id", "lease_id", "status", "provider_id", "artifact_digest", "verification_passed"), row[3:]))
                    if (any(type(values[key]) is not str or not values[key] for key in ("binding_id", "binding_owner", "project_id", "task_id", "status"))
                            or values["binding_id"] != row[1] or values["binding_owner"] != owner):
                        partial = True
                        continue
                    items.append({"rowid": row[0], "updated_at": float(row[2]), **values})
                    if len(items) == limit:
                        break
                if consumed < len(rows):
                    break
                if len(rows) < _TASK_RUN_QUERY_ROWS:
                    exhausted = True
                    break
            if scanned >= _TASK_RUN_SCAN_ROWS:
                exhausted = False
            if time.monotonic() >= deadline:
                raise sqlite3.OperationalError
            return {"items": items, "high_water": high_water, "frontier": frontier, "partial": partial,
                    "truncated": not exhausted}
        except sqlite3.OperationalError:
            if time.monotonic() >= deadline:
                raise ObservatoryError("workspace_source_unavailable", "The task binding owner exceeded its read deadline.", 503) from None
            raise
        finally:
            self.db.set_progress_handler(None, 0)
            if previous_busy_timeout is not None:
                self.db.execute("PRAGMA busy_timeout=" + str(previous_busy_timeout))
            self.lock.release()

    def delete(self, kind, owner, key):
        with self.lock, self.db:
            self.db.execute("DELETE FROM records WHERE kind=? AND owner=? AND id=?", (kind, owner, key))

    def recover(self, kind, states):
        with self.lock:
            rows = self.db.execute("SELECT owner,id,body FROM records WHERE kind=?", (kind,)).fetchall()
            for owner, key, raw in rows:
                body = json.loads(raw)
                if body.get("status") in states:
                    body.update(status="interrupted", error="The service restarted. The request was not replayed.")
                    self.put(kind, owner, key, body)

    def close(self):
        with self.lock:
            self.db.close()
