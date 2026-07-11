"""HTTP controller transport for the anvil-serving MCP control plane.

The controller is a management-plane HTTP wrapper around ``anvil_serving.mcp``.
It deliberately reuses ``mcp.list_tools()`` and ``mcp.call_tool()`` so stdio MCP
and HTTP controller callers see the same tool schemas and tool semantics.

This module is stdlib-only and safe to test without a long-running listener:
``make_server()`` returns an unstarted server, and both the server class and MCP
tool functions are injectable.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence

from . import mcp


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_AUTH_TOKEN_ENV = "ANVIL_CONTROLLER_TOKEN"
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_READ_TIMEOUT_SECONDS = 30.0
DEFAULT_STATUS_URL = "http://127.0.0.1:8765"
DEFAULT_STATUS_MAX_RESPONSE_BYTES = 64 * 1024

_MAX_BODY_BYTES = int(
    os.environ.get("ANVIL_CONTROLLER_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES))
)
_READ_TIMEOUT_SECONDS = float(
    os.environ.get("ANVIL_CONTROLLER_READ_TIMEOUT_SECONDS", str(DEFAULT_READ_TIMEOUT_SECONDS))
)
_TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
_RFC1918_V4 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
_ULA_V6 = ipaddress.ip_network("fc00::/7")
_DOCUMENTATION_V4 = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
_WILDCARD_BINDS = {"", "0", "0.0.0.0", "::"}
_TOKEN_HEADER = "x-api-key"
_REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
_IDEMPOTENCY_KEY_HEADER = "X-Anvil-Idempotency-Key"
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_CONTEXT_FIELDS = ("topology", "execution_host", "execution_runtime")
_SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[^\s'\"\\]+"),
    re.compile(
        r"(?i)\b((?:access[_-]?key|api[_-]?key|authorization|client[_-]?secret|"
        r"private[_-]?key|secret[_-]?access[_-]?key|session[_-]?token|x-api-key)"
        r"\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"
    ),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{8,}\b"),
)
_TOMBSTONE_BYTES_PER_RECORD = 16
_TOMBSTONE_MIN_BYTES = 128
_TOMBSTONE_HASH_COUNT = 7
DEFAULT_IDEMPOTENCY_RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_IDEMPOTENCY_MAX_RECORDS = 1024
DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES = 64 * 1024
DEFAULT_IDEMPOTENCY_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".anvil-serving", "controller-operations.sqlite3"
)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
AuditLogger = Callable[[dict[str, Any]], None]
ListToolsFunc = Callable[[], list[dict]]
CallToolFunc = Callable[[str, Optional[dict]], dict]


class OperationStore:
    """Durable, bounded controller mutation records keyed by idempotency key."""

    def __init__(
        self,
        path: str = DEFAULT_IDEMPOTENCY_DB_PATH,
        *,
        retention_seconds: float = DEFAULT_IDEMPOTENCY_RETENTION_SECONDS,
        max_records: int = DEFAULT_IDEMPOTENCY_MAX_RECORDS,
        max_result_bytes: int = DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES,
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
        now = time.time()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_records(connection, now)
            row = connection.execute(
                "SELECT * FROM operation_records WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is not None:
                record = self._record(row)
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
        now = time.time()
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

    def lookup(self, key: str) -> Optional[dict[str, Any]]:
        now = time.time()
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
                (key, self._lease_owner, time.time()),
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
        self._rotate_tombstones(connection, time.time())
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


class ControllerError(Exception):
    """Structured controller failure rendered as JSON."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int = 400,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


class BindSafetyError(ControllerError):
    """Raised when a requested bind address violates controller safety rules."""


@dataclass(frozen=True)
class BindAssessment:
    """Result of classifying a controller bind address."""

    host: str
    addresses: tuple[str, ...]
    loopback: bool
    private: bool
    tailscale: bool
    public: bool
    requires_auth: bool


class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _strict_json_loads(value: str) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError("non-finite JSON number: " + constant)

    return json.loads(value, parse_constant=reject_constant)


def _redact_secret(value: Any, secret: Optional[str]) -> Any:
    if isinstance(value, str):
        if secret:
            value = value.replace(secret, "<redacted>")
        for pattern in _SECRET_TEXT_PATTERNS:
            value = pattern.sub(
                lambda match: match.group(1) + "<redacted>" if match.lastindex else "<redacted>",
                value,
            )
        return value
    if isinstance(value, list):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, tuple):
        return [_redact_secret(item, secret) for item in value]
    if isinstance(value, dict):
        return {
            str(_redact_secret(str(key), secret)): _redact_secret(item, secret)
            for key, item in value.items()
        }
    return value


def _sanitize_persisted_value(value: Any, secret: Optional[str]) -> Any:
    if isinstance(value, str):
        return _redact_secret(value, secret)
    if isinstance(value, list):
        return [_sanitize_persisted_value(item, secret) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_persisted_value(item, secret) for item in value]
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(_redact_secret(str(key), secret))
            rendered[key_text] = (
                "<redacted>"
                if _is_sensitive_key(str(key))
                else _sanitize_persisted_value(item, secret)
            )
        return rendered
    return value


