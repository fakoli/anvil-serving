"""Durable controller operation persistence and idempotency fingerprints."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Iterator, Mapping, Optional
import urllib.parse
import uuid

from .errors import ControllerError
from .security import _json_dumps, _sanitize_persisted_value, _strict_json_loads


_IDEMPOTENCY_KEY_HEADER = "X-Anvil-Idempotency-Key"
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_CONTEXT_FIELDS = ("topology", "execution_host", "execution_runtime")
_TOMBSTONE_BYTES_PER_RECORD = 16
_TOMBSTONE_MIN_BYTES = 128
_TOMBSTONE_HASH_COUNT = 7

DEFAULT_IDEMPOTENCY_RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_IDEMPOTENCY_MAX_RECORDS = 1024
DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES = 64 * 1024
DEFAULT_IDEMPOTENCY_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".anvil-serving", "controller-operations.sqlite3"
)

Clock = Callable[[], float]


def _bounded_persisted_value(
    value: Mapping[str, Any], secret: Optional[str], max_bytes: int
) -> dict[str, Any]:
    safe = _sanitize_persisted_value(dict(value), secret)
    try:
        if len(_json_dumps(safe).encode("utf-8")) <= max_bytes:
            return safe
    except (TypeError, ValueError):
        pass
    return {
        "ok": False,
        "error": {
            "code": "persisted_result_too_large",
            "message": "persisted operation result exceeded the configured limit",
            "details": {"max_result_bytes": max_bytes},
        },
    }


def _is_persistence_failure(value: Mapping[str, Any]) -> bool:
    error = value.get("error")
    return isinstance(error, Mapping) and error.get("code") == "persisted_result_too_large"


def _idempotency_key(headers: Any) -> Optional[str]:
    values = headers.get_all(_IDEMPOTENCY_KEY_HEADER) or []
    if not values:
        return None
    if len(values) != 1 or not _IDEMPOTENCY_KEY_RE.fullmatch(values[0]):
        raise ControllerError(
            "bad_idempotency_key",
            "%s must be a single 1-128 character token" % _IDEMPOTENCY_KEY_HEADER,
            status=400,
        )
    return values[0]


def _operation_status_key(path_segment: str) -> str:
    if re.search(r"%(?![0-9A-Fa-f]{2})", path_segment):
        raise ControllerError(
            "bad_idempotency_key",
            "operation status route requires a valid idempotency key",
            status=400,
        )
    try:
        key = urllib.parse.unquote_to_bytes(path_segment).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControllerError(
            "bad_idempotency_key",
            "operation status route requires a valid idempotency key",
            status=400,
        ) from exc
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ControllerError(
            "bad_idempotency_key",
            "operation status route requires a valid idempotency key",
            status=400,
        )
    return key


def _idempotency_context(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(_IDEMPOTENCY_CONTEXT_FIELDS):
        raise ControllerError(
            "bad_idempotency_context",
            "idempotent calls require topology, execution_host, and execution_runtime",
            status=400,
        )
    context: dict[str, str] = {}
    for field in _IDEMPOTENCY_CONTEXT_FIELDS:
        item = value.get(field)
        if not isinstance(item, str) or not _IDEMPOTENCY_KEY_RE.fullmatch(item):
            raise ControllerError(
                "bad_idempotency_context",
                "idempotency context fields must be bounded identifiers",
                status=400,
                details={"field": field},
            )
        context[field] = item
    return context


def _operation_fingerprint(
    tool_name: str,
    arguments: Mapping[str, Any],
    context: Mapping[str, str],
) -> str:
    payload = _json_dumps(
        {
            "arguments": dict(arguments),
            "execution_host": context["execution_host"],
            "execution_runtime": context["execution_runtime"],
            "operation": tool_name,
            "topology": context["topology"],
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class OperationStore:
    """Durable, bounded controller mutation records keyed by idempotency key."""

    def __init__(
        self,
        path: str = DEFAULT_IDEMPOTENCY_DB_PATH,
        *,
        retention_seconds: float = DEFAULT_IDEMPOTENCY_RETENTION_SECONDS,
        max_records: int = DEFAULT_IDEMPOTENCY_MAX_RECORDS,
        max_result_bytes: int = DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES,
        clock: Clock = time.time,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("idempotency database path must be a non-empty string")
        if retention_seconds <= 0:
            raise ValueError("idempotency retention must be positive")
        if max_records < 1:
            raise ValueError("idempotency max records must be positive")
        if max_result_bytes < 1:
            raise ValueError("idempotency max result bytes must be positive")
        self.path = path
        self.retention_seconds = float(retention_seconds)
        self.max_records = int(max_records)
        self.max_result_bytes = int(max_result_bytes)
        self._clock = clock
        self._tombstone_bytes = max(
            _TOMBSTONE_MIN_BYTES,
            self.max_records * _TOMBSTONE_BYTES_PER_RECORD,
        )
        self._lock = threading.RLock()
        self._active_keys: set[str] = set()
        self._lease_owner = uuid.uuid4().hex

    def claim(
        self, key: str, fingerprint: str, request_id: str
    ) -> tuple[str, Optional[dict[str, Any]]]:
        """Create a running record, or return an existing matching record."""
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_records(connection, now)
            row = connection.execute(
                "SELECT * FROM operation_records WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is not None:
                record = self._record(row)
                if record["status"] == "running":
                    failed = self._fail_orphaned(connection, key, now, self._orphan_grace())
                    if failed is not None:
                        record = failed
                connection.commit()
                if record["fingerprint"] != fingerprint:
                    return "conflict", record
                return "existing", record
            if self._is_tombstoned(connection, key):
                disposition = (
                    "expired" if self._is_tombstoned(connection, key, fingerprint) else "conflict"
                )
                connection.commit()
                return disposition, {"key": key, "status": "expired"}
            count = connection.execute("SELECT COUNT(*) FROM operation_records").fetchone()[0]
            if count >= self.max_records:
                connection.rollback()
                return "full", None
            connection.execute(
                """
                INSERT INTO operation_records (
                    idempotency_key, fingerprint, request_id, status,
                    created_at, updated_at, expires_at, response, result, error
                ) VALUES (?, ?, ?, 'running', ?, ?, ?, NULL, NULL, NULL)
                """,
                (key, fingerprint, request_id, now, now, now + self.retention_seconds),
            )
            connection.commit()
        return "claimed", None

    @contextmanager
    def executing(self, key: str) -> Iterator[None]:
        """Protect a dispatched running record from compaction until completion."""
        stop = threading.Event()
        with self._lock:
            self._active_keys.add(key)
        try:
            self._write_lease(key)
        except Exception:
            with self._lock:
                self._active_keys.discard(key)
            raise
        heartbeat = threading.Thread(
            target=self._heartbeat_lease,
            args=(key, stop),
            daemon=True,
        )
        heartbeat.start()
        try:
            yield
        finally:
            stop.set()
            heartbeat.join(timeout=max(1.0, min(5.0, self.retention_seconds / 3.0) + 1.0))
            self._delete_lease(key)
            with self._lock:
                self._active_keys.discard(key)

    def complete(
        self, key: str, status: str, response: Mapping[str, Any], auth_token: Optional[str]
    ) -> None:
        if status not in {"succeeded", "failed"}:
            raise ValueError("operation records can only complete as succeeded or failed")
        safe_response = _bounded_persisted_value(response, auth_token, self.max_result_bytes)
        if _is_persistence_failure(safe_response):
            status = "failed"
        result = safe_response if status == "succeeded" else None
        error = (
            safe_response.get("error")
            if status == "failed" and isinstance(safe_response, dict)
            else None
        )
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE operation_records
                SET status = ?, updated_at = ?, expires_at = ?, response = ?, result = ?, error = ?
                WHERE idempotency_key = ? AND status = 'running'
                """,
                (
                    status,
                    now,
                    now + self.retention_seconds,
                    _json_dumps(safe_response),
                    _json_dumps(result) if result is not None else None,
                    _json_dumps(error) if error is not None else None,
                    key,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError("active idempotency record is unavailable for completion")
            self._expire_records(connection, now)
            connection.commit()

    def _orphan_grace(self) -> float:
        """Grace before a running record's lease counts as stale.

        Several missed heartbeats, floored so scheduler jitter under load can
        never orphan a live operation (ADR-0033).
        """
        interval = max(0.05, min(5.0, self.retention_seconds / 3.0))
        return max(15.0, 4.0 * interval)

    def _fail_orphaned(
        self,
        connection: sqlite3.Connection,
        key: str,
        now: float,
        grace: float,
    ) -> Optional[dict[str, Any]]:
        """Fail-close one orphaned running record; None when it is live."""
        if key in self._active_keys:
            return None
        lease = connection.execute(
            "SELECT updated_at FROM operation_leases WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if lease is not None and float(lease["updated_at"]) > now - grace:
            return None
        row = connection.execute(
            "SELECT * FROM operation_records WHERE idempotency_key = ? AND status = 'running'",
            (key,),
        ).fetchone()
        if row is None:
            return None
        envelope = {
            "ok": False,
            "request_id": row["request_id"],
            "error": {
                "code": "operation_interrupted",
                "message": "controller restarted while this operation was running",
                "details": {"key": key},
            },
        }
        connection.execute(
            """
            UPDATE operation_records
            SET status = 'failed', updated_at = ?, expires_at = ?,
                response = ?, result = NULL, error = ?
            WHERE idempotency_key = ? AND status = 'running'
            """,
            (
                now,
                now + self.retention_seconds,
                _json_dumps(envelope),
                _json_dumps(envelope["error"]),
                key,
            ),
        )
        connection.execute(
            "DELETE FROM operation_leases WHERE idempotency_key = ?", (key,)
        )
        refreshed = connection.execute(
            "SELECT * FROM operation_records WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return self._record(refreshed) if refreshed is not None else None

    def recover_interrupted(
        self, *, grace_seconds: Optional[float] = None
    ) -> list[dict[str, Any]]:
        """Fail-close running records orphaned by a controller restart.

        A running record with no in-process execution and an absent or stale
        lease was interrupted. It becomes a typed ``operation_interrupted``
        failure; the underlying action is never silently re-executed. Returns
        metadata-only summaries for audit.
        """
        grace = (
            self._orphan_grace()
            if grace_seconds is None
            else max(0.0, float(grace_seconds))
        )
        now = self._clock()
        recovered: list[dict[str, Any]] = []
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT idempotency_key, request_id, created_at FROM operation_records"
                " WHERE status = 'running'"
            ).fetchall()
            for row in rows:
                key = row["idempotency_key"]
                if self._fail_orphaned(connection, key, now, grace) is not None:
                    recovered.append(
                        {
                            "key": key,
                            "request_id": row["request_id"],
                            "created_at": row["created_at"],
                        }
                    )
            connection.commit()
        return recovered

    def lookup(self, key: str) -> Optional[dict[str, Any]]:
        now = self._clock()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_records(connection, now)
            row = connection.execute(
                "SELECT * FROM operation_records WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is not None:
                record = self._record(row)
            elif self._is_tombstoned(connection, key):
                record = {"key": key, "status": "expired"}
            else:
                record = None
            connection.commit()
        return record

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_records (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    response TEXT,
                    result TEXT,
                    error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_leases (
                    idempotency_key TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_tombstones (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    key_bits BLOB NOT NULL,
                    fingerprint_bits BLOB NOT NULL,
                    generation_started_at REAL NOT NULL DEFAULT 0,
                    previous_key_bits BLOB NOT NULL DEFAULT X'',
                    previous_fingerprint_bits BLOB NOT NULL DEFAULT X''
                )
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(operation_tombstones)")
            }
            for name, declaration in (
                ("generation_started_at", "REAL NOT NULL DEFAULT 0"),
                ("previous_key_bits", "BLOB NOT NULL DEFAULT X''"),
                ("previous_fingerprint_bits", "BLOB NOT NULL DEFAULT X''"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE operation_tombstones ADD COLUMN {name} {declaration}"
                    )
            connection.execute(
                """
                INSERT OR IGNORE INTO operation_tombstones (
                    singleton, key_bits, fingerprint_bits,
                    generation_started_at, previous_key_bits, previous_fingerprint_bits
                ) VALUES (1, ?, ?, 0, ?, ?)
                """,
                (bytes(self._tombstone_bytes),) * 4,
            )
            self._normalize_tombstones(connection)
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        record = {
            "key": row["idempotency_key"],
            "request_id": row["request_id"],
            "fingerprint": row["fingerprint"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
        }
        for name in ("response", "result", "error"):
            value = row[name]
            if value is not None:
                try:
                    record[name] = _strict_json_loads(value)
                except (TypeError, ValueError):
                    record[name] = {"truncated": True}
        return record

    def _expire_records(self, connection: sqlite3.Connection, now: float) -> None:
        self._rotate_tombstones(connection, now)
        connection.execute(
            "DELETE FROM operation_leases WHERE updated_at <= ?",
            (now - self.retention_seconds,),
        )
        rows = connection.execute(
            """
            SELECT idempotency_key, fingerprint
            FROM operation_records
            WHERE expires_at <= ?
              AND idempotency_key NOT IN (SELECT idempotency_key FROM operation_leases)
            """,
            (now,),
        ).fetchall()
        rows = [row for row in rows if row["idempotency_key"] not in self._active_keys]
        if not rows:
            return
        tombstones = connection.execute(
            "SELECT key_bits, fingerprint_bits FROM operation_tombstones WHERE singleton = 1"
        ).fetchone()
        key_bits = bytearray(tombstones["key_bits"])
        fingerprint_bits = bytearray(tombstones["fingerprint_bits"])
        for row in rows:
            self._bloom_add(key_bits, row["idempotency_key"])
            self._bloom_add(
                fingerprint_bits,
                self._tombstone_fingerprint(row["idempotency_key"], row["fingerprint"]),
            )
        connection.execute(
            """
            UPDATE operation_tombstones
            SET key_bits = ?, fingerprint_bits = ?
            WHERE singleton = 1
            """,
            (bytes(key_bits), bytes(fingerprint_bits)),
        )
        connection.executemany(
            "DELETE FROM operation_records WHERE idempotency_key = ?",
            ((row["idempotency_key"],) for row in rows),
        )

    def _write_lease(self, key: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO operation_leases (idempotency_key, owner, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    owner = excluded.owner,
                    updated_at = excluded.updated_at
                """,
                (key, self._lease_owner, self._clock()),
            )
            connection.commit()

    def _heartbeat_lease(self, key: str, stop: threading.Event) -> None:
        interval = max(0.05, min(5.0, self.retention_seconds / 3.0))
        while not stop.wait(interval):
            try:
                self._write_lease(key)
            except Exception:
                continue

    def _delete_lease(self, key: str) -> None:
        try:
            with self._lock, self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM operation_leases WHERE idempotency_key = ? AND owner = ?",
                    (key, self._lease_owner),
                )
                connection.commit()
        except Exception:
            pass

    def _is_tombstoned(
        self,
        connection: sqlite3.Connection,
        key: str,
        fingerprint: Optional[str] = None,
    ) -> bool:
        self._rotate_tombstones(connection, self._clock())
        row = connection.execute(
            """
            SELECT key_bits, fingerprint_bits, previous_key_bits,
                   previous_fingerprint_bits
            FROM operation_tombstones WHERE singleton = 1
            """
        ).fetchone()
        if fingerprint is None:
            return any(
                self._bloom_contains(row[name], key) for name in ("key_bits", "previous_key_bits")
            )
        value = self._tombstone_fingerprint(key, fingerprint)
        return any(
            self._bloom_contains(row[name], value)
            for name in ("fingerprint_bits", "previous_fingerprint_bits")
        )

    def _normalize_tombstones(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT * FROM operation_tombstones WHERE singleton = 1"
        ).fetchone()
        empty = bytes(self._tombstone_bytes)
        saturated = bytes([0xFF]) * self._tombstone_bytes
        values = []
        changed = False
        for name in (
            "key_bits",
            "fingerprint_bits",
            "previous_key_bits",
            "previous_fingerprint_bits",
        ):
            value = bytes(row[name])
            if len(value) != self._tombstone_bytes:
                value = empty if name.startswith("previous_") and not value else saturated
                changed = True
            values.append(value)
        if changed:
            connection.execute(
                """
                UPDATE operation_tombstones
                SET key_bits = ?, fingerprint_bits = ?, previous_key_bits = ?,
                    previous_fingerprint_bits = ?
                WHERE singleton = 1
                """,
                values,
            )

    def _rotate_tombstones(self, connection: sqlite3.Connection, now: float) -> None:
        row = connection.execute(
            "SELECT * FROM operation_tombstones WHERE singleton = 1"
        ).fetchone()
        started_at = float(row["generation_started_at"])
        if started_at <= 0:
            connection.execute(
                "UPDATE operation_tombstones SET generation_started_at = ? WHERE singleton = 1",
                (now,),
            )
            return
        elapsed = now - started_at
        if elapsed < self.retention_seconds:
            return
        empty = bytes(self._tombstone_bytes)
        generations = int(elapsed // self.retention_seconds)
        if generations == 1:
            previous_key_bits = row["key_bits"]
            previous_fingerprint_bits = row["fingerprint_bits"]
        else:
            previous_key_bits = empty
            previous_fingerprint_bits = empty
        connection.execute(
            """
            UPDATE operation_tombstones
            SET key_bits = ?, fingerprint_bits = ?, generation_started_at = ?,
                previous_key_bits = ?, previous_fingerprint_bits = ?
            WHERE singleton = 1
            """,
            (
                empty,
                empty,
                started_at + generations * self.retention_seconds,
                previous_key_bits,
                previous_fingerprint_bits,
            ),
        )

    @staticmethod
    def _tombstone_fingerprint(key: str, fingerprint: str) -> str:
        return key + "\x00" + fingerprint

    @staticmethod
    def _bloom_positions(value: str, bit_count: int) -> Iterator[int]:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        for index in range(_TOMBSTONE_HASH_COUNT):
            start = index * 4
            yield int.from_bytes(digest[start : start + 4], "big") % bit_count

    @classmethod
    def _bloom_add(cls, bits: bytearray, value: str) -> None:
        for position in cls._bloom_positions(value, len(bits) * 8):
            bits[position // 8] |= 1 << (position % 8)

    @classmethod
    def _bloom_contains(cls, bits: bytes, value: str) -> bool:
        return all(
            bits[position // 8] & (1 << (position % 8))
            for position in cls._bloom_positions(value, len(bits) * 8)
        )
