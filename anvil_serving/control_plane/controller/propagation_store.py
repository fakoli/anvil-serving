"""Durable owner intent and dispatch persistence for inert propagation v1.

This store intentionally has no relation to :class:`OperationStore`: controller
operation records expire, while propagation admission identity must outlive an
expired result cache.  It performs no fleet work and exposes only owner-derived
data needed by a future authenticated dispatcher.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator, Mapping

from ..propagation import (
    ActiveIdentity,
    ApprovedAuthority,
    MAX_TARGETS,
    PropagationContract,
    admit_contract,
    parse_contract,
)


MAX_PENDING_ITEMS = 100
MAX_DISPATCH_BYTES = 64 * 1024
TERMINAL_PAYLOAD_RETENTION = timedelta(days=90)
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CURSOR = re.compile(r"^p1:([1-9][0-9]{0,18})$")
_SQLITE_MAX_INTEGER = (1 << 63) - 1
_MIGRATION_1 = (
    """CREATE TABLE propagation_intents (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT NOT NULL UNIQUE,
        scope TEXT NOT NULL, revision TEXT NOT NULL, generation INTEGER NOT NULL,
        contract_digest TEXT NOT NULL UNIQUE, workflow_id TEXT NOT NULL,
        effect_set_digest TEXT NOT NULL, target_set_digest TEXT NOT NULL,
        canonical_payload BLOB, projection_payload BLOB, created_at INTEGER NOT NULL,
        terminal_at INTEGER
    )""",
    """CREATE TABLE propagation_outbox (
        intent_id TEXT PRIMARY KEY REFERENCES propagation_intents(intent_id),
        acknowledged_at INTEGER, workflow_id TEXT, created_at INTEGER NOT NULL
    )""",
    """CREATE TABLE propagation_request_tombstones (
        caller_id TEXT NOT NULL, request_id TEXT NOT NULL, contract_digest TEXT NOT NULL,
        intent_id TEXT NOT NULL, PRIMARY KEY (caller_id, request_id)
    )""",
    """CREATE TABLE propagation_scope_tombstones (
        scope TEXT NOT NULL, revision TEXT NOT NULL, contract_digest TEXT NOT NULL,
        intent_id TEXT NOT NULL, PRIMARY KEY (scope, revision)
    )""",
    """CREATE TABLE propagation_identity_tombstones (
        kind TEXT NOT NULL, identity TEXT NOT NULL, contract_digest TEXT NOT NULL,
        intent_id TEXT NOT NULL, PRIMARY KEY (kind, identity)
    )""",
    "CREATE TABLE propagation_scope_high_water (scope TEXT PRIMARY KEY, generation INTEGER NOT NULL)",
    "CREATE INDEX propagation_pending_scan ON propagation_outbox(acknowledged_at, intent_id)",
)


class PropagationIntentError(ValueError):
    """A bounded, stable persistence failure for owner adapters."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AcceptedIntent:
    intent_id: str
    workflow_id: str
    contract_digest: str
    duplicate: bool


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("ascii")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _token(value: object, code: str = "malformed_identity") -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise PropagationIntentError(code)
    return value


def _utc_seconds(value: datetime) -> int:
    if not isinstance(value, datetime) or value.tzinfo != timezone.utc or value.utcoffset() != timedelta(0):
        raise PropagationIntentError("malformed_clock")
    return int(value.timestamp())


def logical_workflow_id(scope: str, revision: str) -> str:
    """Return the deterministic workflow identity specified by the owner design."""

    return "propagation-" + _digest(["workflow/v1", _token(scope), _token(revision)])


def _intent_id(contract_digest: str) -> str:
    return "intent-" + contract_digest


def _target_projection(contract: PropagationContract) -> tuple[str, list[dict[str, str]]]:
    value = contract.value
    targets = [
        {
            "target_id": target["target_id"],
            "check_set_digest": _digest(target["checks"]),
        }
        for target in value["targets"]
    ]
    # The contract parser has already bounded target count and forbidden duplicate
    # IDs.  Rechecking here keeps the future worker projection fail-closed.
    if len(targets) > MAX_TARGETS or [row["target_id"] for row in targets] != sorted(row["target_id"] for row in targets):
        targets.sort(key=lambda row: row["target_id"])
    return _digest(value["targets"]), targets