def _is_sensitive_key(key: str) -> bool:
    parts = tuple(part for part in re.split(r"[^a-z0-9]+", key.lower()) if part)
    compact = "".join(parts)
    if set(parts) & {"authorization", "credential", "credentials", "password", "secret", "token"}:
        return True
    return any(
        shape in compact
        for shape in (
            "accesskey",
            "accesstoken",
            "apikey",
            "authorization",
            "bearertoken",
            "clientsecret",
            "privatekey",
            "refreshtoken",
            "secretaccesskey",
            "sessiontoken",
        )
    )


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


def _idempotency_key(headers) -> Optional[str]:
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


def _default_audit_logger(record: dict[str, Any]) -> None:
    sys.stderr.write(_json_dumps(record) + "\n")
    sys.stderr.flush()


def _is_tailscale_ip(addr: IPAddress) -> bool:
    if addr.version == 4:
        return addr in _TAILSCALE_V4
    return addr in _TAILSCALE_V6


def _is_safe_private_ip(addr: IPAddress) -> bool:
    if addr.is_unspecified or addr.is_link_local or addr.is_multicast or addr.is_reserved:
        return False
    if addr.version == 4:
        return bool(
            addr.is_loopback
            or _is_tailscale_ip(addr)
            or any(addr in network for network in _RFC1918_V4)
        )
    return bool(addr.is_loopback or addr in _ULA_V6 or _is_tailscale_ip(addr))


def _is_forbidden_bind_ip(addr: IPAddress) -> bool:
    if addr.is_unspecified or addr.is_link_local or addr.is_multicast or addr.is_reserved:
        return True
    if addr.version == 4 and any(addr in network for network in _DOCUMENTATION_V4):
        return True
    return False


def _resolve_bind_ips(
    host: str,
    *,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
) -> tuple[IPAddress, ...]:
    if host in _WILDCARD_BINDS:
        return ()
    if host.strip().lower() == "localhost":
        raise BindSafetyError(
            "localhost_not_allowed",
            "use 127.0.0.1 or ::1 instead of localhost",
            status=400,
            details={"host": host},
        )
    try:
        return (ipaddress.ip_address(host),)
    except ValueError:
        pass

    getaddrinfo = resolver or socket.getaddrinfo
    try:
        infos = getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise BindSafetyError(
            "bind_host_unresolved",
            "could not resolve bind host",
            status=400,
            details={"host": host, "error": str(exc)},
        ) from exc

    addrs: list[IPAddress] = []
    for info in infos:
        try:
            raw_addr = info[4][0]
            addrs.append(ipaddress.ip_address(raw_addr))
        except (IndexError, TypeError, ValueError):
            continue
    if not addrs:
        raise BindSafetyError(
            "bind_host_unresolved",
            "could not resolve bind host to an IP address",
            status=400,
            details={"host": host},
        )
    # Preserve resolver order while removing duplicates.
    seen: set[str] = set()
    unique: list[IPAddress] = []
    for addr in addrs:
        rendered = str(addr)
        if rendered not in seen:
            unique.append(addr)
            seen.add(rendered)
    return tuple(unique)


def _env_has_token(auth_token_env: Optional[str], env: Mapping[str, str]) -> bool:
    if not auth_token_env:
        return False
    return bool(env.get(auth_token_env))


def validate_bind_safety(
    host: str,
    *,
    allow_public_bind: bool = False,
    allow_unauthenticated_loopback: bool = False,
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    env: Optional[Mapping[str, str]] = None,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
) -> BindAssessment:
    """Validate controller bind safety and return the bind classification.

    Allowed without ``allow_public_bind``:
    - loopback IPs: ``127.0.0.1`` and ``::1``
    - private addresses such as RFC1918 IPv4 or IPv6 ULA
    - Tailscale IPv4 CGNAT addresses in ``100.64.0.0/10``

    Public and wildcard binds are refused unless ``allow_public_bind`` is true.
    All binds require an auth token by default. Loopback can opt out only with
    ``allow_unauthenticated_loopback`` for explicit local development tests.
    """
    effective_env = os.environ if env is None else env
    addrs = _resolve_bind_ips(host, resolver=resolver)

    wildcard_resolved = bool(addrs and any(addr.is_unspecified for addr in addrs))
    if host in _WILDCARD_BINDS or wildcard_resolved:
        loopback = False
        private = False
        tailscale = False
        public = True
        addresses: tuple[str, ...] = (
            (host,) if host in _WILDCARD_BINDS else tuple(str(addr) for addr in addrs)
        )
    else:
        loopback = all(addr.is_loopback for addr in addrs)
        private = all(addr.is_private for addr in addrs)
        tailscale = any(_is_tailscale_ip(addr) for addr in addrs)
        public = any(not _is_safe_private_ip(addr) for addr in addrs)
        addresses = tuple(str(addr) for addr in addrs)

    if not (host in _WILDCARD_BINDS or wildcard_resolved) and any(
        _is_forbidden_bind_ip(addr) for addr in addrs
    ):
        raise BindSafetyError(
            "unsafe_bind_address",
            "refusing to bind controller to a link-local, reserved, multicast, or documentation address",
            status=400,
            details={"host": host, "addresses": [str(addr) for addr in addrs]},
        )

    if public and not allow_public_bind:
        raise BindSafetyError(
            "public_bind_refused",
            "refusing to bind controller to a public address without --allow-public-bind",
            status=400,
            details={"host": host, "addresses": list(addresses)},
        )

    requires_auth = not (loopback and allow_unauthenticated_loopback)
    if requires_auth and not _env_has_token(auth_token_env, effective_env):
        raise BindSafetyError(
            "auth_token_required",
            "controller binds require an auth token environment variable",
            status=400,
            details={
                "host": host,
                "auth_token_env": auth_token_env or None,
                "addresses": list(addresses),
            },
        )

    return BindAssessment(
        host=host,
        addresses=addresses,
        loopback=loopback,
        private=private,
        tailscale=tailscale,
        public=public,
        requires_auth=requires_auth,
    )


