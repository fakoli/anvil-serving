"""Durable controller operation persistence and idempotency fingerprints."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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

from ...benchmarking.artifacts import atomic_write_json
from ...benchmarking.jobs import (
    BenchmarkJobError,
    append_job_log,
    build_artifact_envelope,
    canonical_json_bytes,
    job_spec_sha256,
    new_job_record,
    resolve_owned_run_path,
    transition_job,
    validate_job_id,
    validate_job_record,
    validate_job_spec,
)
from ...observability.workloads import (
    MAX_FUTURE_SECONDS,
    MAX_JSON_BYTES,
    SOURCE_LIMIT,
    ObservationQuality,
    ResultStatus,
    SourceAuthority,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOwner,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadState,
    format_workload_timestamp,
    map_store_state,
    normalize_workload_timestamp,
    parse_workload_timestamp,
    select_records,
    validate_source_records,
    workload_id,
)
from .errors import ControllerError
from .security import _json_dumps, _sanitize_persisted_value, _strict_json_loads


_IDEMPOTENCY_KEY_HEADER = "X-Anvil-Idempotency-Key"
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDEMPOTENCY_CONTEXT_FIELDS = ("topology", "execution_host", "execution_runtime")

DEFAULT_IDEMPOTENCY_RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_IDEMPOTENCY_MAX_RECORDS = 1024
DEFAULT_IDEMPOTENCY_MAX_RESULT_BYTES = 64 * 1024
DEFAULT_IDEMPOTENCY_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".anvil-serving", "controller-operations.sqlite3"
)
DEFAULT_BENCHMARK_JOB_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".anvil-serving", "benchmark-jobs.sqlite3"
)
DEFAULT_BENCHMARK_RUN_ROOT = os.path.join(
    os.path.expanduser("~"), ".anvil-serving", "benchmark-runs"
)

Clock = Callable[[], float]
BenchmarkCleanup = Callable[[str], None]

_STORE_SNAPSHOT_SECONDS = 1.0
_STORE_PROGRESS_INSTRUCTIONS = 1000
_STORE_TIMESTAMP_BYTES = 65
_UNKNOWN_STATE = "__unknown__"

_BENCHMARK_WORKLOAD_SQL = """
WITH extracted AS (
    SELECT
        rowid AS native_rowid,
        CASE
            WHEN typeof(state) <> 'text' THEN NULL
            WHEN state IN ('queued', 'running', 'completed', 'failed', 'cancelled')
                THEN state
            ELSE ?
        END AS state_code,
        CASE
            WHEN typeof(record) = 'text'
                 AND length(CAST(record AS BLOB)) <= ?
            THEN CASE WHEN json_valid(record) THEN record ELSE NULL END
            ELSE NULL
        END AS safe_record
    FROM benchmark_jobs
), stamped AS (
    SELECT
        native_rowid,
        state_code,
        anvil_benchmark_digest(native_rowid) AS workload_digest,
        CASE
            WHEN safe_record IS NOT NULL
                 AND json_type(safe_record, '$.submitted_at') = 'text'
                 AND length(CAST(json_extract(safe_record, '$.submitted_at') AS BLOB))
                     BETWEEN 1 AND ?
            THEN anvil_store_timestamp(
                CAST(substr(
                    CAST(json_extract(safe_record, '$.submitted_at') AS BLOB), 1, ?
                ) AS TEXT)
            )
            ELSE NULL
        END AS created_at,
        CASE
            WHEN safe_record IS NOT NULL
                 AND json_type(safe_record, '$.updated_at') = 'text'
                 AND length(CAST(json_extract(safe_record, '$.updated_at') AS BLOB))
                     BETWEEN 1 AND ?
            THEN anvil_store_timestamp(
                CAST(substr(
                    CAST(json_extract(safe_record, '$.updated_at') AS BLOB), 1, ?
                ) AS TEXT)
            )
            ELSE NULL
        END AS updated_at
    FROM extracted
), classified AS (
    SELECT
        workload_digest,
        state_code,
        CASE
            WHEN state_code = 'queued' THEN 'queued'
            WHEN state_code = 'running' THEN 'running'
            WHEN state_code IN ('completed', 'failed', 'cancelled') THEN 'terminal'
            WHEN state_code = ? THEN 'unsupported'
            ELSE NULL
        END AS mapped_state,
        created_at,
        updated_at,
        CASE
            WHEN workload_digest IS NULL OR state_code IS NULL
                 OR created_at IS NULL OR updated_at IS NULL OR created_at > updated_at
                THEN 1
            WHEN created_at > ? OR updated_at > ? THEN 2
            ELSE 0
        END AS invalid_code
    FROM stamped
), selected AS (
    SELECT workload_digest, state_code, created_at, updated_at, invalid_code
    FROM classified
    WHERE
        (? IS NULL OR mapped_state = ? OR mapped_state IS NULL)
        AND (
            mapped_state IS NULL
            OR (
                ? = 1
                AND mapped_state IN ('queued', 'running')
                AND (updated_at IS NULL OR updated_at >= ?)
            )
            OR (
                ? = 0
                AND (
                    (mapped_state IN ('queued', 'running')
                        AND (updated_at IS NULL OR updated_at >= ?))
                    OR mapped_state = 'unsupported'
                    OR (mapped_state = 'terminal'
                        AND (updated_at IS NULL OR updated_at >= ?))
                )
            )
        )
)
SELECT workload_digest, state_code, created_at, updated_at, invalid_code
FROM selected
ORDER BY (invalid_code <> 0) ASC, updated_at DESC, workload_digest ASC
LIMIT ?
"""


_OPERATION_WORKLOAD_SQL = """
WITH lease_metadata AS (
    SELECT
        idempotency_key,
        COUNT(*) AS match_count,
        CASE WHEN COUNT(*) = 1 THEN MAX(updated_at) END AS updated_at
    FROM operation_leases
    GROUP BY idempotency_key
), extracted AS (
    SELECT
        records.rowid AS native_rowid,
        CASE
            WHEN typeof(records.status) <> 'text' THEN NULL
            WHEN records.status IN ('running', 'succeeded', 'failed')
                THEN records.status
            ELSE ?
        END AS state_code,
        CASE WHEN typeof(records.created_at) IN ('integer', 'real')
            THEN anvil_operation_timestamp(records.created_at) END AS created_at,
        CASE WHEN typeof(records.updated_at) IN ('integer', 'real')
            THEN anvil_operation_timestamp(records.updated_at) END AS updated_at,
        CASE WHEN typeof(leases.updated_at) IN ('integer', 'real')
            THEN anvil_operation_timestamp(leases.updated_at) END AS lease_updated_at,
        COALESCE(leases.match_count, 0) AS lease_match_count
    FROM operation_records AS records
    LEFT JOIN lease_metadata AS leases
        ON leases.idempotency_key = records.idempotency_key
), stamped AS (
    SELECT
        native_rowid,
        state_code,
        created_at,
        updated_at,
        lease_updated_at,
        lease_match_count,
        CASE
            WHEN state_code = 'running'
                 AND lease_updated_at IS NOT NULL
                 AND lease_updated_at > updated_at THEN lease_updated_at
            ELSE updated_at
        END AS source_at
    FROM extracted
), digested AS (
    SELECT
        anvil_operation_digest(native_rowid, created_at) AS workload_digest,
        state_code,
        created_at,
        updated_at,
        source_at,
        lease_match_count
    FROM stamped
), classified AS (
    SELECT
        workload_digest,
        state_code,
        CASE
            WHEN state_code = 'running' THEN 'running'
            WHEN state_code IN ('succeeded', 'failed') THEN 'terminal'
            WHEN state_code = ? THEN 'unsupported'
            ELSE NULL
        END AS mapped_state,
        created_at,
        updated_at,
        source_at,
        CASE
            WHEN state_code IS NULL OR created_at IS NULL OR updated_at IS NULL
                 OR source_at IS NULL OR created_at > updated_at
                 OR updated_at > source_at OR workload_digest IS NULL
                 OR lease_match_count > 1 THEN 1
            WHEN created_at > ? OR updated_at > ? OR source_at > ? THEN 2
            ELSE 0
        END AS invalid_code
    FROM digested
), selected AS (
    SELECT workload_digest, state_code, created_at, updated_at, source_at, invalid_code
    FROM classified
    WHERE
        (? IS NULL OR mapped_state = ? OR mapped_state IS NULL)
        AND (
            mapped_state IS NULL
            OR (
                ? = 1
                AND mapped_state = 'running'
                AND (source_at IS NULL OR source_at >= ?)
            )
            OR (
                ? = 0
                AND (
                    (mapped_state = 'running'
                        AND (source_at IS NULL OR source_at >= ?))
                    OR mapped_state = 'unsupported'
                    OR (mapped_state = 'terminal'
                        AND (updated_at IS NULL OR updated_at >= ?))
                )
            )
        )
)
SELECT workload_digest, state_code, created_at, updated_at, source_at, invalid_code
FROM selected
ORDER BY (invalid_code <> 0) ASC, updated_at DESC, workload_digest ASC
LIMIT ?
"""


def _unavailable_source(owner: WorkloadOwner, collected: datetime) -> SourceResult:
    return SourceResult(
        owner=owner,
        status=ResultStatus.UNAVAILABLE,
        collection_timestamp=collected,
        records=(),
        truncation=Truncation(0, None),
        error=WorkloadErrorCode.UNAVAILABLE,
    )


def _empty_source(owner: WorkloadOwner, collected: datetime) -> SourceResult:
    return SourceResult(
        owner=owner,
        status=ResultStatus.COMPLETE,
        collection_timestamp=collected,
        records=(),
        truncation=Truncation(0, 0),
    )


def _canonical_store_timestamp(value: object) -> str | None:
    """Canonicalize one already-bounded timestamp scalar for SQLite ordering."""
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value.encode("utf-8")) > _STORE_TIMESTAMP_BYTES:
            return None
        suffix_normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(suffix_normalized)
        return format_workload_timestamp(parsed)
    except (OverflowError, TypeError, ValueError, WorkloadError):
        return None


def _benchmark_digest(host: str, rowid: object) -> str | None:
    if isinstance(rowid, bool) or not isinstance(rowid, int) or rowid < 1:
        return None
    try:
        return workload_id(
            host,
            WorkloadKind.BENCHMARK_JOB,
            WorkloadOwner.BENCHMARK,
            f"benchmark-row:{rowid}",
        )
    except WorkloadError:
        return None


def _canonical_operation_timestamp(value: object) -> str | None:
    """Return one canonical UTC timestamp from a SQLite numeric epoch scalar."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        epoch = float(value)
        if epoch != epoch or epoch in (float("inf"), float("-inf")):
            return None
        return format_workload_timestamp(datetime.fromtimestamp(epoch, tz=timezone.utc))
    except (OSError, OverflowError, ValueError, WorkloadError):
        return None


