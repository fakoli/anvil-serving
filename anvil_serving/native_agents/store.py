"""Private durable receipts. Restart never replays a dispatched command."""
import os
from pathlib import Path
import sqlite3
import threading
import stat
import time
from .types import AgentsError

def protected_directory(path: Path):
    if not path.is_absolute() or path != Path(os.path.normpath(path)):
        raise ValueError("store path must be absolute and normalized")
    for parent in (*reversed(path.parents), path):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
            raise ValueError("store directory custody unavailable")

def protected_file(path: Path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.getuid())
        or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("store file custody unavailable")

class Store:
    def __init__(self, path: Path, maximum_receipts_per_principal=100000):
        if type(maximum_receipts_per_principal) is not int or not 1 <= maximum_receipts_per_principal <= 100000: raise ValueError("invalid receipt capacity")
        self.maximum_receipts_per_principal = maximum_receipts_per_principal
        path = Path(path)
        if not path.is_absolute(): raise ValueError("store path must be absolute")
        protected_directory(path.parent)
        try:
            fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        except FileExistsError:
            protected_file(path)
        else:
            os.close(fd)
        protected_file(path)
        for suffix in ("-journal","-wal","-shm"):
            sidecar = path.with_name(path.name+suffix)
            if sidecar.exists() or sidecar.is_symlink(): protected_file(sidecar)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS cancellations(principal TEXT NOT NULL,id TEXT NOT NULL,workspace TEXT NOT NULL,session TEXT NOT NULL,PRIMARY KEY(principal,id));
          CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, principal TEXT NOT NULL,
            workspace TEXT NOT NULL, directory TEXT NOT NULL, created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS operations(principal TEXT NOT NULL, id TEXT NOT NULL,
            digest TEXT NOT NULL, workspace TEXT NOT NULL, session TEXT NOT NULL,
            kind TEXT NOT NULL, state TEXT NOT NULL, code TEXT, created REAL NOT NULL,
            PRIMARY KEY(principal,id));
        ''')
        self.db.execute("UPDATE operations SET state='unknown',code='owner_outcome_unknown' WHERE state IN ('reserved','dispatched')")

    def _operation(self, row):
        return {"id": row[1], "workspaceID": row[3], "sessionID": row[4],
                "kind": row[5], "state": row[6], **({"code": row[7]} if row[7] else {})}

    def operation(self, principal, request):
        with self.lock:
            row = self.db.execute("SELECT * FROM operations WHERE principal=? AND id=?", (principal, request)).fetchone()
            if not row: raise AgentsError(404, "not_found")
            return self._operation(row)

    def reserve(self, principal, request, digest, workspace, session, kind, directory=None):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                old = self.db.execute("SELECT * FROM operations WHERE principal=? AND id=?", (principal, request)).fetchone()
                if old:
                    if old[2] != digest: raise AgentsError(409, "request_conflict")
                    self.db.execute("COMMIT")
                    return self._operation(old), False
                if self.db.execute("SELECT count(*) FROM operations WHERE principal=?",(principal,)).fetchone()[0] >= self.maximum_receipts_per_principal:
                    raise AgentsError(503, "receipt_capacity")
                cancelled = self.db.execute("SELECT workspace,session FROM cancellations WHERE principal=? AND id=?",(principal,request)).fetchone()
                if cancelled:
                    if cancelled != (workspace,session) or kind not in ("prompt","invocation"): raise AgentsError(409,"request_conflict")
                    self.db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?)",(principal,request,digest,workspace,session,kind,"rejected","cancelled_before_dispatch",time.time()))
                    self.db.execute("COMMIT")
                    return self.operation(principal,request),False
                if kind in ("prompt","invocation") and self.db.execute(
                    "SELECT count(*) FROM operations WHERE principal=? AND workspace=? AND session=? AND kind IN ('prompt','invocation') AND state IN ('reserved','dispatched','unknown') AND NOT EXISTS (SELECT 1 FROM cancellations c WHERE c.principal=operations.principal AND c.id=operations.id AND c.workspace=operations.workspace AND c.session=operations.session)",
                    (principal,workspace,session)).fetchone()[0] >= 128:
                    raise AgentsError(429,"session_pending_limit")
                if directory is not None:
                    self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (session, principal, workspace, directory, time.time()))
                self.db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?)", (principal, request, digest, workspace, session, kind, "reserved", None, time.time()))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            return self.operation(principal, request), True

    def cancel(self, principal, workspace, session, requests):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                existing = self.db.execute("SELECT count(*) FROM cancellations WHERE principal=?",(principal,)).fetchone()[0]
                for request in requests:
                    old = self.db.execute("SELECT workspace,session FROM cancellations WHERE principal=? AND id=?",(principal,request)).fetchone()
                    if old:
                        if old != (workspace,session): raise AgentsError(409,"request_conflict")
                        continue
                    op = self.db.execute("SELECT workspace,session,kind FROM operations WHERE principal=? AND id=?",(principal,request)).fetchone()
                    if op and (op[0] != workspace or op[1] != session or op[2] not in ("prompt","invocation")): raise AgentsError(409,"request_conflict")
                    if existing >= self.maximum_receipts_per_principal: raise AgentsError(503,"receipt_capacity")
                    self.db.execute("INSERT INTO cancellations VALUES(?,?,?,?)",(principal,request,workspace,session)); existing += 1
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK"); raise

    def update(self, principal, request, state, code=None):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                changed = self.db.execute("UPDATE operations SET state=?,code=? WHERE principal=? AND id=? AND state NOT IN ('accepted','rejected','completed')", (state, code, principal, request)).rowcount
                if changed and state == "rejected":
                    row = self.db.execute("SELECT workspace,session,kind FROM operations WHERE principal=? AND id=?",(principal,request)).fetchone()
                    if row and row[2] == "create":
                        self.db.execute("DELETE FROM sessions WHERE principal=? AND workspace=? AND id=?",(principal,row[0],row[1]))
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def session(self, principal, workspace, session):
        with self.lock:
            row = self.db.execute("SELECT directory FROM sessions WHERE principal=? AND workspace=? AND id=?", (principal, workspace, session)).fetchone()
            if not row: raise AgentsError(404, "not_found")
            return row[0]

    def sessions(self, principal, workspace):
        with self.lock:
            return self.db.execute("SELECT id,directory FROM sessions WHERE principal=? AND workspace=? ORDER BY created DESC LIMIT 512", (principal, workspace)).fetchall()

    def pending(self, principal, workspace, session):
        with self.lock:
            return [row[0] for row in self.db.execute("SELECT id FROM operations WHERE principal=? AND workspace=? AND session=? AND kind IN ('prompt','invocation') AND state IN ('reserved','dispatched','unknown') AND NOT EXISTS (SELECT 1 FROM cancellations c WHERE c.principal=operations.principal AND c.id=operations.id AND c.workspace=operations.workspace AND c.session=operations.session)", (principal, workspace, session))]