def resolve_auth_token(
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    *,
    env: Optional[Mapping[str, str]] = None,
    required: bool = False,
) -> Optional[str]:
    effective_env = os.environ if env is None else env
    if not auth_token_env:
        if required:
            raise ControllerError(
                "auth_token_required",
                "auth token environment variable name is required",
                status=400,
            )
        return None
    token = effective_env.get(auth_token_env)
    if token:
        return token
    if required:
        raise ControllerError(
            "auth_token_missing",
            "auth token environment variable is not set",
            status=400,
            details={"auth_token_env": auth_token_env},
        )
    return None


def _extract_request_token(headers) -> Optional[str]:
    auth_header = headers.get("Authorization")
    if auth_header:
        scheme, _, value = auth_header.partition(" ")
        if scheme.strip().lower() == "bearer" and value.strip():
            return value.strip()
        return None
    api_key = headers.get(_TOKEN_HEADER)
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def _safe_request_id(value: Optional[str]) -> str:
    if value and _REQUEST_ID_RE.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _content_type_is_json(value: Optional[str]) -> bool:
    if value is None:
        return False
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _mcp_tool_name(name: str) -> str:
    """Translate declared topology operation names to MCP catalog names."""
    return name.replace("-", "_")


def _validated_tool_catalog(
    list_tools_func: ListToolsFunc,
    allowed_operations: Optional[Sequence[str]] = None,
) -> tuple[list[dict], dict[str, str]]:
    """Snapshot a tool catalog and reject ambiguous normalized names."""
    tools = list_tools_func()
    normalized: dict[str, str] = {}
    for tool in tools:
        name = tool.get("name") if isinstance(tool, dict) else None
        if not isinstance(name, str):
            continue
        catalog_name = _mcp_tool_name(name)
        existing = normalized.setdefault(catalog_name, name)
        if existing != name:
            raise ControllerError(
                "ambiguous_tool_catalog",
                "controller tool catalog contains hyphen/underscore normalization collisions",
                status=500,
                details={"tools": sorted((existing, name))},
            )
    if allowed_operations is not None:
        allowed = {_mcp_tool_name(name) for name in allowed_operations}
        unknown = sorted(allowed - set(normalized))
        if unknown:
            raise ControllerError(
                "unknown_allowed_operation",
                "controller allowlist contains operations absent from the tool catalog",
                status=400,
                details={"operations": unknown},
            )
        tools = [
            tool
            for tool in tools
            if isinstance(tool, dict)
            and isinstance(tool.get("name"), str)
            and _mcp_tool_name(tool["name"]) in allowed
        ]
        normalized = {
            key: value for key, value in normalized.items() if key in allowed
        }
    return tools, normalized