def _operation_digest(host: str, rowid: object, created_at: object) -> str | None:
    if (
        isinstance(rowid, bool)
        or not isinstance(rowid, int)
        or rowid < 1
        or not isinstance(created_at, str)
    ):
        return None
    try:
        parse_workload_timestamp(created_at)
        return workload_id(
            host,
            WorkloadKind.CONTROLLER_OPERATION,
            WorkloadOwner.CONTROLLER,
            f"operation-row:{rowid}:{created_at}",
        )
    except WorkloadError:
        return None


def _query_benchmark_state(state: WorkloadState | None) -> str | None:
    return None if state is None else state.value


def _query_operation_state(state: WorkloadState | None) -> str | None:
    return None if state is None else state.value


def _remaining(deadline: float, monotonic: Clock) -> float:
    try:
        return max(0.0, deadline - monotonic())
    except Exception:
        return 0.0


def _read_snapshot_rows(
    path: str,
    *,
    sql: str,
    parameters: tuple[object, ...],
    deadline: float,
    monotonic: Clock,
    functions: tuple[tuple[str, int, Callable[..., object]], ...],
) -> tuple[sqlite3.Row, ...]:
    """Execute one bounded, logically read-only snapshot before a shared deadline.

    Existing WAL databases may cause SQLite itself to create or recreate ``-wal``
    and ``-shm`` coordination sidecars. This helper never creates a main database,
    schema, journal mode, or product-owned content.
    """
    remaining = _remaining(deadline, monotonic)
    if remaining <= 0:
        raise TimeoutError("store workload snapshot deadline expired")
    uri = Path(path).absolute().as_uri() + "?mode=ro"
    connection = sqlite3.connect(
        uri, uri=True, timeout=remaining, isolation_level=None,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        for name, count, function in functions:
            connection.create_function(name, count, function, deterministic=True)

        def interrupted() -> int:
            return int(_remaining(deadline, monotonic) <= 0)

        connection.set_progress_handler(interrupted, _STORE_PROGRESS_INSTRUCTIONS)
        if _remaining(deadline, monotonic) <= 0:
            raise TimeoutError("store workload snapshot deadline expired")
        connection.execute("BEGIN")
        rows = tuple(connection.execute(sql, parameters).fetchall())
        if _remaining(deadline, monotonic) <= 0:
            raise TimeoutError("store workload snapshot deadline expired")
        connection.rollback()
        return rows
    finally:
        try:
            connection.set_progress_handler(None, 0)
        finally:
            connection.close()


def read_benchmark_workloads(
    path: str | os.PathLike[str], host: str, query: WorkloadQuery, now: datetime,
    *, _snapshot_clock: Clock = time.monotonic, _lock=None,
) -> SourceResult:
    """Read existing benchmark metadata without constructing a writable owner.

    Missing sources stay unavailable. This creates no main database, schema,
    run directory, job or artifact; SQLite may maintain WAL coordination
    sidecars, as documented by _read_snapshot_rows.
    """
    if not isinstance(query, WorkloadQuery):
        raise WorkloadError(
            WorkloadErrorCode.INVALID, "workload query has the wrong type",
        )
    collected = normalize_workload_timestamp(now)
    # Canonical identity construction validates the trusted host before storage access.
    workload_id(
        host, WorkloadKind.BENCHMARK_JOB, WorkloadOwner.BENCHMARK, "validation",
    )
    if (
        (query.owner is not None and query.owner is not WorkloadOwner.BENCHMARK)
        or (query.kind is not None and query.kind is not WorkloadKind.BENCHMARK_JOB)
        or (query.host is not None and query.host != host)
        or (
            query.state is not None
            and query.state not in {
                WorkloadState.QUEUED,
                WorkloadState.RUNNING,
                WorkloadState.TERMINAL,
                WorkloadState.UNSUPPORTED,
            }
        )
        or (
            query.active_only
            and query.state is not None
            and query.state not in {
                WorkloadState.QUEUED,
                WorkloadState.RUNNING,
            }
        )
    ):
        return _empty_source(WorkloadOwner.BENCHMARK, collected)

    try:
        future_cutoff = format_workload_timestamp(
            collected + timedelta(seconds=MAX_FUTURE_SECONDS),
        )
        freshness_cutoff = format_workload_timestamp(
            collected - timedelta(seconds=30),
        )
        recent_cutoff = format_workload_timestamp(
            collected - timedelta(seconds=query.recent_seconds),
        )
    except (OverflowError, WorkloadError):
        raise WorkloadError(
            WorkloadErrorCode.INVALID,
            "workload collection time is outside the supported range",
        ) from None
    cap = min(query.limit, SOURCE_LIMIT)
    state_code = _query_benchmark_state(query.state)
    parameters: tuple[object, ...] = (
        _UNKNOWN_STATE,
        MAX_JSON_BYTES,
        _STORE_TIMESTAMP_BYTES,
        _STORE_TIMESTAMP_BYTES,
        _STORE_TIMESTAMP_BYTES,
        _STORE_TIMESTAMP_BYTES,
        _UNKNOWN_STATE,
        future_cutoff,
        future_cutoff,
        state_code,
        state_code,
        int(query.active_only),
        freshness_cutoff,
        int(query.active_only),
        freshness_cutoff,
        recent_cutoff,
        cap + 1,
    )
    try:
        if not isinstance(path, (str, os.PathLike)):
            return _unavailable_source(WorkloadOwner.BENCHMARK, collected)
        raw_path = os.fspath(path)
        if not isinstance(raw_path, str) or not raw_path:
            return _unavailable_source(WorkloadOwner.BENCHMARK, collected)
        source_path = str(Path(raw_path).expanduser().absolute())
        lock = threading.RLock() if _lock is None else _lock
        deadline = _snapshot_clock() + _STORE_SNAPSHOT_SECONDS
    except Exception:
        return _unavailable_source(WorkloadOwner.BENCHMARK, collected)
    remaining = _remaining(deadline, _snapshot_clock)
    if remaining <= 0:
        return _unavailable_source(WorkloadOwner.BENCHMARK, collected)
    try:
        acquired = lock.acquire(timeout=remaining)
    except Exception:
        acquired = False
    if not acquired:
        return _unavailable_source(WorkloadOwner.BENCHMARK, collected)
    try:
        rows = _read_snapshot_rows(
            source_path,
            sql=_BENCHMARK_WORKLOAD_SQL,
            parameters=parameters,
            deadline=deadline,
            monotonic=_snapshot_clock,
            functions=(
                ("anvil_store_timestamp", 1, _canonical_store_timestamp),
                (
                    "anvil_benchmark_digest",
                    1,
                    lambda rowid: _benchmark_digest(host, rowid),
                ),
            ),
        )
    except Exception:
        return _unavailable_source(WorkloadOwner.BENCHMARK, collected)
    finally:
        lock.release()

    extra = len(rows) > cap
    records: list[WorkloadRecord] = []
    error: WorkloadErrorCode | None = None
    for row in rows[:cap]:
        invalid_code = row["invalid_code"]
        if invalid_code:
            code = (
                WorkloadErrorCode.FUTURE
                if invalid_code == 2
                else WorkloadErrorCode.INVALID
            )
            if error is not WorkloadErrorCode.FUTURE:
                error = code
            continue
        try:
            state, phase, outcome = map_store_state(
                WorkloadOwner.BENCHMARK, row["state_code"],
            )
            record = WorkloadRecord(
                id=row["workload_digest"],
                kind=WorkloadKind.BENCHMARK_JOB,
                owner=WorkloadOwner.BENCHMARK,
                host=host,
                state=state,
                phase=phase,
                outcome=outcome,
                created_at=parse_workload_timestamp(row["created_at"]),
                updated_at=parse_workload_timestamp(row["updated_at"]),
                source_timestamp=parse_workload_timestamp(row["updated_at"]),
                source_authority=SourceAuthority.BENCHMARK_STORE,
                observation_quality=ObservationQuality.RECORDED,
            )
            validate_source_records(
                (record,),
                owner=WorkloadOwner.BENCHMARK,
                host=host,
                collection_timestamp=collected,
            )
        except WorkloadError as exc:
            if error is not WorkloadErrorCode.FUTURE:
                error = exc.code
            continue
        records.append(record)

    try:
        canonical_records, canonical_truncation = select_records(
            tuple(records), query, now=collected,
        )
    except Exception:
        return _unavailable_source(WorkloadOwner.BENCHMARK, collected)
    if canonical_records != tuple(records) or canonical_truncation.omitted != 0:
        return _unavailable_source(WorkloadOwner.BENCHMARK, collected)

    partial = extra or error is not None
    return SourceResult(
        owner=WorkloadOwner.BENCHMARK,
        status=ResultStatus.PARTIAL if partial else ResultStatus.COMPLETE,
        collection_timestamp=collected,
        records=canonical_records,
        truncation=Truncation(len(records), None if partial else 0),
        error=error,
    )


class BenchmarkJobStore:
    """Durable suite-neutral benchmark jobs with bounded logs and artifacts."""

    def __init__(
        self,
        path: str = DEFAULT_BENCHMARK_JOB_DB_PATH,
        *,
        run_root: str = DEFAULT_BENCHMARK_RUN_ROOT,
        _snapshot_clock: Clock = time.monotonic,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("benchmark job database path must be a non-empty string")
        if not isinstance(run_root, str) or not run_root:
            raise ValueError("benchmark run root must be a non-empty string")
        if not callable(_snapshot_clock):
            raise ValueError("benchmark snapshot clock must be callable")
        self.path = path
        self.run_root = os.path.realpath(os.path.abspath(os.path.expanduser(run_root)))
        Path(self.run_root).mkdir(parents=True, exist_ok=True)
        resolve_owned_run_path(
            self.run_root,
            ownership_id="validation",
            run_id="validation",
        )
        self._lock = threading.RLock()
        self._snapshot_clock = _snapshot_clock

    def list_workloads(
        self, host: str, query: WorkloadQuery, now: datetime,
    ) -> SourceResult:
        """Return a read-only snapshot using this owner's exact lock and clock."""
        return read_benchmark_workloads(
            self.path, host, query, now,
            _snapshot_clock=self._snapshot_clock, _lock=self._lock,
        )

    def submit(self, spec: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        """Create a queued job, or return the identical existing run."""
        normalized = validate_job_spec(spec)
        digest = job_spec_sha256(normalized)
        record = new_job_record(normalized)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT spec_sha256, record FROM benchmark_jobs WHERE run_id = ?",
                (normalized["run_id"],),
            ).fetchone()
            if row is not None:
                existing = self._decode_record(row["record"])
                connection.commit()
                if row["spec_sha256"] != digest:
                    raise BenchmarkJobError(
                        "run_id_conflict",
                        "run_id already exists with a different immutable specification",
                        {"run_id": normalized["run_id"]},
                    )
                return "existing", existing
            connection.execute(
                """
                INSERT INTO benchmark_jobs (run_id, spec_sha256, state, revision, record)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalized["run_id"],
                    digest,
                    record["state"],
                    record["revision"],
                    _json_dumps(record),
                ),
            )
            connection.commit()
        return "submitted", record

    def status(self, run_id: str) -> Optional[dict[str, Any]]:
        run = validate_job_id(run_id)
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT record FROM benchmark_jobs WHERE run_id = ?", (run,)
            ).fetchone()
        return self._decode_record(row["record"]) if row is not None else None

    def logs(self, run_id: str, *, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            raise BenchmarkJobError("bad_log_cursor", "log cursor must be non-negative")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise BenchmarkJobError("bad_log_limit", "log limit must be between 1 and 1000")
        record = self._required(run_id)
        logs = record["logs"]
        entries = [item for item in logs["entries"] if item["cursor"] >= cursor][:limit]
        next_cursor = entries[-1]["cursor"] + 1 if entries else max(
            cursor, logs["retained_from"]
        )
        return {
            "run_id": record["spec"]["run_id"],
            "state": record["state"],
            "cursor": cursor,
            "next_cursor": next_cursor,
            "retained_from": logs["retained_from"],
            "truncated": logs["truncated"] or cursor < logs["retained_from"],
            "entries": entries,
        }

    def append_log(self, run_id: str, *, level: str, message: Any) -> dict[str, Any]:
        return self._mutate(
            run_id,
            lambda record: append_job_log(record, level=level, message=message),
        )

    def transition(
        self,
        run_id: str,
        target: str,
        *,
        failure: Mapping[str, Any] | None = None,
        results: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        def change(record: dict[str, Any]) -> dict[str, Any]:
            updated = transition_job(record, target, failure=failure)
            if target in {"completed", "failed", "cancelled"}:
                artifact = build_artifact_envelope(
                    updated, results=results, failure=failure
                )
                metadata = self._write_artifact(updated, artifact)
                updated = dict(updated)
                updated["artifact"] = metadata
            return updated

        return self._mutate(run_id, change)

    def claim(self, run_id: str) -> dict[str, Any]:
        """Atomically claim one queued job for exactly one worker process."""
        def change(record: dict[str, Any]) -> dict[str, Any]:
            if record["state"] != "queued":
                raise BenchmarkJobError(
                    "job_already_claimed",
                    "benchmark job is not queued",
                    {"state": record["state"]},
                )
            return transition_job(record, "running")

        return self._mutate(run_id, change)

    def cancel(
        self,
        run_id: str,
        *,
        cleanup: BenchmarkCleanup | None = None,
    ) -> dict[str, Any]:
        """Record partial evidence, perform owned cleanup, and finish cancelled."""
        record = self._required(run_id)
        if record["state"] in {"completed", "failed", "cancelled"}:
            return record
        if record["state"] == "running":
            record = self._mutate(
                run_id, lambda current: transition_job(current, "cancelling")
            )
        record = self._mutate(
            run_id,
            lambda current: append_job_log(
                current, level="warning", message="cancellation requested"
            ),
        )
        partial = build_artifact_envelope(record)
        self._write_artifact(record, partial)
        if cleanup is not None:
            work_path = resolve_owned_run_path(
                self.run_root,
                ownership_id=record["spec"]["ownership_id"],
                run_id=record["spec"]["run_id"],
                relative="work",
            )
            cleanup(work_path)
        return self.transition(run_id, "cancelled")

    def artifact(self, run_id: str) -> Optional[dict[str, Any]]:
        record = self._required(run_id)
        metadata = record.get("artifact")
        if not isinstance(metadata, Mapping):
            return None
        path = self._artifact_path(record)
        try:
            raw = Path(path).read_text(encoding="utf-8")
            value = _strict_json_loads(raw)
        except (OSError, TypeError, ValueError) as exc:
            raise BenchmarkJobError(
                "artifact_unavailable", "benchmark artifact could not be read"
            ) from exc
        digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()
        if digest != metadata.get("sha256"):
            raise BenchmarkJobError(
                "artifact_digest_mismatch", "benchmark artifact digest does not match"
            )
        return value

    def _required(self, run_id: str) -> dict[str, Any]:
        record = self.status(run_id)
        if record is None:
            raise BenchmarkJobError(
                "job_not_found", "benchmark job does not exist", {"run_id": run_id}
            )
        return record

    def _mutate(
        self,
        run_id: str,
        mutation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        run = validate_job_id(run_id)
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT record FROM benchmark_jobs WHERE run_id = ?", (run,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise BenchmarkJobError(
                    "job_not_found", "benchmark job does not exist", {"run_id": run}
                )
            updated = validate_job_record(mutation(self._decode_record(row["record"])))
            connection.execute(
                """
                UPDATE benchmark_jobs SET state = ?, revision = ?, record = ?
                WHERE run_id = ?
                """,
                (updated["state"], updated["revision"], _json_dumps(updated), run),
            )
            connection.commit()
        return updated

    def _artifact_path(self, record: Mapping[str, Any]) -> str:
        spec = record["spec"]
        return resolve_owned_run_path(
            self.run_root,
            ownership_id=spec["ownership_id"],
            run_id=spec["run_id"],
            relative="artifact.json",
        )

    def _write_artifact(
        self, record: Mapping[str, Any], artifact: Mapping[str, Any]
    ) -> dict[str, Any]:
        path = self._artifact_path(record)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, artifact)
        return {
            "schema": artifact["schema"],
            "path": "artifact.json",
            "sha256": hashlib.sha256(canonical_json_bytes(artifact)).hexdigest(),
        }

    @staticmethod
    def _decode_record(raw: str) -> dict[str, Any]:
        try:
            return validate_job_record(_strict_json_loads(raw))
        except (TypeError, ValueError) as exc:
            raise BenchmarkJobError(
                "bad_job_record", "persisted benchmark job is invalid"
            ) from exc

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
                CREATE TABLE IF NOT EXISTS benchmark_jobs (
                    run_id TEXT PRIMARY KEY,
                    spec_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    record TEXT NOT NULL
                )
                """
            )
            yield connection
        finally:
            connection.close()


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
        _snapshot_clock: Clock = time.monotonic,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("idempotency database path must be a non-empty string")
        if retention_seconds <= 0:
            raise ValueError("idempotency retention must be positive")
        if max_records < 1:
            raise ValueError("idempotency max records must be positive")
        if max_result_bytes < 1:
            raise ValueError("idempotency max result bytes must be positive")
        if not callable(_snapshot_clock):
            raise ValueError("operation snapshot clock must be callable")
        self.path = path
        self.retention_seconds = float(retention_seconds)
        self.max_records = int(max_records)
        self.max_result_bytes = int(max_result_bytes)
        self._clock = clock
        self._snapshot_clock = _snapshot_clock
        self._lock = threading.RLock()
        self._active_keys: set[str] = set()
        self._lease_owner = uuid.uuid4().hex

    def list_workloads(
        self, host: str, query: WorkloadQuery, now: datetime,
    ) -> SourceResult:
        """Return one bounded, read-only controller operation snapshot.

        This deliberately bypasses every operation lifecycle helper: a workload
        observation neither expires nor recovers controller work.
        """
        if not isinstance(query, WorkloadQuery):
            raise WorkloadError(
                WorkloadErrorCode.INVALID, "workload query has the wrong type",
            )
        collected = normalize_workload_timestamp(now)
        workload_id(
            host, WorkloadKind.CONTROLLER_OPERATION, WorkloadOwner.CONTROLLER,
            "validation",
        )
        if (
            (query.owner is not None and query.owner is not WorkloadOwner.CONTROLLER)
            or (
                query.kind is not None
                and query.kind is not WorkloadKind.CONTROLLER_OPERATION
            )
            or (query.host is not None and query.host != host)
            or (
                query.state is not None
                and query.state not in {
                    WorkloadState.RUNNING,
                    WorkloadState.TERMINAL,
                    WorkloadState.UNSUPPORTED,
                }
            )
            or (
                query.active_only
                and query.state is not None
                and query.state is not WorkloadState.RUNNING
            )
        ):
            return _empty_source(WorkloadOwner.CONTROLLER, collected)

        try:
            future_cutoff = format_workload_timestamp(
                collected + timedelta(seconds=MAX_FUTURE_SECONDS),
            )
            freshness_cutoff = format_workload_timestamp(
                collected - timedelta(seconds=30),
            )
            recent_cutoff = format_workload_timestamp(
                collected - timedelta(seconds=query.recent_seconds),
            )
        except (OverflowError, WorkloadError):
            raise WorkloadError(
                WorkloadErrorCode.INVALID,
                "workload collection time is outside the supported range",
            ) from None
        cap = min(query.limit, SOURCE_LIMIT)
        state_code = _query_operation_state(query.state)
        parameters: tuple[object, ...] = (
            _UNKNOWN_STATE,
            _UNKNOWN_STATE,
            future_cutoff,
            future_cutoff,
            future_cutoff,
            state_code,
            state_code,
            int(query.active_only),
            freshness_cutoff,
            int(query.active_only),
            freshness_cutoff,
            recent_cutoff,
            cap + 1,
        )
        try:
            deadline = self._snapshot_clock() + _STORE_SNAPSHOT_SECONDS
        except Exception:
            return _unavailable_source(WorkloadOwner.CONTROLLER, collected)
        remaining = _remaining(deadline, self._snapshot_clock)
        if remaining <= 0:
            return _unavailable_source(WorkloadOwner.CONTROLLER, collected)
        try:
            acquired = self._lock.acquire(timeout=remaining)
        except Exception:
            acquired = False
        if not acquired:
            return _unavailable_source(WorkloadOwner.CONTROLLER, collected)
        try:
            rows = _read_snapshot_rows(
                self.path,
                sql=_OPERATION_WORKLOAD_SQL,
                parameters=parameters,
                deadline=deadline,
                monotonic=self._snapshot_clock,
                functions=(
                    ("anvil_operation_timestamp", 1, _canonical_operation_timestamp),
                    (
                        "anvil_operation_digest", 2,
                        lambda rowid, created: _operation_digest(host, rowid, created),
                    ),
                ),
            )
        except Exception:
            return _unavailable_source(WorkloadOwner.CONTROLLER, collected)
        finally:
            self._lock.release()

        extra = len(rows) > cap
        records: list[WorkloadRecord] = []
        error: WorkloadErrorCode | None = None
        for row in rows[:cap]:
            invalid_code = row["invalid_code"]
            if invalid_code:
                code = (
                    WorkloadErrorCode.FUTURE
                    if invalid_code == 2
                    else WorkloadErrorCode.INVALID
                )
                if error is not WorkloadErrorCode.FUTURE:
                    error = code
                continue
            try:
                state, phase, outcome = map_store_state(
                    WorkloadOwner.CONTROLLER, row["state_code"],
                )
                record = WorkloadRecord(
                    id=row["workload_digest"],
                    kind=WorkloadKind.CONTROLLER_OPERATION,
                    owner=WorkloadOwner.CONTROLLER,
                    host=host,
                    state=state,
                    phase=phase,
                    outcome=outcome,
                    created_at=parse_workload_timestamp(row["created_at"]),
                    updated_at=parse_workload_timestamp(row["updated_at"]),
                    source_timestamp=parse_workload_timestamp(row["source_at"]),
                    source_authority=SourceAuthority.CONTROLLER_STORE,
                    observation_quality=ObservationQuality.RECORDED,
                )
                validate_source_records(
                    (record,),
                    owner=WorkloadOwner.CONTROLLER,
                    host=host,
                    collection_timestamp=collected,
                )
            except WorkloadError as exc:
                if error is not WorkloadErrorCode.FUTURE:
                    error = exc.code
                continue
            records.append(record)

        try:
            canonical_records, canonical_truncation = select_records(
                tuple(records), query, now=collected,
            )
        except Exception:
            return _unavailable_source(WorkloadOwner.CONTROLLER, collected)
        if canonical_records != tuple(records) or canonical_truncation.omitted != 0:
            return _unavailable_source(WorkloadOwner.CONTROLLER, collected)
        partial = extra or error is not None
        try:
            return SourceResult(
                owner=WorkloadOwner.CONTROLLER,
                status=ResultStatus.PARTIAL if partial else ResultStatus.COMPLETE,
                collection_timestamp=collected,
                records=canonical_records,
                truncation=Truncation(len(records), None if partial else 0),
                error=error,
            )
        except Exception:
            return _unavailable_source(WorkloadOwner.CONTROLLER, collected)

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
                CREATE TABLE IF NOT EXISTS tombstones (
                    idempotency_key TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
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
        self._purge_tombstones(connection, now)
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
        connection.executemany(
            """
            INSERT INTO tombstones (idempotency_key, fingerprint, expires_at)
            VALUES (?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
                fingerprint = excluded.fingerprint,
                expires_at = excluded.expires_at
            """,
            (
                (row["idempotency_key"], row["fingerprint"], now + self.retention_seconds)
                for row in rows
            ),
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
        self._purge_tombstones(connection, self._clock())
        row = connection.execute(
            "SELECT fingerprint FROM tombstones WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if row is None:
            return False
        if fingerprint is None:
            return True
        return row["fingerprint"] == fingerprint

    @staticmethod
    def _purge_tombstones(connection: sqlite3.Connection, now: float) -> None:
        connection.execute("DELETE FROM tombstones WHERE expires_at <= ?", (now,))