class PropagationIntentStore:
    """A migrated SQLite owner ledger with permanent deduplication authority."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_active_intents: int = 10_000,
        max_database_bytes: int = 64 * 1024 * 1024,
        post_commit: Callable[[], None] | None = None,
        post_ack_commit: Callable[[], None] | None = None,
    ) -> None:
        if max_active_intents < 1 or max_database_bytes < 1:
            raise ValueError("propagation store limits must be positive")
        self.path = Path(path)
        self.max_active_intents = max_active_intents
        self.max_database_bytes = max_database_bytes
        self._post_commit = post_commit
        self._post_ack_commit = post_ack_commit
        self._migrate()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS propagation_schema "
                    "(version INTEGER PRIMARY KEY)"
                )
                version = connection.execute(
                    "SELECT MAX(version) AS version FROM propagation_schema"
                ).fetchone()["version"]
                if version is None:
                    for statement in _MIGRATION_1:
                        connection.execute(statement)
                    connection.execute("INSERT INTO propagation_schema(version) VALUES (1)")
                elif version != 1:
                    raise PropagationIntentError("unsupported_schema")
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def admit(
        self,
        raw: bytes | str | Mapping[str, Any],
        *,
        approval_lookup: Callable[[str], ApprovedAuthority | None],
        active_identity: ActiveIdentity,
        caller_id: str,
        request_id: str,
        now: datetime,
    ) -> AcceptedIntent:
        """Atomically admit immutable approved bytes and create one outbox row."""

        # ``caller_id`` is an authenticated-handler custody value, never a
        # body assertion.  The ledger uses it only to bind request replay;
        # authorization of a *new* intent remains owner approval plus active
        # identity validation below.
        caller_id, request_id = _token(caller_id), _token(request_id)
        # A retry after the execution window has elapsed, or a second request
        # for the already-admitted scope/revision, resolves the durable binding.
        # Parsing verifies exact canonical bytes but does not authorize new work.
        parsed = parse_contract(raw)
        resolved = self._resolve_request(caller_id, request_id, parsed.digest)
        if resolved is not None:
            return resolved
        # A concurrent admission may commit after the optimistic lookup.
        # Resolve its digest inside the duplicate-binding transaction below;
        # a separate existence check would misclassify an identical replay.
        scope_duplicate = self._bind_scope_duplicate(caller_id, request_id, parsed)
        if scope_duplicate is not None:
            return scope_duplicate
        contract = admit_contract(raw, approval_lookup, active_identity, now)
        value = contract.value
        if value["generation"] > _SQLITE_MAX_INTEGER:
            raise PropagationIntentError("malformed_generation")
        intent_id = _intent_id(contract.digest)
        workflow_id = logical_workflow_id(value["scope"], value["revision"])
        target_set_digest, targets = _target_projection(contract)
        projection = {
            "intent_id": intent_id,
            "contract_digest": contract.digest,
            "target_set_digest": target_set_digest,
            "effect_set_digest": value["effect_set_digest"],
            "issued_at": value["issued_at"],
            "deadline_at": value["deadline_at"],
            "targets": targets,
        }
        canonical, projected = contract.canonical, _canonical(projection)
        public_row = {"workflow_id": workflow_id, **projection}
        # Admit only an outbox row that can be returned in a real bounded
        # dispatch response, including the largest valid continuation cursor.
        maximum_page = {"intents": [public_row], "next_cursor": "p1:9223372036854775807"}
        if (len(canonical) > self.max_database_bytes
                or len(projected) > MAX_DISPATCH_BYTES
                or len(_canonical(maximum_page)) > MAX_DISPATCH_BYTES):
            raise PropagationIntentError("storage_capacity")
        now_seconds = _utc_seconds(now)

        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT contract_digest, intent_id FROM propagation_request_tombstones "
                    "WHERE caller_id = ? AND request_id = ?", (caller_id, request_id),
                ).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    if existing["contract_digest"] != contract.digest:
                        raise PropagationIntentError("intent_conflict")
                    return AcceptedIntent(existing["intent_id"], workflow_id, contract.digest, True)

                scope_existing = connection.execute(
                    "SELECT contract_digest, intent_id FROM propagation_scope_tombstones "
                    "WHERE scope = ? AND revision = ?", (value["scope"], value["revision"]),
                ).fetchone()
                if scope_existing is not None:
                    if scope_existing["contract_digest"] != contract.digest:
                        connection.execute("ROLLBACK")
                        raise PropagationIntentError("intent_conflict")
                    original = connection.execute(
                        "SELECT workflow_id FROM propagation_intents WHERE intent_id = ?",
                        (scope_existing["intent_id"],),
                    ).fetchone()
                    if original is None:
                        connection.execute("ROLLBACK")
                        raise PropagationIntentError("storage_unavailable")
                    connection.execute(
                        "INSERT INTO propagation_request_tombstones VALUES (?, ?, ?, ?)",
                        (caller_id, request_id, contract.digest, scope_existing["intent_id"]),
                    )
                    self._commit_if_within_capacity(connection)
                    return AcceptedIntent(
                        scope_existing["intent_id"], original["workflow_id"], contract.digest, True,
                    )
                high_water = connection.execute(
                    "SELECT generation FROM propagation_scope_high_water WHERE scope = ?", (value["scope"],),
                ).fetchone()
                if high_water is not None and value["generation"] <= high_water["generation"]:
                    connection.execute("ROLLBACK")
                    raise PropagationIntentError("stale_generation")
                if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='propagation_native_resources'").fetchone():
                    if connection.execute("SELECT 1 FROM propagation_native_resources LIMIT 1").fetchone():
                        connection.execute("ROLLBACK")
                        raise PropagationIntentError("resource_conflict")
                active_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM propagation_intents WHERE terminal_at IS NULL"
                ).fetchone()["count"]
                if active_count >= self.max_active_intents:
                    connection.execute("ROLLBACK")
                    raise PropagationIntentError("storage_capacity")

                connection.execute(
                    "INSERT INTO propagation_intents "
                    "(intent_id, scope, revision, generation, contract_digest, workflow_id, effect_set_digest, target_set_digest, canonical_payload, projection_payload, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (intent_id, value["scope"], value["revision"], value["generation"], contract.digest,
                     workflow_id, value["effect_set_digest"], target_set_digest, canonical, projected, now_seconds),
                )
                connection.execute(
                    "INSERT INTO propagation_outbox(intent_id, created_at) VALUES (?, ?)", (intent_id, now_seconds),
                )
                connection.execute(
                    "INSERT INTO propagation_request_tombstones VALUES (?, ?, ?, ?)",
                    (caller_id, request_id, contract.digest, intent_id),
                )
                connection.execute(
                    "INSERT INTO propagation_scope_tombstones VALUES (?, ?, ?, ?)",
                    (value["scope"], value["revision"], contract.digest, intent_id),
                )
                for kind, identity in (("contract", contract.digest), ("effect-set", value["effect_set_digest"])):
                    connection.execute(
                        "INSERT INTO propagation_identity_tombstones VALUES (?, ?, ?, ?)",
                        (kind, identity, contract.digest, intent_id),
                    )
                connection.execute(
                    "INSERT INTO propagation_scope_high_water(scope, generation) VALUES (?, ?) "
                    "ON CONFLICT(scope) DO UPDATE SET generation = excluded.generation",
                    (value["scope"], value["generation"]),
                )
                self._commit_if_within_capacity(connection)
        except PropagationIntentError:
            raise
        except sqlite3.Error as exc:
            resolved = self._resolve_request(caller_id, request_id, contract.digest)
            if resolved is not None:
                return resolved
            raise PropagationIntentError("storage_unavailable") from exc

        try:
            if self._post_commit is not None:
                self._post_commit()
        except Exception:
            # A lost acknowledgement after COMMIT is resolved by the permanent
            # request tombstone.  No second outbox row or effect is created.
            resolved = self._resolve_request(caller_id, request_id, contract.digest)
            if resolved is not None:
                return resolved
            raise PropagationIntentError("storage_unavailable") from None
        return AcceptedIntent(intent_id, workflow_id, contract.digest, False)

    def pending(self, cursor: str | None = None, *, limit: int = MAX_PENDING_ITEMS) -> dict[str, Any]:
        """Return a bounded owner-derived page of unacknowledged immutable intent input."""

        if type(limit) is not int or isinstance(limit, bool) or not 1 <= limit <= MAX_PENDING_ITEMS:
            raise PropagationIntentError("invalid_page_limit")
        after = self._decode_cursor(cursor)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT i.sequence, i.workflow_id, i.projection_payload FROM propagation_outbox o "
                "JOIN propagation_intents i ON i.intent_id = o.intent_id "
                "WHERE o.acknowledged_at IS NULL AND i.terminal_at IS NULL AND i.sequence > ? "
                "ORDER BY i.sequence LIMIT ?", (after, limit + 1),
            ).fetchall()
        page_rows: list[dict[str, Any]] = []
        last_sequence = after
        next_cursor: str | None = None
        for index, row in enumerate(rows):
            projection = self._decode_projection(row["projection_payload"])
            candidate = {"workflow_id": row["workflow_id"], **projection}
            has_more = index + 1 < len(rows)
            candidate_cursor = self._encode_cursor(row["sequence"]) if has_more else None
            # Size the public response exactly as returned: a non-null cursor
            # costs bytes too, and internal SQLite sequence values never enter it.
            if len(_canonical({"intents": [*page_rows, candidate], "next_cursor": candidate_cursor})) > MAX_DISPATCH_BYTES:
                if not page_rows:
                    raise PropagationIntentError("dispatch_page_too_large")
                next_cursor = self._encode_cursor(last_sequence)
                break
            page_rows.append(candidate)
            last_sequence = row["sequence"]
            if len(page_rows) == limit:
                next_cursor = self._encode_cursor(last_sequence) if has_more else None
                break
        return {"intents": page_rows, "next_cursor": next_cursor}

    def contract_bytes(self, intent_id: str) -> bytes:
        """Return the immutable admitted bytes for a non-pruned owner operation."""
        intent_id = _token(intent_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT canonical_payload FROM propagation_intents WHERE intent_id = ?", (intent_id,),
            ).fetchone()
        if row is None:
            raise PropagationIntentError("intent_not_found")
        value = row["canonical_payload"]
        if not isinstance(value, bytes):
            raise PropagationIntentError("storage_unavailable")
        return value

    def assert_current(self, intent_id: str) -> None:
        """Fence admission against the owner's current scope generation."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT i.generation, i.terminal_at, h.generation AS current_generation "
                "FROM propagation_intents i JOIN propagation_scope_high_water h ON i.scope=h.scope "
                "WHERE i.intent_id=?", (_token(intent_id),),
            ).fetchone()
        if row is None:
            raise PropagationIntentError("intent_not_found")
        if row["generation"] != row["current_generation"]:
            raise PropagationIntentError("stale_generation")
        if row["terminal_at"] is not None:
            raise PropagationIntentError("intent_terminal")

    def acknowledge(self, intent_id: str, *, workflow_id: str, contract_digest: str, now: datetime) -> bool:
        """Persist an observed deterministic workflow binding; no execution occurs."""

        intent_id, workflow_id = _token(intent_id), _token(workflow_id)
        if type(contract_digest) is not str or re.fullmatch(r"[0-9a-f]{64}", contract_digest) is None:
            raise PropagationIntentError("malformed_identity")
        now_seconds = _utc_seconds(now)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT i.workflow_id, i.contract_digest, o.acknowledged_at, o.workflow_id AS recorded_workflow "
                    "FROM propagation_intents i JOIN propagation_outbox o ON o.intent_id = i.intent_id "
                    "WHERE i.intent_id = ?", (intent_id,),
                ).fetchone()
                if row is None:
                    connection.execute("ROLLBACK")
                    raise PropagationIntentError("intent_not_found")
                if row["workflow_id"] != workflow_id or row["contract_digest"] != contract_digest:
                    connection.execute("ROLLBACK")
                    raise PropagationIntentError("dispatch_binding_mismatch")
                if row["acknowledged_at"] is None:
                    connection.execute(
                        "UPDATE propagation_outbox SET acknowledged_at = ?, workflow_id = ? WHERE intent_id = ?",
                        (now_seconds, workflow_id, intent_id),
                    )
                    connection.execute("COMMIT")
                    try:
                        if self._post_ack_commit is not None:
                            self._post_ack_commit()
                    except Exception:
                        # A dispatcher that loses its acknowledgement must look
                        # up this exact durable binding, never start again.
                        return self._acknowledged(intent_id, workflow_id, contract_digest)
                    return True
                connection.execute("COMMIT")
                return row["recorded_workflow"] == workflow_id
        except PropagationIntentError:
            raise
        except sqlite3.Error as exc:
            raise PropagationIntentError("storage_unavailable") from exc

    def mark_terminal(self, intent_id: str, *, now: datetime) -> None:
        """Record terminality for retention tests; it never removes identity authority."""

        intent_id = _token(intent_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE propagation_intents SET terminal_at = ? WHERE intent_id = ? AND terminal_at IS NULL",
                (_utc_seconds(now), intent_id),
            ).rowcount
            if not updated:
                connection.execute("ROLLBACK")
                raise PropagationIntentError("intent_not_found")
            connection.execute("COMMIT")

    def prune_terminal_payloads(self, *, now: datetime) -> int:
        """Remove only aged terminal payload bytes; permanent identities remain."""

        cutoff = _utc_seconds(now - TERMINAL_PAYLOAD_RETENTION)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute(
                "UPDATE propagation_intents SET canonical_payload = NULL, projection_payload = NULL "
                "WHERE terminal_at IS NOT NULL AND terminal_at <= ? AND canonical_payload IS NOT NULL", (cutoff,),
            ).rowcount
            connection.execute("COMMIT")
            return count

    def _resolve_request(self, caller_id: str, request_id: str, contract_digest: str) -> AcceptedIntent | None:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT r.contract_digest, r.intent_id, i.workflow_id FROM propagation_request_tombstones r "
                    "JOIN propagation_intents i ON i.intent_id = r.intent_id "
                    "WHERE r.caller_id = ? AND r.request_id = ?", (caller_id, request_id),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None or row["contract_digest"] != contract_digest:
            return None
        return AcceptedIntent(row["intent_id"], row["workflow_id"], contract_digest, True)

    def _bind_scope_duplicate(
        self,
        caller_id: str,
        request_id: str,
        parsed: PropagationContract,
    ) -> AcceptedIntent | None:
        """Bind a new authenticated request to an existing immutable scope.

        This deliberately precedes expiry and active-identity checks: those
        gates apply to allocating the original intent, whereas this only gives
        a new authenticated caller/request pair its already-persisted outcome.
        Capacity is checked before allocating the permanent request tombstone.
        """

        value = parsed.value
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT contract_digest, intent_id FROM propagation_request_tombstones "
                    "WHERE caller_id = ? AND request_id = ?", (caller_id, request_id),
                ).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    if existing["contract_digest"] != parsed.digest:
                        raise PropagationIntentError("intent_conflict")
                    return self._resolve_request(caller_id, request_id, parsed.digest)
                scope = connection.execute(
                    "SELECT contract_digest, intent_id FROM propagation_scope_tombstones "
                    "WHERE scope = ? AND revision = ?", (value["scope"], value["revision"]),
                ).fetchone()
                if scope is None:
                    connection.execute("COMMIT")
                    return None
                if scope["contract_digest"] != parsed.digest:
                    connection.execute("ROLLBACK")
                    raise PropagationIntentError("intent_conflict")
                original = connection.execute(
                    "SELECT workflow_id FROM propagation_intents WHERE intent_id = ?", (scope["intent_id"],),
                ).fetchone()
                if original is None:
                    connection.execute("ROLLBACK")
                    raise PropagationIntentError("storage_unavailable")
                connection.execute(
                    "INSERT INTO propagation_request_tombstones VALUES (?, ?, ?, ?)",
                    (caller_id, request_id, parsed.digest, scope["intent_id"]),
                )
                self._commit_if_within_capacity(connection)
                return AcceptedIntent(scope["intent_id"], original["workflow_id"], parsed.digest, True)
        except PropagationIntentError:
            raise
        except sqlite3.Error as exc:
            raise PropagationIntentError("storage_unavailable") from exc

    def _acknowledged(self, intent_id: str, workflow_id: str, contract_digest: str) -> bool:
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT i.contract_digest, o.workflow_id, o.acknowledged_at "
                    "FROM propagation_intents i JOIN propagation_outbox o ON o.intent_id = i.intent_id "
                    "WHERE i.intent_id = ?", (intent_id,),
                ).fetchone()
        except sqlite3.Error:
            return False
        return bool(row and row["acknowledged_at"] is not None
                    and row["workflow_id"] == workflow_id
                    and row["contract_digest"] == contract_digest)

    def _commit_if_within_capacity(self, connection: sqlite3.Connection) -> None:
        """Commit only when this transaction's allocated SQLite pages fit.

        SQLite allocates pages for table and index writes together, so byte
        estimates from payload lengths leave a gap at page boundaries.  Query
        the transaction's actual allocation after every durable write, then
        roll every linked row back before the commit when it exceeds the cap.
        """

        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        if page_count * page_size > self.max_database_bytes:
            connection.execute("ROLLBACK")
            raise PropagationIntentError("storage_capacity")
        connection.execute("COMMIT")

    @staticmethod
    def _encode_cursor(sequence: int) -> str:
        return f"p1:{sequence}"

    @staticmethod
    def _decode_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 0
        if type(cursor) is not str:
            raise PropagationIntentError("invalid_cursor")
        matched = _CURSOR.fullmatch(cursor)
        if matched is None:
            raise PropagationIntentError("invalid_cursor")
        value = int(matched.group(1))
        if value > _SQLITE_MAX_INTEGER:
            raise PropagationIntentError("invalid_cursor")
        return value

    @staticmethod
    def _decode_projection(payload: bytes | None) -> dict[str, Any]:
        if not isinstance(payload, bytes):
            raise PropagationIntentError("dispatch_payload_expired")
        try:
            value = json.loads(payload.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            raise PropagationIntentError("storage_unavailable") from None
        if type(value) is not dict:
            raise PropagationIntentError("storage_unavailable")
        return value