def _error_body(
    code: str,
    message: str,
    *,
    request_id: str,
    details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "request_id": request_id,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def _response_with_request_id(
    envelope: dict, request_id: str, auth_token: Optional[str] = None
) -> dict:
    if "request_id" in envelope:
        return _sanitize_persisted_value(dict(envelope), auth_token)
    response = dict(envelope)
    response["request_id"] = request_id
    return _sanitize_persisted_value(response, auth_token)


def _tool_result(envelope: dict) -> dict:
    return {
        "content": [{"type": "text", "text": _json_dumps(envelope)}],
        "structuredContent": envelope,
        "isError": not envelope.get("ok", False),
    }


def _server_class_for_host(host: str):
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return ThreadingHTTPServer
    if addr.version == 6:
        return IPv6ThreadingHTTPServer
    return ThreadingHTTPServer


def make_handler(
    *,
    list_tools_func: ListToolsFunc = mcp.list_tools,
    call_tool_func: CallToolFunc = mcp.call_tool,
    auth_token: Optional[str] = None,
    audit_logger: Optional[AuditLogger] = None,
    max_body_bytes: int = _MAX_BODY_BYTES,
    read_timeout_seconds: float = _READ_TIMEOUT_SECONDS,
    operation_store: Optional[OperationStore] = None,
    allowed_operations: Optional[Sequence[str]] = None,
):
    """Build a request handler class for controller tests or ``make_server``."""

    audit = audit_logger or _default_audit_logger
    allowlist_enabled = allowed_operations is not None
    declared_tools, declared_name_by_normalized = _validated_tool_catalog(
        list_tools_func, allowed_operations
    )
    store = operation_store or OperationStore()

    class ControllerHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "anvil-controller"
        sys_version = ""

        def setup(self) -> None:
            super().setup()
            if read_timeout_seconds > 0:
                self.connection.settimeout(read_timeout_seconds)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _remote_addr(self) -> str:
            try:
                return str(self.client_address[0])
            except Exception:
                return ""

        def _authenticated(self) -> bool:
            if auth_token is None:
                return True
            supplied = _extract_request_token(self.headers)
            if supplied is None:
                return False
            return hmac.compare_digest(supplied.encode("utf-8"), auth_token.encode("utf-8"))

        def _send_json(
            self,
            status: int,
            obj: dict[str, Any],
            *,
            request_id: str,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            payload = _json_dumps(_redact_secret(obj, auth_token)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header(_REQUEST_ID_HEADER, request_id)
            self.send_header("Cache-Control", "no-store")
            if self.close_connection:
                self.send_header("Connection", "close")
            if extra_headers:
                for name, value in extra_headers.items():
                    self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _send_no_content(self, *, request_id: str) -> None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.send_header(_REQUEST_ID_HEADER, request_id)
            self.send_header("Cache-Control", "no-store")
            if self.close_connection:
                self.send_header("Connection", "close")
            self.end_headers()

        def _send_error_json(
            self,
            status: int,
            code: str,
            message: str,
            *,
            request_id: str,
            details: Optional[dict[str, Any]] = None,
            extra_headers: Optional[dict[str, str]] = None,
        ) -> None:
            self._send_json(
                status,
                _error_body(code, message, request_id=request_id, details=details),
                request_id=request_id,
                extra_headers=extra_headers,
            )

        def _audit(
            self,
            *,
            request_id: str,
            operation: str,
            status: int,
            started: float,
            ok: bool,
            tool: Optional[str] = None,
            dry_run: Optional[bool] = None,
            confirm: Optional[bool] = None,
            error_code: Optional[str] = None,
        ) -> None:
            record: dict[str, Any] = {
                "request_id": request_id,
                "operation": operation,
                "tool": tool,
                "dry_run": dry_run,
                "confirm": confirm,
                "status": status,
                "ok": ok,
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "remote_addr": self._remote_addr(),
            }
            if error_code is not None:
                record["error_code"] = error_code
            try:
                audit(record)
            except Exception:
                pass

        def _read_json_body(self, *, request_id: str) -> dict[str, Any]:
            if self.headers.get_all("Transfer-Encoding"):
                self.close_connection = True
                raise ControllerError(
                    "chunked_not_supported",
                    "chunked request bodies are not supported",
                    status=411,
                )
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                return {}
            if len(self.headers.get_all("Content-Length") or []) != 1:
                self.close_connection = True
                raise ControllerError(
                    "bad_content_length",
                    "exactly one Content-Length header is required",
                    status=400,
                )
            if not _content_type_is_json(self.headers.get("Content-Type")):
                self.close_connection = True
                raise ControllerError(
                    "unsupported_media_type",
                    "POST request bodies must use Content-Type: application/json",
                    status=415,
                )
            if not raw_length.isdigit():
                self.close_connection = True
                raise ControllerError(
                    "bad_content_length",
                    "Content-Length must be a non-negative integer",
                    status=400,
                )
            length = int(raw_length)
            if length > max_body_bytes:
                self.close_connection = True
                raise ControllerError(
                    "payload_too_large",
                    "request body is too large",
                    status=413,
                    details={"max_body_bytes": max_body_bytes},
                )
            if length == 0:
                return {}
            chunks: list[bytes] = []
            remaining = length
            deadline = (
                time.perf_counter() + read_timeout_seconds if read_timeout_seconds > 0 else None
            )
            try:
                while remaining > 0:
                    if deadline is not None:
                        seconds_left = deadline - time.perf_counter()
                        if seconds_left <= 0:
                            self.close_connection = True
                            raise ControllerError(
                                "request_timeout",
                                "request body read timed out",
                                status=408,
                                details={"read_timeout_seconds": read_timeout_seconds},
                            )
                        self.connection.settimeout(seconds_left)
                    reader = self.rfile.read1 if hasattr(self.rfile, "read1") else self.rfile.read
                    chunk = reader(min(remaining, 65536))
                    if not chunk:
                        self.close_connection = True
                        raise ControllerError(
                            "incomplete_body",
                            "request body ended before Content-Length bytes were received",
                            status=400,
                            details={
                                "expected_body_bytes": length,
                                "received_body_bytes": length - remaining,
                            },
                        )
                    chunks.append(chunk)
                    remaining -= len(chunk)
            except socket.timeout as exc:
                self.close_connection = True
                raise ControllerError(
                    "request_timeout",
                    "request body read timed out",
                    status=408,
                    details={"read_timeout_seconds": read_timeout_seconds},
                ) from exc
            finally:
                if read_timeout_seconds > 0:
                    self.connection.settimeout(read_timeout_seconds)
            raw = b"".join(chunks)
            try:
                obj = _strict_json_loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ControllerError(
                    "invalid_json",
                    "request body must be valid UTF-8 JSON",
                    status=400,
                    details={"error": str(exc)},
                ) from exc
            if not isinstance(obj, dict):
                raise ControllerError(
                    "bad_request",
                    "request body must be a JSON object",
                    status=400,
                )
            return obj

        def _dispatch_tool(
            self,
            tool_name: str,
            arguments: dict[str, Any],
            *,
            request_id: str,
            idempotency_key: Optional[str],
            idempotency_context: Any = None,
        ) -> tuple[dict[str, Any], int]:
            if (
                arguments.get("confirm") is True
                and arguments.get("dry_run") is not True
                and idempotency_key is None
            ):
                raise ControllerError(
                    "idempotency_key_required",
                    "confirmed mutation operations require an idempotency key",
                    status=409,
                )
            if idempotency_key is None:
                return _response_with_request_id(
                    call_tool_func(tool_name, arguments), request_id, auth_token
                ), 200

            context = _idempotency_context(idempotency_context)
            disposition, record = store.claim(
                idempotency_key,
                _operation_fingerprint(tool_name, arguments, context),
                request_id,
            )
            if disposition == "conflict":
                raise ControllerError(
                    "idempotency_key_conflict",
                    "idempotency key was already used for a different operation",
                    status=409,
                    details={"key": idempotency_key},
                )
            if disposition == "full":
                raise ControllerError(
                    "idempotency_store_full",
                    "operation status store is at capacity",
                    status=503,
                )
            if disposition == "expired":
                raise ControllerError(
                    "idempotency_key_expired",
                    "idempotency key is expired and cannot be reused",
                    status=409,
                    details={"key": idempotency_key},
                )
            if disposition == "existing":
                assert record is not None
                if record["status"] == "running":
                    return (
                        _error_body(
                            "operation_running",
                            "operation with this idempotency key is still running",
                            request_id=request_id,
                            details={"key": idempotency_key},
                        ),
                        202,
                    )
                response = record.get("response")
                if isinstance(response, dict):
                    return response, 200
                raise ControllerError(
                    "idempotency_record_unavailable",
                    "operation record is not available for replay",
                    status=503,
                )

            with store.executing(idempotency_key):
                try:
                    envelope = _response_with_request_id(
                        call_tool_func(tool_name, arguments), request_id, auth_token
                    )
                    if not isinstance(envelope, dict):
                        raise TypeError("MCP tool result must be an object")
                except Exception:
                    failure = _error_body(
                        "internal_error",
                        "internal error",
                        request_id=request_id,
                    )
                    store.complete(idempotency_key, "failed", failure, auth_token)
                    raise
                store.complete(
                    idempotency_key,
                    "succeeded" if envelope.get("ok") else "failed",
                    envelope,
                    auth_token,
                )
            return envelope, 200

        def _jsonrpc_response(
            self,
            body: dict[str, Any],
            *,
            request_id: str,
            idempotency_key: Optional[str],
        ) -> Optional[dict[str, Any]]:
            if "id" not in body:
                return None
            req_id = body.get("id")
            if req_id is None:
                return {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "id must not be null"},
                }
            method = body.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": mcp.PROTOCOL_VERSION,
                    "serverInfo": mcp.SERVER_INFO,
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {"tools": declared_tools}
            elif method == "tools/call":
                params = body.get("params", {})
                if params is None:
                    params = {}
                if not isinstance(params, dict):
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": "params must be an object",
                        },
                    }
                raw_tool_name = params.get("name")
                normalized_name = (
                    _mcp_tool_name(raw_tool_name) if isinstance(raw_tool_name, str) else None
                )
                tool_name = (
                    declared_name_by_normalized.get(normalized_name)
                    if normalized_name is not None
                    else None
                )
                if tool_name is None:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": "unknown tool %r" % normalized_name,
                            "data": {"code": "unknown_tool"},
                        },
                    }
                arguments = params.get("arguments", {})
                if arguments is None:
                    arguments = {}
                if not isinstance(arguments, dict):
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32602,
                            "message": "tool arguments must be an object",
                            "data": {"code": "bad_arguments"},
                        },
                    }
                try:
                    envelope, _ = self._dispatch_tool(
                        tool_name,
                        arguments,
                        request_id=request_id,
                        idempotency_key=idempotency_key,
                        idempotency_context=params.get("context"),
                    )
                except ControllerError as exc:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32009,
                            "message": exc.message,
                            "data": {"code": exc.code, "details": exc.details},
                        },
                    }
                result = _tool_result(envelope)
            elif method == "notifications/initialized":
                return None
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            if req_id is None:
                return None
            return {"jsonrpc": "2.0", "id": req_id, "result": result}

        def _auth_or_401(self, *, request_id: str) -> bool:
            if self._authenticated():
                return True
            self.close_connection = True
            self._send_error_json(
                401,
                "authentication_error",
                "invalid or missing API key",
                request_id=request_id,
            )
            return False

        def do_GET(self) -> None:
            request_id = _safe_request_id(self.headers.get(_REQUEST_ID_HEADER))
            started = time.perf_counter()
            route = self.path.split("?", 1)[0].rstrip("/") or "/"
            operation = route.lstrip("/") or "root"
            status = 500
            ok = False
            error_code: Optional[str] = None
            try:
                if not self._auth_or_401(request_id=request_id):
                    status = 401
                    error_code = "authentication_error"
                    return
                if route in ("/health", "/healthz"):
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        {
                            "status": "ok",
                            "service": "anvil-serving-controller",
                            "request_id": request_id,
                        },
                        request_id=request_id,
                    )
                    return
                if route == "/tools/list":
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        {"tools": declared_tools, "request_id": request_id},
                        request_id=request_id,
                    )
                    return
                if route.startswith("/operations/"):
                    key = _operation_status_key(route[len("/operations/") :])
                    record = store.lookup(key)
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        (
                            record
                            if record is not None
                            else {"key": key, "status": "unknown", "request_id": request_id}
                        ),
                        request_id=request_id,
                    )
                    return
                if route == "/tools/call":
                    status = 405
                    error_code = "method_not_allowed"
                    self._send_error_json(
                        status,
                        error_code,
                        "this route only accepts POST requests",
                        request_id=request_id,
                        extra_headers={"Allow": "POST"},
                    )
                    return
                status = 404
                error_code = "not_found"
                self._send_error_json(
                    status,
                    error_code,
                    "unknown controller route",
                    request_id=request_id,
                    details={"path": route},
                )
            except ControllerError as exc:
                status = exc.status
                ok = False
                error_code = exc.code
                self._send_error_json(
                    status,
                    exc.code,
                    exc.message,
                    request_id=request_id,
                    details=exc.details,
                )
            except Exception:
                status = 500
                ok = False
                error_code = "internal_error"
                self._send_error_json(
                    status,
                    error_code,
                    "internal error",
                    request_id=request_id,
                )
            finally:
                self._audit(
                    request_id=request_id,
                    operation=operation,
                    status=status,
                    started=started,
                    ok=ok,
                    error_code=error_code,
                )

        def do_POST(self) -> None:
            request_id = _safe_request_id(self.headers.get(_REQUEST_ID_HEADER))
            started = time.perf_counter()
            route = self.path.split("?", 1)[0].rstrip("/") or "/"
            operation = route.lstrip("/") or "root"
            status = 500
            ok = False
            tool: Optional[str] = None
            dry_run: Optional[bool] = None
            confirm: Optional[bool] = None
            error_code: Optional[str] = None
            try:
                if not self._auth_or_401(request_id=request_id):
                    status = 401
                    error_code = "authentication_error"
                    return
                if route == "/tools/list":
                    self._read_json_body(request_id=request_id)
                    status = 200
                    ok = True
                    self._send_json(
                        status,
                        {"tools": declared_tools, "request_id": request_id},
                        request_id=request_id,
                    )
                    return
                if route in ("/", "/mcp"):
                    body = self._read_json_body(request_id=request_id)
                    if "id" in body and body.get("method") == "tools/call":
                        params = body.get("params", {})
                        if params is None:
                            params = {}
                        if isinstance(params, dict):
                            raw_arguments = params.get("arguments", {})
                            if raw_arguments is None:
                                raw_arguments = {}
                            if isinstance(raw_arguments, dict):
                                tool = (
                                    params.get("name")
                                    if isinstance(params.get("name"), str)
                                    else None
                                )
                                if isinstance(raw_arguments.get("dry_run"), bool):
                                    dry_run = raw_arguments["dry_run"]
                                if isinstance(raw_arguments.get("confirm"), bool):
                                    confirm = raw_arguments["confirm"]
                    idempotency_key = (
                        _idempotency_key(self.headers)
                        if body.get("method") == "tools/call"
                        else None
                    )
                    response = self._jsonrpc_response(
                        body,
                        request_id=request_id,
                        idempotency_key=idempotency_key,
                    )
                    status = 200
                    ok = response is None
                    if response is not None:
                        if "error" in response:
                            ok = False
                            error = response.get("error")
                            data = error.get("data") if isinstance(error, dict) else None
                            if isinstance(data, dict) and isinstance(data.get("code"), str):
                                error_code = data["code"]
                            elif isinstance(error, dict) and isinstance(error.get("message"), str):
                                error_code = error["message"]
                        else:
                            ok = True
                            result = response.get("result")
                            structured = (
                                result.get("structuredContent")
                                if isinstance(result, dict)
                                else None
                            )
                            if isinstance(structured, dict) and structured.get("ok") is False:
                                ok = False
                                err = structured.get("error")
                                if isinstance(err, dict) and isinstance(err.get("code"), str):
                                    error_code = err["code"]
                    if response is not None:
                        self._send_json(status, response, request_id=request_id)
                    else:
                        status = 204
                        self._send_no_content(request_id=request_id)
                    return

                if route != "/tools/call":
                    status = 405 if route in ("/health", "/healthz") else 404
                    error_code = (
                        "method_not_allowed" if route in ("/health", "/healthz") else "not_found"
                    )
                    self._send_error_json(
                        status,
                        error_code,
                        (
                            "this route only accepts GET requests"
                            if route in ("/health", "/healthz")
                            else "unknown controller route"
                        ),
                        request_id=request_id,
                        details={} if route in ("/health", "/healthz") else {"path": route},
                        extra_headers={"Allow": "GET"}
                        if route in ("/health", "/healthz")
                        else None,
                    )
                    return

                body = self._read_json_body(request_id=request_id)
                raw_name = body.get("name")
                if not isinstance(raw_name, str) or not raw_name:
                    raise ControllerError(
                        "bad_request",
                        "tools/call requires a non-empty string 'name'",
                        status=400,
                    )
                raw_arguments = body.get("arguments", {})
                if raw_arguments is None:
                    raw_arguments = {}
                if not isinstance(raw_arguments, dict):
                    raise ControllerError(
                        "bad_request",
                        "tools/call 'arguments' must be a JSON object",
                        status=400,
                    )

                normalized_name = _mcp_tool_name(raw_name)
                tool = declared_name_by_normalized.get(normalized_name)
                if tool is None and allowlist_enabled:
                    raise ControllerError(
                        "unknown_tool",
                        "unknown tool %r" % normalized_name,
                        status=400,
                    )
                if tool is None:
                    tool = normalized_name
                if isinstance(raw_arguments.get("dry_run"), bool):
                    dry_run = raw_arguments["dry_run"]
                if isinstance(raw_arguments.get("confirm"), bool):
                    confirm = raw_arguments["confirm"]

                envelope, status = self._dispatch_tool(
                    tool,
                    raw_arguments,
                    request_id=request_id,
                    idempotency_key=_idempotency_key(self.headers),
                    idempotency_context=body.get("context"),
                )
                ok = bool(envelope.get("ok"))
                if not ok:
                    err = envelope.get("error") if isinstance(envelope, dict) else None
                    if isinstance(err, dict) and isinstance(err.get("code"), str):
                        error_code = err["code"]
                self._send_json(
                    status,
                    envelope,
                    request_id=request_id,
                )
            except ControllerError as exc:
                status = exc.status
                ok = False
                error_code = exc.code
                self._send_error_json(
                    status,
                    exc.code,
                    exc.message,
                    request_id=request_id,
                    details=exc.details,
                )
            except Exception:
                status = 500
                ok = False
                error_code = "internal_error"
                self._send_error_json(
                    status,
                    error_code,
                    "internal error",
                    request_id=request_id,
                )
            finally:
                self._audit(
                    request_id=request_id,
                    operation=operation,
                    status=status,
                    started=started,
                    ok=ok,
                    tool=tool,
                    dry_run=dry_run,
                    confirm=confirm,
                    error_code=error_code,
                )

        def _method_not_allowed(self) -> None:
            request_id = _safe_request_id(self.headers.get(_REQUEST_ID_HEADER))
            started = time.perf_counter()
            route = self.path.split("?", 1)[0].rstrip("/") or "/"
            operation = route.lstrip("/") or "root"
            status = 405
            error_code = "method_not_allowed"
            try:
                if not self._auth_or_401(request_id=request_id):
                    status = 401
                    error_code = "authentication_error"
                    return
                self._send_error_json(
                    status,
                    error_code,
                    "method not allowed",
                    request_id=request_id,
                    extra_headers={"Allow": "GET, POST"},
                )
            finally:
                self._audit(
                    request_id=request_id,
                    operation=operation,
                    status=status,
                    started=started,
                    ok=False,
                    error_code=error_code,
                )

        do_HEAD = _method_not_allowed
        do_PUT = _method_not_allowed
        do_PATCH = _method_not_allowed
        do_DELETE = _method_not_allowed
        do_OPTIONS = _method_not_allowed

    return ControllerHandler


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    allow_public_bind: bool = False,
    allow_unauthenticated_loopback: bool = False,
    env: Optional[Mapping[str, str]] = None,
    server_class: Optional[type[ThreadingHTTPServer]] = None,
    list_tools_func: ListToolsFunc = mcp.list_tools,
    call_tool_func: CallToolFunc = mcp.call_tool,
    audit_logger: Optional[AuditLogger] = None,
    max_body_bytes: int = _MAX_BODY_BYTES,
    read_timeout_seconds: float = _READ_TIMEOUT_SECONDS,
    idempotency_db_path: str = DEFAULT_IDEMPOTENCY_DB_PATH,
    idempotency_retention_seconds: float = DEFAULT_IDEMPOTENCY_RETENTION_SECONDS,
    idempotency_max_records: int = DEFAULT_IDEMPOTENCY_MAX_RECORDS,
    idempotency_max_result_bytes: int = DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES,
    operation_store: Optional[OperationStore] = None,
    resolver: Optional[Callable[..., Sequence[Any]]] = None,
    allowed_operations: Optional[Sequence[str]] = None,
) -> ThreadingHTTPServer:
    """Return an unstarted controller server.

    Tests can pass ``port=0`` for an ephemeral local port, a fake
    ``server_class`` to avoid opening a socket, and fake MCP functions to assert
    that transport behavior does not duplicate tool logic.
    """
    effective_env = os.environ if env is None else env
    assessment = validate_bind_safety(
        host,
        allow_public_bind=allow_public_bind,
        allow_unauthenticated_loopback=allow_unauthenticated_loopback,
        auth_token_env=auth_token_env,
        env=effective_env,
        resolver=resolver,
    )
    token = resolve_auth_token(
        auth_token_env,
        env=effective_env,
        required=assessment.requires_auth,
    )
    store = operation_store or OperationStore(
        idempotency_db_path,
        retention_seconds=idempotency_retention_seconds,
        max_records=idempotency_max_records,
        max_result_bytes=idempotency_max_result_bytes,
    )
    handler = make_handler(
        list_tools_func=list_tools_func,
        call_tool_func=call_tool_func,
        auth_token=token,
        audit_logger=audit_logger,
        max_body_bytes=max_body_bytes,
        read_timeout_seconds=read_timeout_seconds,
        operation_store=store,
        allowed_operations=allowed_operations,
    )
    cls = server_class or _server_class_for_host(host)
    httpd = cls((host, port), handler)
    httpd.anvil_controller_bind = assessment
    httpd.anvil_controller_auth_token_env = auth_token_env
    httpd.anvil_controller_auth_enabled = token is not None
    return httpd


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    auth_token_env: Optional[str] = DEFAULT_AUTH_TOKEN_ENV,
    allow_public_bind: bool = False,
    allow_unauthenticated_loopback: bool = False,
    allowed_operations: Optional[Sequence[str]] = None,
    server_factory: Callable[..., ThreadingHTTPServer] = make_server,
) -> int:
    httpd = server_factory(
        host=host,
        port=port,
        auth_token_env=auth_token_env,
        allow_public_bind=allow_public_bind,
        allow_unauthenticated_loopback=allow_unauthenticated_loopback,
        allowed_operations=allowed_operations,
    )
    actual_host, actual_port = httpd.server_address[:2]
    print(
        "anvil-serving controller listening on http://%s:%s" % (actual_host, actual_port),
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.server_close()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="anvil-serving controller")
    subparsers = parser.add_subparsers(dest="command")
    serve_parser = subparsers.add_parser("serve", help="start the HTTP controller")
    serve_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_parser.add_argument(
        "--auth-token-env",
        default=DEFAULT_AUTH_TOKEN_ENV,
        help=(
            "environment variable containing the controller token (default: ANVIL_CONTROLLER_TOKEN)"
        ),
    )
    serve_parser.add_argument(
        "--allow-public-bind",
        action="store_true",
        help="allow a public or wildcard bind; still requires --auth-token-env to be set",
    )
    serve_parser.add_argument(
        "--allow-operation",
        action="append",
        default=None,
        help="restrict the controller to a declared operation (repeatable)",
    )
    status_parser = subparsers.add_parser("status", help="probe controller health")
    status_parser.add_argument("--url", default=DEFAULT_STATUS_URL)
    status_parser.add_argument("--auth-token-env", default=DEFAULT_AUTH_TOKEN_ENV)
    status_parser.add_argument("--timeout", type=float, default=5.0)
    status_parser.add_argument(
        "--max-response-bytes", type=int, default=DEFAULT_STATUS_MAX_RESPONSE_BYTES
    )
    status_parser.add_argument(
        "--require-operation",
        action="append",
        default=(),
        help="require a declared controller capability (repeatable)",
    )
    return parser


