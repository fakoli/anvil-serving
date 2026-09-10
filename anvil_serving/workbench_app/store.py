"""Private, principal-scoped conversation state, separate from Serving evidence."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from ..observability.dashboard.contracts import ObservatoryError, canonical


class PrivateStore:
    def __init__(self, path, *, retention_days=30):
        self.path = Path(path)
        if not self.path.is_absolute() or self.path.is_symlink():
            raise ValueError("Use an absolute private Workbench state file")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
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
