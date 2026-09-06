"""Bounded, immutable workload visibility schema and query helpers.

This module deliberately performs no I/O. Collectors own observation; these
types only validate, aggregate, serialize, and select observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import InitVar, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from itertools import islice
from typing import Any

SCHEMA = "anvil-workloads/v1"
SOURCE_LIMIT = 200
AGGREGATE_LIMIT = 1000
MAX_SOURCES_PER_NODE = 6
MAX_NODES = 1000
DEFAULT_QUERY_LIMIT = 200
DEFAULT_RECENT_SECONDS = 3600
DEFAULT_STALE_AFTER_SECONDS = 30
MAX_FUTURE_SECONDS = 30
MAX_COUNT = 1_000_000_000
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_TEXT_LENGTH = 1024
_MAX_JSON_DEPTH = 32
_TIMESTAMP_RE = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
# Same identifier grammar as topology._ID_RE, without importing topology I/O.
_HOST_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}", re.ASCII)
_DIGEST_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)


class WorkloadErrorCode(str, Enum):
    INVALID = "invalid-workload"
    UNSUPPORTED = "unsupported-workload"
    UNAVAILABLE = "workload-source-unavailable"
    FUTURE = "future-workload-timestamp"


class WorkloadError(ValueError):
    """Safe workload error whose message never contains input material."""

    def __init__(self, code: WorkloadErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class WorkloadKind(str, Enum):
    ROUTER_REQUEST = "router-request"
    CONTROLLER_OPERATION = "controller-operation"
    BENCHMARK_JOB = "benchmark-job"
    MEDIA_JOB = "media-job"
    RECIPE_SERVE = "recipe-serve"


class WorkloadOwner(str, Enum):
    ROUTER = "router"
    CONTROLLER = "controller"
    BENCHMARK = "benchmark"
    MEDIA = "media"
    RECIPE = "recipe"
    MANIFEST = "manifest"


class WorkloadState(str, Enum):
    CHECKING = "checking"
    ADMITTED = "admitted"
    DISPATCHED = "dispatched"
    STREAMING = "streaming"
    QUEUED = "queued"
    RUNNING = "running"
    TERMINAL = "terminal"
    CONFIGURED = "configured"
    ABSENT = "absent"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


class WorkloadPhase(str, Enum):
    CHECKING = "checking"
    ADMITTED = "admitted"
    DISPATCHED = "dispatched"
    STREAMING = "streaming"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AWAITING_APPROVAL = "awaiting-approval"
    PREPARING = "preparing"
    SUBMITTING = "submitting"
    CONFIGURED = "configured"
    ABSENT = "absent"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


class WorkloadOutcome(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    DISCONNECTED = "disconnected"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class SourceAuthority(str, Enum):
    ROUTER_MEMORY = "router-memory"
    CONTROLLER_STORE = "controller-store"
    BENCHMARK_STORE = "benchmark-store"
    MEDIA_STORE = "media-store"
    MANAGED_STATUS = "managed-status"


class ObservationQuality(str, Enum):
    RECORDED = "recorded"
    CONFIGURED = "configured"
    OBSERVED_RUNNING = "observed-running"
    HEALTHY_IDENTITY = "healthy-identity"
    STALE = "stale"
    ABSENT = "absent"
    INSPECTION_ERROR = "inspection-error"


class ResultStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


_OWNER_KINDS = {
    WorkloadOwner.ROUTER: WorkloadKind.ROUTER_REQUEST,
    WorkloadOwner.CONTROLLER: WorkloadKind.CONTROLLER_OPERATION,
    WorkloadOwner.BENCHMARK: WorkloadKind.BENCHMARK_JOB,
    WorkloadOwner.MEDIA: WorkloadKind.MEDIA_JOB,
    WorkloadOwner.RECIPE: WorkloadKind.RECIPE_SERVE,
    WorkloadOwner.MANIFEST: WorkloadKind.RECIPE_SERVE,
}
_OWNER_AUTHORITIES = {
    WorkloadOwner.ROUTER: SourceAuthority.ROUTER_MEMORY,
    WorkloadOwner.CONTROLLER: SourceAuthority.CONTROLLER_STORE,
    WorkloadOwner.BENCHMARK: SourceAuthority.BENCHMARK_STORE,
    WorkloadOwner.MEDIA: SourceAuthority.MEDIA_STORE,
    WorkloadOwner.RECIPE: SourceAuthority.MANAGED_STATUS,
    WorkloadOwner.MANIFEST: SourceAuthority.MANAGED_STATUS,
}
_STATE_DETAILS = {
    WorkloadState.CHECKING: {(WorkloadPhase.CHECKING, None)},
    WorkloadState.ADMITTED: {(WorkloadPhase.ADMITTED, None)},
    WorkloadState.DISPATCHED: {(WorkloadPhase.DISPATCHED, None)},
    WorkloadState.STREAMING: {(WorkloadPhase.STREAMING, None)},
    WorkloadState.QUEUED: {
        (WorkloadPhase.QUEUED, None),
        (WorkloadPhase.AWAITING_APPROVAL, None),
    },
    WorkloadState.RUNNING: {
        (WorkloadPhase.RUNNING, None),
        (WorkloadPhase.PREPARING, None),
        (WorkloadPhase.SUBMITTING, None),
    },
    WorkloadState.TERMINAL: {
        (WorkloadPhase.COMPLETED, WorkloadOutcome.SUCCESS),
        (WorkloadPhase.FAILED, WorkloadOutcome.ERROR),
        (WorkloadPhase.CANCELLED, WorkloadOutcome.CANCELLED),
        (WorkloadPhase.FAILED, WorkloadOutcome.TIMEOUT),
        (WorkloadPhase.FAILED, WorkloadOutcome.REJECTED),
        (WorkloadPhase.FAILED, WorkloadOutcome.DISCONNECTED),
    },
    WorkloadState.CONFIGURED: {(WorkloadPhase.CONFIGURED, None)},
    WorkloadState.ABSENT: {(WorkloadPhase.ABSENT, None)},
    WorkloadState.UNAVAILABLE: {(WorkloadPhase.UNAVAILABLE, WorkloadOutcome.UNAVAILABLE)},
    WorkloadState.UNSUPPORTED: {(WorkloadPhase.UNSUPPORTED, WorkloadOutcome.UNKNOWN)},
}
_ACTIVE_STATES = {
    WorkloadState.CHECKING, WorkloadState.ADMITTED, WorkloadState.DISPATCHED,
    WorkloadState.STREAMING, WorkloadState.QUEUED, WorkloadState.RUNNING,
}
_CURRENT_STATES = {
    WorkloadState.CONFIGURED, WorkloadState.ABSENT, WorkloadState.UNAVAILABLE,
    WorkloadState.UNSUPPORTED,
}
_STORE_OWNERS = {
    WorkloadOwner.ROUTER, WorkloadOwner.CONTROLLER, WorkloadOwner.BENCHMARK,
    WorkloadOwner.MEDIA,
}
_MANAGED_OWNERS = {WorkloadOwner.RECIPE, WorkloadOwner.MANIFEST}
_MANAGED_QUALITIES = {
    WorkloadState.CONFIGURED: {ObservationQuality.CONFIGURED},
    WorkloadState.RUNNING: {
        ObservationQuality.OBSERVED_RUNNING, ObservationQuality.HEALTHY_IDENTITY,
    },
    WorkloadState.ABSENT: {ObservationQuality.ABSENT},
    WorkloadState.UNAVAILABLE: {ObservationQuality.INSPECTION_ERROR},
    WorkloadState.UNSUPPORTED: {ObservationQuality.INSPECTION_ERROR},
}
_MANAGED_STALE_STATES = {
    WorkloadState.CONFIGURED, WorkloadState.RUNNING, WorkloadState.ABSENT,
}
_OWNER_STATES = {
    WorkloadOwner.ROUTER: {
        WorkloadState.CHECKING,
        WorkloadState.ADMITTED,
        WorkloadState.DISPATCHED,
        WorkloadState.STREAMING,
        WorkloadState.TERMINAL,
        WorkloadState.UNSUPPORTED,
    },
    WorkloadOwner.CONTROLLER: {
        WorkloadState.RUNNING,
        WorkloadState.TERMINAL,
        WorkloadState.UNSUPPORTED,
    },
    WorkloadOwner.BENCHMARK: {
        WorkloadState.QUEUED,
        WorkloadState.RUNNING,
        WorkloadState.TERMINAL,
        WorkloadState.UNSUPPORTED,
    },
    WorkloadOwner.MEDIA: {
        WorkloadState.QUEUED,
        WorkloadState.RUNNING,
        WorkloadState.TERMINAL,
        WorkloadState.UNSUPPORTED,
    },
    WorkloadOwner.RECIPE: {
        WorkloadState.CONFIGURED,
        WorkloadState.RUNNING,
        WorkloadState.ABSENT,
        WorkloadState.UNAVAILABLE,
        WorkloadState.UNSUPPORTED,
    },
    WorkloadOwner.MANIFEST: {
        WorkloadState.CONFIGURED,
        WorkloadState.RUNNING,
        WorkloadState.ABSENT,
        WorkloadState.UNAVAILABLE,
        WorkloadState.UNSUPPORTED,
    },
}


def _invalid(message: str) -> WorkloadError:
    return WorkloadError(WorkloadErrorCode.INVALID, message)


def _strict_int(value: object, *, minimum: int, maximum: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise _invalid(f"{field} is outside the supported range")
    return value


def _enum(enum_type: type[Enum], value: object, *, field: str) -> Any:
    if not isinstance(value, str):
        raise _invalid(f"{field} must be a string")
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        raise _invalid(f"{field} is invalid") from None


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(f"{field} must be a non-empty string")
    if len(value) > MAX_TEXT_LENGTH:
        raise _invalid(f"{field} is too long")
    return value


def _host(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if _HOST_RE.fullmatch(text) is None:
        raise _invalid(f"{field} must be a safe topology identifier")
    return text


def _digest(value: object) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise _invalid("workload.id must be a canonical digest")
    return value


@dataclass(frozen=True, slots=True)
class Progress:
    completed: int
    total: int | None = None
    unit: str = "items"

    def __post_init__(self) -> None:
        _strict_int(self.completed, minimum=0, maximum=MAX_COUNT, field="progress.completed")
        if self.total is not None:
            _strict_int(self.total, minimum=0, maximum=MAX_COUNT, field="progress.total")
            if self.completed > self.total:
                raise _invalid("progress.completed cannot exceed progress.total")
        if not isinstance(self.unit, str) or self.unit not in {"items", "requests", "steps"}:
            raise _invalid("progress.unit is invalid")


@dataclass(frozen=True, slots=True)
class Freshness:
    age_seconds: float
    is_stale: bool
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    force_managed_stale: InitVar[bool] = False

    def __post_init__(self, force_managed_stale: bool) -> None:
        if isinstance(self.age_seconds, bool) or not isinstance(self.age_seconds, (int, float)):
            raise _invalid("freshness.age_seconds must be a number")
        try:
            age = float(self.age_seconds)
        except (OverflowError, ValueError):
            raise _invalid("freshness.age_seconds must be finite and nonnegative") from None
        if not math.isfinite(age) or age < 0:
            raise _invalid("freshness.age_seconds must be finite and nonnegative")
        if not isinstance(self.is_stale, bool):
            raise _invalid("freshness.is_stale must be a boolean")
        if not isinstance(force_managed_stale, bool):
            raise _invalid("freshness managed-stale marker must be a boolean")
        threshold = _strict_int(
            self.stale_after_seconds, minimum=1, maximum=86400,
            field="freshness.stale_after_seconds",
        )
        expected = age > threshold
        if self.is_stale != expected and not (force_managed_stale and self.is_stale):
            raise _invalid("freshness stale marker is inconsistent with its age")


@dataclass(frozen=True, slots=True)
class Truncation:
    returned: int
    omitted: int | None

    def __post_init__(self) -> None:
        _strict_int(self.returned, minimum=0, maximum=AGGREGATE_LIMIT, field="truncation.returned")
        if self.omitted is not None:
            _strict_int(self.omitted, minimum=0, maximum=MAX_COUNT, field="truncation.omitted")


@dataclass(frozen=True, slots=True)
class WorkloadRecord:
    id: str
    kind: WorkloadKind
    owner: WorkloadOwner
    host: str
    state: WorkloadState
    phase: WorkloadPhase
    outcome: WorkloadOutcome | None
    created_at: datetime
    updated_at: datetime
    source_timestamp: datetime
    source_authority: SourceAuthority
    observation_quality: ObservationQuality
    progress: Progress | None = None

    def __post_init__(self) -> None:
        _digest(self.id)
        _host(self.host, field="workload.host")
        for name, enum_type in (
            ("kind", WorkloadKind), ("owner", WorkloadOwner),
            ("state", WorkloadState), ("phase", WorkloadPhase),
            ("source_authority", SourceAuthority),
            ("observation_quality", ObservationQuality),
        ):
            if not isinstance(getattr(self, name), enum_type):
                raise _invalid(f"workload.{name} has the wrong type")
        if self.outcome is not None and not isinstance(self.outcome, WorkloadOutcome):
            raise _invalid("workload.outcome has the wrong type")
        if self.kind is not _OWNER_KINDS[self.owner]:
            raise _invalid("workload owner and kind are incompatible")
        if self.state not in _OWNER_STATES[self.owner]:
            raise _invalid("workload owner and state are incompatible")
        if (self.phase, self.outcome) not in _STATE_DETAILS[self.state]:
            raise _invalid("workload state, phase, and outcome are incompatible")
        if self.phase in {WorkloadPhase.PREPARING, WorkloadPhase.SUBMITTING, WorkloadPhase.AWAITING_APPROVAL} and self.owner is not WorkloadOwner.MEDIA:
            raise _invalid("workload owner and phase are incompatible")
        if self.state is WorkloadState.TERMINAL:
            allowed = {WorkloadOutcome.SUCCESS, WorkloadOutcome.ERROR, WorkloadOutcome.CANCELLED}
            if self.owner is WorkloadOwner.CONTROLLER:
                allowed = {WorkloadOutcome.SUCCESS, WorkloadOutcome.ERROR}
            if self.owner is not WorkloadOwner.ROUTER and self.outcome not in allowed:
                raise _invalid("workload owner and terminal outcome are incompatible")
        if self.source_authority is not _OWNER_AUTHORITIES[self.owner]:
            raise _invalid("workload owner and source authority are incompatible")
        if not _quality_allowed(self.owner, self.state, self.observation_quality):
            raise _invalid("workload state and observation quality are incompatible")
        created = _normalize_datetime(self.created_at, field="workload.created_at")
        updated = _normalize_datetime(self.updated_at, field="workload.updated_at")
        source = _normalize_datetime(self.source_timestamp, field="workload.source_timestamp")
        if created > updated:
            raise _invalid("workload.created_at cannot follow workload.updated_at")
        if self.owner in _STORE_OWNERS and updated > source:
            raise _invalid("workload.updated_at cannot follow workload.source_timestamp")
        # Managed lifecycle timestamps can come from a different component
        # clock. Preserve its documented skew allowance, not an extra receipt
        # allowance: node/fleet composition checks every timestamp again.
        if self.owner in _MANAGED_OWNERS and updated - source > timedelta(seconds=MAX_FUTURE_SECONDS):
            raise WorkloadError(WorkloadErrorCode.FUTURE, "managed workload timestamp is too far beyond observation")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "updated_at", updated)
        object.__setattr__(self, "source_timestamp", source)
        if self.progress is not None and not isinstance(self.progress, Progress):
            raise _invalid("workload.progress has the wrong type")

    @property
    def label(self) -> str:
        return self.kind.value.replace("-", " ").title()

    def freshness(
        self, now: datetime, *, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    ) -> Freshness:
        normalized_now = _normalize_datetime(now, field="now")
        age = max(0.0, (normalized_now - self.source_timestamp).total_seconds())
        forced = self.owner in _MANAGED_OWNERS and self.observation_quality is ObservationQuality.STALE
        return Freshness(
            age_seconds=age,
            is_stale=forced or age > stale_after_seconds,
            stale_after_seconds=stale_after_seconds,
            force_managed_stale=forced,
        )


@dataclass(frozen=True, slots=True)
class WorkloadQuery:
    owner: WorkloadOwner | None = None
    kind: WorkloadKind | None = None
    state: WorkloadState | None = None
    host: str | None = None
    active_only: bool = False
    recent_seconds: int = DEFAULT_RECENT_SECONDS
    limit: int = DEFAULT_QUERY_LIMIT

    def __post_init__(self) -> None:
        for name, enum_type in (("owner", WorkloadOwner), ("kind", WorkloadKind), ("state", WorkloadState)):
            value = getattr(self, name)
            if value is not None and not isinstance(value, enum_type):
                raise _invalid(f"query.{name} has the wrong type")
        if self.host is not None:
            _host(self.host, field="query.host")
        if not isinstance(self.active_only, bool):
            raise _invalid("query.active_only must be a boolean")
        _strict_int(self.recent_seconds, minimum=1, maximum=86400, field="query.recent_seconds")
        _strict_int(self.limit, minimum=1, maximum=AGGREGATE_LIMIT, field="query.limit")


@dataclass(frozen=True, slots=True)
class SourceResult:
    owner: WorkloadOwner
    status: ResultStatus
    collection_timestamp: datetime
    records: tuple[WorkloadRecord, ...]
    truncation: Truncation
    error: WorkloadErrorCode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.owner, WorkloadOwner) or not isinstance(self.status, ResultStatus):
            raise _invalid("source owner or status has the wrong type")
        collected = _normalize_datetime(self.collection_timestamp, field="source.collection_timestamp")
        object.__setattr__(self, "collection_timestamp", collected)
        if not isinstance(self.records, tuple) or len(self.records) > SOURCE_LIMIT:
            raise _invalid("source records exceed the supported bound")
        if not isinstance(self.truncation, Truncation) or self.truncation.returned != len(self.records):
            raise _invalid("source truncation does not match returned records")
        if self.error is not None and not isinstance(self.error, WorkloadErrorCode):
            raise _invalid("source error has the wrong type")
        if self.status is ResultStatus.COMPLETE and (self.error is not None or self.truncation.omitted != 0):
            raise _invalid("complete sources require no error and no omissions")
        if self.status is ResultStatus.PARTIAL and self.error is None and self.truncation.omitted == 0:
            raise _invalid("partial sources require an error or incomplete results")
        if self.status is ResultStatus.UNAVAILABLE and (self.records or self.error is None):
            raise _invalid("unavailable sources require an error and no records")
        ids: set[str] = set()
        for record in self.records:
            if not isinstance(record, WorkloadRecord) or record.owner is not self.owner:
                raise _invalid("source records do not match their owner")
            if record.id in ids:
                raise _invalid("source workload ids must be unique")
            ids.add(record.id)
            _validate_record_time(record, collected)


@dataclass(frozen=True, slots=True)
class NodeResult:
    host: str
    status: ResultStatus
    collection_timestamp: datetime
    sources: tuple[SourceResult, ...]

    def __post_init__(self) -> None:
        _host(self.host, field="node.host")
        if not isinstance(self.status, ResultStatus):
            raise _invalid("node.status has the wrong type")
        collected = _normalize_datetime(self.collection_timestamp, field="node.collection_timestamp")
        object.__setattr__(self, "collection_timestamp", collected)
        if not isinstance(self.sources, tuple) or not 1 <= len(self.sources) <= MAX_SOURCES_PER_NODE:
            raise _invalid("node sources exceed the supported bound")
        owners: set[WorkloadOwner] = set()
        total = 0
        for source in self.sources:
            if not isinstance(source, SourceResult):
                raise _invalid("node sources have the wrong type")
            if source.owner in owners:
                raise _invalid("node source owners must be unique")
            owners.add(source.owner)
            if source.collection_timestamp - collected > timedelta(seconds=MAX_FUTURE_SECONDS):
                raise WorkloadError(WorkloadErrorCode.FUTURE, "source collection timestamp is too far in the future")
            for record in source.records:
                if record.host != self.host:
                    raise _invalid("source record host does not match node host")
                _validate_record_time(record, collected)
            total += len(source.records)
            if total > AGGREGATE_LIMIT:
                raise _invalid("node records exceed the aggregate bound")
        if self.status is not _combined_status(tuple(source.status for source in self.sources)):
            raise _invalid("node status does not match source availability")


@dataclass(frozen=True, slots=True)
class FleetResult:
    status: ResultStatus
    collection_timestamp: datetime
    nodes: tuple[NodeResult, ...]
    truncation: Truncation

    def __post_init__(self) -> None:
        if not isinstance(self.status, ResultStatus):
            raise _invalid("fleet.status has the wrong type")
        collected = _normalize_datetime(self.collection_timestamp, field="fleet.collection_timestamp")
        object.__setattr__(self, "collection_timestamp", collected)
        if not isinstance(self.nodes, tuple) or len(self.nodes) > MAX_NODES:
            raise _invalid("fleet nodes exceed the supported bound")
        if not isinstance(self.truncation, Truncation):
            raise _invalid("fleet truncation has the wrong type")
        hosts: set[str] = set()
        total = 0
        for node in self.nodes:
            if not isinstance(node, NodeResult):
                raise _invalid("fleet nodes have the wrong type")
            if node.host in hosts:
                raise _invalid("fleet node hosts must be unique")
            hosts.add(node.host)
            if node.collection_timestamp - collected > timedelta(seconds=MAX_FUTURE_SECONDS):
                raise WorkloadError(WorkloadErrorCode.FUTURE, "node collection timestamp is too far in the future")
            # Provenance clocks do not grant another skew allowance per layer.
            for source in node.sources:
                if source.collection_timestamp - collected > timedelta(seconds=MAX_FUTURE_SECONDS):
                    raise WorkloadError(WorkloadErrorCode.FUTURE, "source collection timestamp is too far in the future")
                for record in source.records:
                    _validate_record_time(record, collected)
            total += sum(len(source.records) for source in node.sources)
            if total > AGGREGATE_LIMIT:
                raise _invalid("fleet records exceed the aggregate bound")
        if self.truncation.returned != total:
            raise _invalid("fleet truncation does not match returned records")
        expected = _combined_status(tuple(node.status for node in self.nodes)) if self.nodes else ResultStatus.COMPLETE
        if expected is ResultStatus.COMPLETE and self.truncation.omitted != 0:
            expected = ResultStatus.PARTIAL
        if self.status is not expected:
            raise _invalid("fleet status does not match node availability")


def _quality_allowed(owner: WorkloadOwner, state: WorkloadState, quality: ObservationQuality) -> bool:
    if owner in _STORE_OWNERS:
        return quality is ObservationQuality.RECORDED
    return quality in _MANAGED_QUALITIES.get(state, set()) or (
        quality is ObservationQuality.STALE and state in _MANAGED_STALE_STATES
    )


def _combined_status(statuses: tuple[ResultStatus, ...]) -> ResultStatus:
    if statuses and all(value is ResultStatus.UNAVAILABLE for value in statuses):
        return ResultStatus.UNAVAILABLE
    if any(value is not ResultStatus.COMPLETE for value in statuses):
        return ResultStatus.PARTIAL
    return ResultStatus.COMPLETE


def parse_workload_query(values: Mapping[str, object] | Sequence[Sequence[object]]) -> WorkloadQuery:
    """Parse a bounded query mapping or bounded sequence of key/value pairs."""
    pairs = _bounded_query_pairs(values)
    raw: dict[str, object] = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise _invalid("query keys must be strings")
        if key in raw:
            raise _invalid("query keys must be unique")
        raw[key] = value
    allowed = {"owner", "kind", "state", "host", "active_only", "recent_seconds", "limit"}
    if set(raw) - allowed:
        raise _invalid("query contains an unsupported field")
    return WorkloadQuery(
        owner=_enum(WorkloadOwner, raw["owner"], field="query.owner") if "owner" in raw else None,
        kind=_enum(WorkloadKind, raw["kind"], field="query.kind") if "kind" in raw else None,
        state=_enum(WorkloadState, raw["state"], field="query.state") if "state" in raw else None,
        host=_host(raw["host"], field="query.host") if "host" in raw else None,
        active_only=_parse_bool(raw.get("active_only", False), "query.active_only"),
        recent_seconds=_strict_int(raw.get("recent_seconds", DEFAULT_RECENT_SECONDS), minimum=1, maximum=86400, field="query.recent_seconds"),
        limit=_strict_int(raw.get("limit", DEFAULT_QUERY_LIMIT), minimum=1, maximum=AGGREGATE_LIMIT, field="query.limit"),
    )


def workload_id(host: str, kind: WorkloadKind, owner: WorkloadOwner, native_id: str) -> str:
    """Hash only owner-generated identity, never payloads or credentials."""
    _host(host, field="workload.host")
    if not isinstance(owner, WorkloadOwner) or not isinstance(kind, WorkloadKind):
        raise _invalid("workload id owner or kind has the wrong type")
    if kind is not _OWNER_KINDS[owner]:
        raise _invalid("workload id owner and kind are incompatible")
    text = _text(native_id, field="native workload id")
    try:
        material = json.dumps([host, kind.value, owner.value, text], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeError:
        raise _invalid("native workload id is not valid UTF-8") from None
    return hashlib.sha256(material).hexdigest()


def map_unknown_owner_state(owner: WorkloadOwner | None = None) -> tuple[WorkloadState, WorkloadPhase, WorkloadOutcome]:
    if owner is not None and not isinstance(owner, WorkloadOwner):
        raise _invalid("unknown-state owner has the wrong type")
    return WorkloadState.UNSUPPORTED, WorkloadPhase.UNSUPPORTED, WorkloadOutcome.UNKNOWN


def map_store_state(
    owner: WorkloadOwner, value: object,
) -> tuple[WorkloadState, WorkloadPhase, WorkloadOutcome | None]:
    """Map a persisted controller or benchmark state without retaining raw text."""
    if owner is WorkloadOwner.CONTROLLER:
        mapping = {
            "running": (WorkloadState.RUNNING, WorkloadPhase.RUNNING, None),
            "succeeded": (
                WorkloadState.TERMINAL, WorkloadPhase.COMPLETED, WorkloadOutcome.SUCCESS,
            ),
            "failed": (
                WorkloadState.TERMINAL, WorkloadPhase.FAILED, WorkloadOutcome.ERROR,
            ),
        }
    elif owner is WorkloadOwner.BENCHMARK:
        mapping = {
            "queued": (WorkloadState.QUEUED, WorkloadPhase.QUEUED, None),
            "running": (WorkloadState.RUNNING, WorkloadPhase.RUNNING, None),
            "completed": (
                WorkloadState.TERMINAL, WorkloadPhase.COMPLETED, WorkloadOutcome.SUCCESS,
            ),
            "failed": (
                WorkloadState.TERMINAL, WorkloadPhase.FAILED, WorkloadOutcome.ERROR,
            ),
            "cancelled": (
                WorkloadState.TERMINAL, WorkloadPhase.CANCELLED,
                WorkloadOutcome.CANCELLED,
            ),
        }
    else:
        raise _invalid("store workload owner is invalid")
    if not isinstance(value, str):
        raise _invalid("store workload state must be a string")
    return mapping.get(value, map_unknown_owner_state(owner))


def select_records(
    records: Sequence[WorkloadRecord], query: WorkloadQuery, *, now: datetime,
    aggregate: bool = False,
) -> tuple[tuple[WorkloadRecord, ...], Truncation]:
    if not isinstance(aggregate, bool):
        raise _invalid("aggregate must be a boolean")
    input_bound = AGGREGATE_LIMIT if aggregate else SOURCE_LIMIT
    output_bound = AGGREGATE_LIMIT if aggregate else SOURCE_LIMIT
    return _select_records_bounded(
        records, query, now=now, input_bound=input_bound, output_bound=output_bound
    )


def _select_records_bounded(
    records: Sequence[WorkloadRecord], query: WorkloadQuery, *, now: datetime,
    input_bound: int, output_bound: int,
) -> tuple[tuple[WorkloadRecord, ...], Truncation]:
    bounded_records = _bounded_records(records, maximum=input_bound)
    if not isinstance(query, WorkloadQuery):
        raise _invalid("query has the wrong type")
    normalized_now = _normalize_datetime(now, field="now")
    selected: list[WorkloadRecord] = []
    for record in bounded_records:
        if not isinstance(record, WorkloadRecord):
            raise _invalid("records contain the wrong type")
        if query.owner is not None and record.owner is not query.owner:
            continue
        if query.kind is not None and record.kind is not query.kind:
            continue
        if query.state is not None and record.state is not query.state:
            continue
        if query.host is not None and record.host != query.host:
            continue
        stale = record.freshness(normalized_now).is_stale
        if query.active_only:
            if record.state not in _ACTIVE_STATES or stale:
                continue
        elif record.owner in _MANAGED_OWNERS and record.observation_quality is ObservationQuality.STALE:
            pass
        elif record.state in _ACTIVE_STATES and not stale:
            pass
        elif record.state in _CURRENT_STATES:
            pass
        elif not (
            record.state is WorkloadState.TERMINAL
            and normalized_now - record.updated_at <= timedelta(seconds=query.recent_seconds)
        ):
            continue
        selected.append(record)
    # Two stable passes avoid lossy float timestamps while preserving ID tie-breaks.
    selected.sort(key=lambda item: item.id)
    selected.sort(key=lambda item: item.updated_at, reverse=True)
    cap = min(query.limit, output_bound)
    returned = tuple(selected[:cap])
    return returned, Truncation(len(returned), len(selected) - len(returned))


def select_managed_records(
    records: Sequence[WorkloadRecord], query: WorkloadQuery, *, now: datetime,
) -> tuple[tuple[WorkloadRecord, ...], Truncation]:
    """Select bounded recipe/manifest observations without widening source defaults."""
    bounded = _bounded_records(records, maximum=512)
    if any(
        not isinstance(record, WorkloadRecord)
        or record.owner not in _MANAGED_OWNERS
        for record in bounded
    ):
        raise _invalid("managed records must be recipe or manifest observations")
    return _select_records_bounded(
        bounded, query, now=now, input_bound=512, output_bound=SOURCE_LIMIT
    )


def validate_source_records(
    records: Sequence[WorkloadRecord], *, owner: WorkloadOwner, host: str,
    collection_timestamp: datetime,
) -> tuple[WorkloadRecord, ...]:
    if not isinstance(owner, WorkloadOwner):
        raise _invalid("source owner has the wrong type")
    _host(host, field="source.host")
    bounded_records = _bounded_records(records, maximum=SOURCE_LIMIT)
    collected = _normalize_datetime(collection_timestamp, field="source.collection_timestamp")
    validated: list[WorkloadRecord] = []
    ids: set[str] = set()
    for record in bounded_records:
        if not isinstance(record, WorkloadRecord):
            raise _invalid("source records contain the wrong type")
        if record.owner is not owner or record.host != host:
            raise _invalid("source record ownership does not match its source")
        if record.id in ids:
            raise _invalid("source workload ids must be unique")
        ids.add(record.id)
        _validate_record_time(record, collected)
        validated.append(record)
    return tuple(validated)


def _bounded_records(records: object, *, maximum: int) -> tuple[object, ...]:
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes, bytearray)):
        raise _invalid("records must be a bounded sequence")
    try:
        count = len(records)
    except Exception:
        raise _invalid("record sequence cannot be read") from None
    if count > maximum:
        raise _invalid("records exceed the supported bound")
    try:
        values = tuple(records[index] for index in range(count))
        stable_size = len(records) == count
    except Exception:
        raise _invalid("record sequence cannot be read") from None
    if not stable_size:
        raise _invalid("record sequence size changed")
    return values


def workload_record_to_dict(record: WorkloadRecord) -> dict[str, object]:
    if not isinstance(record, WorkloadRecord):
        raise _invalid("workload record has the wrong type")
    result: dict[str, object] = {
        "schema": SCHEMA, "id": record.id, "kind": record.kind.value,
        "owner": record.owner.value, "host": record.host, "label": record.label,
        "state": record.state.value, "phase": record.phase.value,
        "created_at": _format_datetime(record.created_at),
        "updated_at": _format_datetime(record.updated_at),
        "source_timestamp": _format_datetime(record.source_timestamp),
        "source_authority": record.source_authority.value,
        "observation_quality": record.observation_quality.value,
    }
    if record.outcome is not None:
        result["outcome"] = record.outcome.value
    if record.progress is not None:
        result["progress"] = {
            "completed": record.progress.completed, "total": record.progress.total,
            "unit": record.progress.unit,
        }
    return result


def workload_record_from_dict(data: Mapping[str, object]) -> WorkloadRecord:
    obj = _object(data, field="workload")
    _check_schema(obj)
    required = {
        "schema", "id", "kind", "owner", "host", "label", "state", "phase",
        "created_at", "updated_at", "source_timestamp", "source_authority",
        "observation_quality",
    }
    _exact_fields(obj, required, {"progress", "outcome"}, field="workload")
    kind = _enum(WorkloadKind, obj["kind"], field="workload.kind")
    if obj["label"] != kind.value.replace("-", " ").title():
        raise _invalid("workload label does not match its kind")
    progress = _progress_from_dict(obj["progress"]) if "progress" in obj else None
    return WorkloadRecord(
        id=_digest(obj["id"]), kind=kind,
        owner=_enum(WorkloadOwner, obj["owner"], field="workload.owner"),
        host=_host(obj["host"], field="workload.host"),
        state=_enum(WorkloadState, obj["state"], field="workload.state"),
        phase=_enum(WorkloadPhase, obj["phase"], field="workload.phase"),
        outcome=_enum(WorkloadOutcome, obj["outcome"], field="workload.outcome") if "outcome" in obj else None,
        created_at=_parse_datetime(obj["created_at"], field="workload.created_at"),
        updated_at=_parse_datetime(obj["updated_at"], field="workload.updated_at"),
        source_timestamp=_parse_datetime(obj["source_timestamp"], field="workload.source_timestamp"),
        source_authority=_enum(SourceAuthority, obj["source_authority"], field="workload.source_authority"),
        observation_quality=_enum(ObservationQuality, obj["observation_quality"], field="workload.observation_quality"),
        progress=progress,
    )


def workload_record_to_json(record: WorkloadRecord) -> str:
    return _canonical_json(workload_record_to_dict(record))


def workload_record_from_json(payload: str | bytes) -> WorkloadRecord:
    return workload_record_from_dict(_load_json_object(payload))


def source_result_to_dict(result: SourceResult) -> dict[str, object]:
    if not isinstance(result, SourceResult):
        raise _invalid("source result has the wrong type")
    return {
        "schema": SCHEMA, "owner": result.owner.value, "status": result.status.value,
        "collection_timestamp": _format_datetime(result.collection_timestamp),
        "records": [workload_record_to_dict(record) for record in result.records],
        "truncation": _truncation_to_dict(result.truncation),
        "error": result.error.value if result.error is not None else None,
    }


def source_result_from_dict(data: Mapping[str, object]) -> SourceResult:
    obj = _object(data, field="source")
    _check_schema(obj)
    _exact_fields(obj, {"schema", "owner", "status", "collection_timestamp", "records", "truncation", "error"}, set(), field="source")
    records_data = _array(obj["records"], field="source.records", maximum=SOURCE_LIMIT)
    error = obj["error"]
    return SourceResult(
        owner=_enum(WorkloadOwner, obj["owner"], field="source.owner"),
        status=_enum(ResultStatus, obj["status"], field="source.status"),
        collection_timestamp=_parse_datetime(obj["collection_timestamp"], field="source.collection_timestamp"),
        records=tuple(workload_record_from_dict(item) for item in records_data),
        truncation=_truncation_from_dict(obj["truncation"]),
        error=None if error is None else _enum(WorkloadErrorCode, error, field="source.error"),
    )


def source_result_to_json(result: SourceResult) -> str:
    return _canonical_json(source_result_to_dict(result))


def source_result_from_json(payload: str | bytes) -> SourceResult:
    return source_result_from_dict(_load_json_object(payload))


def node_result_to_dict(result: NodeResult) -> dict[str, object]:
    if not isinstance(result, NodeResult):
        raise _invalid("node result has the wrong type")
    return {
        "schema": SCHEMA, "host": result.host, "status": result.status.value,
        "collection_timestamp": _format_datetime(result.collection_timestamp),
        "sources": [source_result_to_dict(source) for source in result.sources],
    }


def node_result_from_dict(data: Mapping[str, object]) -> NodeResult:
    obj = _object(data, field="node")
    _check_schema(obj)
    _exact_fields(obj, {"schema", "host", "status", "collection_timestamp", "sources"}, set(), field="node")
    sources_data = _array(obj["sources"], field="node.sources", maximum=MAX_SOURCES_PER_NODE, minimum=1)
    return NodeResult(
        host=_host(obj["host"], field="node.host"),
        status=_enum(ResultStatus, obj["status"], field="node.status"),
        collection_timestamp=_parse_datetime(obj["collection_timestamp"], field="node.collection_timestamp"),
        sources=tuple(source_result_from_dict(item) for item in sources_data),
    )


def node_result_to_json(result: NodeResult) -> str:
    return _canonical_json(node_result_to_dict(result))


def node_result_from_json(payload: str | bytes) -> NodeResult:
    return node_result_from_dict(_load_json_object(payload))


def fleet_result_to_dict(result: FleetResult) -> dict[str, object]:
    if not isinstance(result, FleetResult):
        raise _invalid("fleet result has the wrong type")
    return {
        "schema": SCHEMA, "status": result.status.value,
        "collection_timestamp": _format_datetime(result.collection_timestamp),
        "nodes": [node_result_to_dict(node) for node in result.nodes],
        "truncation": _truncation_to_dict(result.truncation),
    }


def fleet_result_from_dict(data: Mapping[str, object]) -> FleetResult:
    obj = _object(data, field="fleet")
    _check_schema(obj)
    _exact_fields(obj, {"schema", "status", "collection_timestamp", "nodes", "truncation"}, set(), field="fleet")
    nodes_data = _array(obj["nodes"], field="fleet.nodes", maximum=MAX_NODES)
    return FleetResult(
        status=_enum(ResultStatus, obj["status"], field="fleet.status"),
        collection_timestamp=_parse_datetime(obj["collection_timestamp"], field="fleet.collection_timestamp"),
        nodes=tuple(node_result_from_dict(item) for item in nodes_data),
        truncation=_truncation_from_dict(obj["truncation"]),
    )


def fleet_result_to_json(result: FleetResult) -> str:
    return _canonical_json(fleet_result_to_dict(result))


def fleet_result_from_json(payload: str | bytes) -> FleetResult:
    return fleet_result_from_dict(_load_json_object(payload))


def _bounded_query_pairs(
    values: Mapping[str, object] | Sequence[Sequence[object]],
) -> tuple[tuple[object, object], ...]:
    try:
        count = len(values)
    except Exception:
        raise _invalid("query must be a bounded mapping or sequence") from None
    if count > 7:
        raise _invalid("query has too many fields")
    if isinstance(values, Mapping):
        try:
            pairs = tuple(islice(values.items(), count + 1))
        except Exception:
            raise _invalid("query cannot be read") from None
        if len(pairs) != count or len(pairs) > 7:
            raise _invalid("query mapping length is inconsistent")
        return pairs
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise _invalid("query must be a bounded mapping or sequence")
    checked: list[tuple[object, object]] = []
    try:
        for index in range(count):
            pair = values[index]
            if (
                not isinstance(pair, Sequence)
                or isinstance(pair, (str, bytes, bytearray))
                or len(pair) != 2
            ):
                raise _invalid("query entries must be key/value pairs")
            checked.append((pair[0], pair[1]))
    except WorkloadError:
        raise
    except Exception:
        raise _invalid("query cannot be read") from None
    return tuple(checked)


def _parse_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise _invalid(f"{field} must be a boolean")
    return value


def _progress_from_dict(value: object) -> Progress | None:
    if value is None:
        return None
    obj = _object(value, field="workload.progress")
    _exact_fields(obj, {"completed", "total", "unit"}, set(), field="workload.progress")
    total = obj["total"]
    if total is not None:
        total = _strict_int(total, minimum=0, maximum=MAX_COUNT, field="progress.total")
    return Progress(
        _strict_int(obj["completed"], minimum=0, maximum=MAX_COUNT, field="progress.completed"),
        total,
        _text(obj["unit"], field="progress.unit"),
    )


def _validate_record_time(record: WorkloadRecord, collected: datetime) -> None:
    if any(value - collected > timedelta(seconds=MAX_FUTURE_SECONDS) for value in (record.created_at, record.updated_at, record.source_timestamp)):
        raise WorkloadError(WorkloadErrorCode.FUTURE, "workload timestamp is too far beyond source collection")


def _normalize_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise _invalid(f"{field} must be a datetime")
    try:
        if value.utcoffset() is None:
            raise _invalid(f"{field} must include a timezone")
        return value.astimezone(timezone.utc)
    except WorkloadError:
        raise
    except Exception:
        raise _invalid(f"{field} has an invalid timezone") from None


def _format_datetime(value: datetime) -> str:
    return _normalize_datetime(value, field="timestamp").isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalize_workload_timestamp(value: object) -> datetime:
    """Return one exact UTC workload timestamp with fixed safe diagnostics."""
    return _normalize_datetime(value, field="workload timestamp")


def format_workload_timestamp(value: object) -> str:
    """Encode one workload timestamp in canonical microsecond-Z form."""
    return _format_datetime(normalize_workload_timestamp(value))


def parse_workload_timestamp(value: object) -> datetime:
    """Decode one canonical microsecond-Z workload timestamp."""
    return _parse_datetime(value, field="workload timestamp")


def _parse_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        raise _invalid(f"{field} must use exact UTC microsecond-Z format")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (OverflowError, ValueError):
        raise _invalid(f"{field} is not a valid timestamp") from None


def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{field} must be an object")
    try:
        count = len(value)
    except Exception:
        raise _invalid(f"{field} cannot be read") from None
    if count > 32:
        raise _invalid(f"{field} has too many fields")
    result: dict[str, object] = {}
    try:
        entries = tuple(islice(value.items(), count + 1))
        if len(entries) != count:
            raise _invalid(f"{field} has an inconsistent size")
        for key, item in entries:
            if not isinstance(key, str) or key in result:
                raise _invalid(f"{field} contains invalid fields")
            result[key] = item
    except WorkloadError:
        raise
    except Exception:
        raise _invalid(f"{field} cannot be read") from None
    return result


def _array(value: object, *, field: str, maximum: int, minimum: int = 0) -> list[object]:
    if not isinstance(value, list):
        raise _invalid(f"{field} must be an array")
    if not minimum <= len(value) <= maximum:
        raise _invalid(f"{field} exceeds the supported bound")
    return value


def _exact_fields(data: Mapping[str, object], required: set[str], optional: set[str], *, field: str) -> None:
    keys = set(data)
    if not required <= keys or keys - required - optional:
        raise _invalid(f"{field} fields do not match the schema")


def _check_schema(data: Mapping[str, object]) -> None:
    if data.get("schema") != SCHEMA:
        raise WorkloadError(WorkloadErrorCode.UNSUPPORTED, "workload schema is unsupported")


def _truncation_to_dict(value: Truncation) -> dict[str, int | None]:
    return {"returned": value.returned, "omitted": value.omitted}


def _truncation_from_dict(value: object) -> Truncation:
    obj = _object(value, field="truncation")
    _exact_fields(obj, {"returned", "omitted"}, set(), field="truncation")
    omitted = obj["omitted"]
    if omitted is not None:
        omitted = _strict_int(omitted, minimum=0, maximum=MAX_COUNT, field="truncation.omitted")
    return Truncation(
        _strict_int(obj["returned"], minimum=0, maximum=AGGREGATE_LIMIT, field="truncation.returned"),
        omitted,
    )


def _canonical_json(value: Mapping[str, object]) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        size = len(payload.encode("utf-8"))
    except (TypeError, UnicodeError, ValueError):
        raise _invalid("workload value cannot be encoded") from None
    if size > MAX_JSON_BYTES:
        raise _invalid("workload JSON exceeds the supported bound")
    return payload


def _load_json_object(payload: str | bytes) -> dict[str, object]:
    if not isinstance(payload, (str, bytes)):
        raise _invalid("workload JSON must be text or bytes")
    try:
        raw = payload.encode("utf-8") if isinstance(payload, str) else payload
    except UnicodeError:
        raise _invalid("workload JSON is not valid UTF-8") from None
    if len(raw) > MAX_JSON_BYTES:
        raise _invalid("workload JSON exceeds the supported bound")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise _invalid("workload JSON is invalid") from None
    try:
        if _json_depth(value) > _MAX_JSON_DEPTH:
            raise _invalid("workload JSON is too deeply nested")
    except WorkloadError:
        raise
    except RecursionError:
        raise _invalid("workload JSON is too deeply nested") from None
    return _object(value, field="workload JSON")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _invalid("workload JSON contains duplicate fields")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise _invalid("workload JSON contains a nonfinite number")


def _json_depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 0