def _status_payload(
    url: str,
    path: str,
    *,
    token: str,
    timeout: float,
    max_response_bytes: int,
    _open: Callable[..., Any],
) -> dict[str, Any]:
    request = urllib.request.Request(
        url.rstrip("/") + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    with _open(request, timeout=timeout) as response:
        raw = response.read(max_response_bytes + 1)
    if not isinstance(raw, bytes):
        raise ValueError("controller status response body must be bytes")
    if len(raw) > max_response_bytes:
        raise ValueError("controller status response exceeds the configured limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("controller status response must be an object")
    return payload


def status(
    url: str = DEFAULT_STATUS_URL,
    *,
    auth_token_env: str = DEFAULT_AUTH_TOKEN_ENV,
    timeout: float = 5.0,
    max_response_bytes: int = DEFAULT_STATUS_MAX_RESPONSE_BYTES,
    required_operations: Sequence[str] = (),
    environment: Optional[Mapping[str, str]] = None,
    _open=urllib.request.urlopen,
) -> int:
    """Probe bounded authenticated controller health and capabilities."""
    if timeout <= 0 or timeout > 60:
        print("controller status: timeout must be between 0 and 60 seconds", file=sys.stderr)
        return 2
    if max_response_bytes < 1 or max_response_bytes > DEFAULT_MAX_BODY_BYTES:
        print(
            "controller status: max response bytes must be between 1 and %s"
            % DEFAULT_MAX_BODY_BYTES,
            file=sys.stderr,
        )
        return 2
    effective_env = os.environ if environment is None else environment
    token = (effective_env.get(auth_token_env) or "").strip()
    if not token:
        print(
            "controller status: token environment variable %s is unset or empty"
            % auth_token_env,
            file=sys.stderr,
        )
        return 3
    try:
        health = _status_payload(
            url,
            "/health",
            token=token,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            _open=_open,
        )
        if health.get("status") != "ok" or health.get("service") != "anvil-serving-controller":
            raise ValueError("controller health identity is invalid")
        capabilities = _status_payload(
            url,
            "/tools/list",
            token=token,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            _open=_open,
        )
        tools = capabilities.get("tools")
        if not isinstance(tools, list):
            raise ValueError("controller capability response has no tools list")
        tool_names = sorted(
            tool["name"]
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        )
        if len(tool_names) != len(tools) or len(set(tool_names)) != len(tool_names):
            raise ValueError("controller capability response contains invalid tool declarations")
        required = {_mcp_tool_name(name) for name in required_operations}
        missing = sorted(required - {_mcp_tool_name(name) for name in tool_names})
        if missing:
            raise ValueError("controller is missing required operations: %s" % ", ".join(missing))
    except (OSError, ValueError) as exc:
        print("controller status: %s" % exc, file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "service": "anvil-serving-controller",
                "capabilities": {"tool_count": len(tool_names), "tools": tool_names},
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        return 2
    args = parser.parse_args(argv)
    if args.command == "serve":
        try:
            return serve(
                host=args.host,
                port=args.port,
                auth_token_env=args.auth_token_env,
                allow_public_bind=args.allow_public_bind,
                allow_unauthenticated_loopback=False,
                allowed_operations=args.allow_operation,
            )
        except ControllerError as exc:
            print(
                _json_dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": exc.code,
                            "message": exc.message,
                            "details": exc.details,
                        },
                    }
                ),
                file=sys.stderr,
            )
            return 2
    if args.command == "status":
        return status(
            args.url,
            auth_token_env=args.auth_token_env,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            required_operations=args.require_operation,
        )
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
