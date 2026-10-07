"""Bounded, metadata-only router workload lifecycle projection.

This module owns active observation state only. ``DecisionLog`` remains the
sole workload decision store; the usage ledger owns accounting terminals.
"""
from __future__ import annotations

from contextlib import contextmanager
import dataclasses
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from anvil_serving.observability.workloads import (
    MAX_COUNT,
    SOURCE_LIMIT,
    ObservationQuality,
    ResultStatus,
    SourceAuthority,
    SourceResult,
    Truncation,
    WorkloadError,
    WorkloadErrorCode,
    WorkloadKind,
    WorkloadOutcome,
    WorkloadOwner,
    WorkloadPhase,
    WorkloadQuery,
    WorkloadRecord,
    WorkloadState,
    format_workload_timestamp,
    normalize_workload_timestamp,
    parse_workload_timestamp,
    select_records,
    validate_source_records,
    workload_id,
)
from anvil_serving.router.decision_log import (
    DecisionLog,
    DecisionRecord,
    safe_client_id,
    safe_gateway_request_id,
    safe_correlation,
)

MAX_ACTIVE_WORKLOADS = 1024
MAX_RECENT_DECISIONS = 512
_SELECTION_CHUNK = 1000
_ACTIVE_PHASES = {
    WorkloadState.CHECKING: WorkloadPhase.CHECKING,
    WorkloadState.ADMITTED: WorkloadPhase.ADMITTED,
    WorkloadState.DISPATCHED: WorkloadPhase.DISPATCHED,
    WorkloadState.STREAMING: WorkloadPhase.STREAMING,
}
_NEXT_PHASE = {
    WorkloadState.CHECKING: WorkloadState.ADMITTED,
    WorkloadState.ADMITTED: WorkloadState.DISPATCHED,
    WorkloadState.DISPATCHED: WorkloadState.STREAMING,
}
_TERMINAL_OUTCOMES = {
    WorkloadOutcome.SUCCESS,
    WorkloadOutcome.ERROR,
    WorkloadOutcome.CANCELLED,
    WorkloadOutcome.TIMEOUT,
    WorkloadOutcome.REJECTED,
    WorkloadOutcome.DISCONNECTED,
}


@dataclass(frozen=True, slots=True)
class _ActiveEntry:
    gateway_request_id: str
    state: WorkloadState
    created_at: datetime
    updated_at: datetime
    diagnostic: tuple = ()
    usage_start: object = None
    usage_route: object = None
    usage_tokens: object = None
    usage_phase: str = "checking"


class RouterWorkloadRegistry:
    """Own bounded active metadata and project it with recent decisions."""

    def __init__(
        self,
        decision_log: DecisionLog,
        *,
        clock: Callable[[], datetime],
        max_active: int = MAX_ACTIVE_WORKLOADS,
    ) -> None:
        if not isinstance(decision_log, DecisionLog):
            raise ValueError("decision_log must be a DecisionLog")
        if not callable(clock):
            raise ValueError("clock must be callable")
        if (
            isinstance(max_active, bool)
            or not isinstance(max_active, int)
            or not 1 <= max_active <= MAX_ACTIVE_WORKLOADS
        ):
            raise ValueError("max_active must be an integer from 1 to 1024")
        self._decision_log = decision_log
        self._clock = clock
        self._max_active = max_active
        self._lock = threading.Lock()
        self._active: dict[str, _ActiveEntry] = {}
        self._usage_revision = 0
        self._usage_mutations = 0
        self._unrepresented = {state: 0 for state in _ACTIVE_PHASES}
        self._finalizing = {state: 0 for state in _ACTIVE_PHASES}

    def begin(self, gateway_request_id: object) -> RouterWorkloadToken:
        """Return an inert token; invalid identity disables observation only."""
        valid_id: str | None = None
        if (
            type(gateway_request_id) is str
            and len(gateway_request_id) == 36
            and safe_gateway_request_id(gateway_request_id) == gateway_request_id
        ):
            valid_id = gateway_request_id
        return RouterWorkloadToken(self, valid_id)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    @property
    def unrepresented_count(self) -> int:
        with self._lock:
            return sum(self._unrepresented.values())

    def observe_request(self, request_id, correlation, route, measurements):
        """Enrich existing active entries with bounded, content-free observations."""
        values = {}
        for key in ("session_id", "client_id"):
            value = safe_correlation(correlation.get(key)) if key == "session_id" else safe_client_id(correlation.get(key))
            if value is not None:
                values[key] = value
        if isinstance(route, str) and len(route) <= 128 and safe_correlation(route):
            values["route"] = route
        phase = measurements.get("phase")
        if phase in {"checking", "queued", "admitted", "dispatched", "streaming"}:
            values["phase"] = phase
        for key in ("elapsed_ms", "last_activity_ms", "admission_wait_ms",
                    "input_tokens", "estimated_input_tokens", "output_tokens", "cache_read_input_tokens",
                    "context_limit_tokens"):
            value = measurements.get(key)
            if type(value) is int and 0 <= value <= MAX_COUNT:
                values[key] = value
        with self._lock:
            entry = self._active.get(request_id)
            if entry is not None:
                self._active[request_id] = dataclasses.replace(entry, diagnostic=tuple(values.items()))
                self._usage_revision += 1

    def attach_usage(self, request_id, start):
        from .usage_store import RequestStart
        if type(start) is not RequestStart:
            raise ValueError("invalid admitted usage metadata")
        with self._lock:
            entry = self._active.get(request_id)
            if entry is not None:
                if entry.usage_start is not None and entry.usage_start != start:
                    raise ValueError("usage metadata conflict")
                self._active[request_id] = dataclasses.replace(entry, usage_start=start,
                    usage_phase="admitted" if entry.usage_start is None else entry.usage_phase)
                self._usage_revision += 1

    @contextmanager
    def usage_mutation(self):
        # No registry lock is held while SQLite waits or writes.
        with self._lock:
            self._usage_mutations += 1
            self._usage_revision += 1
        try:
            yield
        finally:
            with self._lock:
                self._usage_mutations -= 1
                self._usage_revision += 1

    def observe_usage(self, request_id, *, route=None, tokens=None, phase=None):
        from .usage_store import RouteAssociation
        from .decision_log import TokenUsage
        if (route is not None and type(route) is not RouteAssociation or
                tokens is not None and type(tokens) is not TokenUsage or
                phase is not None and phase not in {"checking", "queued", "admitted", "dispatched", "streaming", "finalizing"}):
            raise ValueError("invalid usage observation")
        updated_at = normalize_workload_timestamp(self._clock())
        with self._lock:
            entry = self._active.get(request_id)
            if entry is not None:
                self._active[request_id] = dataclasses.replace(entry,
                    usage_route=route if route is not None else entry.usage_route,
                    usage_tokens=tokens if tokens is not None else entry.usage_tokens,
                    usage_phase=phase or entry.usage_phase,
                    updated_at=max(entry.updated_at, updated_at))
                self._usage_revision += 1

    def usage_snapshot(self):
        """Trusted bounded collector seam, fenced across ledger writes.

        Authorization precedes collection. Read twice around ledger collection;
        refuse on busy or changed revision. Generic views never serialize this.
        """
        with self._lock:
            if self._usage_mutations:
                raise ValueError("usage observation busy")
            return self._usage_revision, tuple(e for e in self._active.values() if e.usage_start is not None), sum(self._unrepresented.values())

    @staticmethod
    def active_usage_page(entries, omitted, now, *, limit=50, filters=()):
        """Closed sensitive projection of the existing immutable snapshot."""
        from .decision_log import TokenDirection, TokenUsage
        from .usage_store import UsageStore, UsageQuery
        UsageQuery(granularity="cumulative", filters=filters)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid active limit")
        now = normalize_workload_timestamp(now)
        timestamp = format_workload_timestamp(now)
        records = []
        for entry in reversed(entries):
            start = entry.usage_start
            tokens = entry.usage_tokens or TokenUsage(
                input=TokenDirection(applicability=start.input_applicability),
                output=TokenDirection(applicability=start.output_applicability))
            dimensions = UsageStore._dimensions(start, tokens, "active", None)
            if any(dimensions[name] != value for name, value in filters):
                continue
            diagnostic_phase = dict(entry.diagnostic).get("phase")
            phase = entry.usage_phase
            if (phase == "admitted" and diagnostic_phase in {"checking", "queued", "admitted"}
                    or phase == "dispatched" and diagnostic_phase == "streaming"):
                phase = diagnostic_phase
            records.append({"gateway_request_id": entry.gateway_request_id,
                "request_id": start.request_id, "caller": start.caller.to_dict(),
                "kind": start.kind, "model": start.model, "accepted_at": start.accepted_at,
                "created_at": format_workload_timestamp(entry.created_at),
                "updated_at": format_workload_timestamp(entry.updated_at),
                "elapsed_ms": max(0, int((now - entry.created_at).total_seconds() * 1000)),
                "last_activity_ms": max(0, int((now - entry.updated_at).total_seconds() * 1000)),
                "phase": phase,
                "route": entry.usage_route.to_dict() if entry.usage_route is not None else None,
                "tokens": tokens.to_dict(), "accounting_status": "in_progress"})
        return {"schema": "router-active-usage/v1", "collected_at": timestamp,
                "source_timestamp": timestamp, "freshness": "fresh", "available": True,
                "records": records[:limit],
                "truncation": {"returned": min(len(records), limit),
                               "omitted": None if omitted else max(0, len(records) - limit),
                               "unrepresented": omitted, "truncated": bool(omitted or len(records) > limit)}}

    def active_requests(self, *, session_id=None, limit=50):
        if session_id is not None and safe_correlation(session_id) != session_id:
            raise ValueError("invalid session identifier")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("invalid request limit")
        with self._lock:
            entries = tuple(self._active.values())
            omitted = sum(self._unrepresented.values())
        records = []
        for entry in reversed(entries):
            fields = dict(entry.diagnostic)
            if session_id is not None and fields.get("session_id") != session_id:
                continue
            records.append({"gateway_request_id": entry.gateway_request_id,
                "state": entry.state.value, "phase": entry.state.value,
                "created_at": format_workload_timestamp(entry.created_at), **fields})
        return {"object": "router_request_history", "scope": "active_workload_buffer",
            "session_id": session_id, "records": records[:limit],
            "truncated": bool(omitted or len(records) > limit)}

    def source_result(
        self,
        host: str,
        query: WorkloadQuery,
        now: datetime,
    ) -> SourceResult:
        """Project active and bounded recent router work for a trusted host."""
        collected = normalize_workload_timestamp(now)
        workload_id(
            host, WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER, "validation"
        )
        if not isinstance(query, WorkloadQuery):
            raise WorkloadError(
                WorkloadErrorCode.INVALID, "workload query has the wrong type"
            )
        with self._lock:
            active = tuple(self._active.values())
            unrepresented = dict(self._unrepresented)
            finalizing = dict(self._finalizing)

        log_failed = False
        history_incomplete = False
        try:
            recent = self._decision_log.recent(MAX_RECENT_DECISIONS)
            if not isinstance(recent, tuple) or len(recent) > MAX_RECENT_DECISIONS:
                raise ValueError("invalid recent decision snapshot")
            history_incomplete = (
                _query_includes_terminal(query, host=host)
                and len(self._decision_log) > len(recent)
            )
        except Exception:
            recent = ()
            log_failed = True

        record_error: WorkloadErrorCode | None = None
        records: dict[str, WorkloadRecord] = {}
        for entry in active:
            try:
                record = validate_source_records(
                    (_active_record(entry, host),),
                    owner=WorkloadOwner.ROUTER,
                    host=host,
                    collection_timestamp=collected,
                )[0]
            except WorkloadError as exc:
                record_error = _record_error(record_error, exc.code)
                continue
            except Exception:
                record_error = _record_error(
                    record_error, WorkloadErrorCode.INVALID
                )
                continue
            records[record.id] = record
        for decision in recent:
            try:
                record = _terminal_record(decision, host)
                if record is not None:
                    record = validate_source_records(
                        (record,),
                        owner=WorkloadOwner.ROUTER,
                        host=host,
                        collection_timestamp=collected,
                    )[0]
            except WorkloadError as exc:
                record_error = _record_error(record_error, exc.code)
                continue
            except Exception:
                record_error = _record_error(
                    record_error, WorkloadErrorCode.INVALID
                )
                continue
            if record is not None:
                records[record.id] = record
            elif isinstance(decision, DecisionRecord) and any(
                value is not None
                for value in (
                    decision.workload_created_at,
                    decision.workload_updated_at,
                    decision.workload_outcome,
                )
            ):
                record_error = _record_error(
                    record_error, WorkloadErrorCode.INVALID
                )

        try:
            selected, selected_omitted = _select_source_records(
                tuple(records.values()), query, now=collected
            )
            matching_anonymous = _matching_anonymous(
                unrepresented, finalizing, query, host=host
            )
        except Exception:
            return _unavailable_source(collected)
        if log_failed or history_incomplete or matching_anonymous is None:
            omitted: int | None = None
        else:
            omitted = min(MAX_COUNT, selected_omitted + matching_anonymous)

        error: WorkloadErrorCode | None = None
        if log_failed:
            error = WorkloadErrorCode.UNAVAILABLE
        elif record_error is not None:
            error = record_error
        if (
            error is WorkloadErrorCode.UNAVAILABLE
            and not selected
            and matching_anonymous == 0
        ):
            return _unavailable_source(collected)
        status = (
            ResultStatus.COMPLETE
            if error is None and omitted == 0
            else ResultStatus.PARTIAL
        )
        return SourceResult(
            owner=WorkloadOwner.ROUTER,
            status=status,
            collection_timestamp=collected,
            records=selected,
            truncation=Truncation(len(selected), omitted),
            error=error,
        )

    def _now(self) -> datetime | None:
        try:
            return normalize_workload_timestamp(self._clock())
        except Exception:
            return None

    def _activate(
        self, gateway_request_id: str, when: datetime
    ) -> tuple[bool, _ActiveEntry] | None:
        entry = _ActiveEntry(
            gateway_request_id=gateway_request_id,
            state=WorkloadState.CHECKING,
            created_at=when,
            updated_at=when,
        )
        with self._lock:
            if gateway_request_id in self._active:
                return None
            represented = len(self._active) < self._max_active
            if represented:
                self._active[gateway_request_id] = entry
                self._usage_revision += 1
            else:
                self._unrepresented[WorkloadState.CHECKING] += 1
                self._usage_revision += 1
            return represented, entry

    def _advance(
        self,
        entry: _ActiveEntry,
        state: WorkloadState,
        when: datetime,
        *,
        represented: bool,
    ) -> _ActiveEntry:
        updated = _ActiveEntry(
            gateway_request_id=entry.gateway_request_id,
            state=state,
            created_at=entry.created_at,
            updated_at=max(entry.updated_at, when),
        )
        with self._lock:
            if represented and entry.gateway_request_id in self._active:
                updated = dataclasses.replace(updated,
                    diagnostic=self._active[entry.gateway_request_id].diagnostic,
                    usage_start=self._active[entry.gateway_request_id].usage_start,
                    usage_route=self._active[entry.gateway_request_id].usage_route,
                    usage_tokens=self._active[entry.gateway_request_id].usage_tokens,
                    usage_phase=self._active[entry.gateway_request_id].usage_phase)
                self._active[entry.gateway_request_id] = updated
                self._usage_revision += 1
            elif not represented:
                if self._unrepresented[entry.state]:
                    self._unrepresented[entry.state] -= 1
                self._unrepresented[state] += 1
                self._usage_revision += 1
        return updated

    def _release(
        self,
        gateway_request_id: str,
        represented: bool,
        state: WorkloadState,
    ) -> None:
        with self._lock:
            if represented:
                self._active.pop(gateway_request_id, None)
                self._usage_revision += 1
            elif self._unrepresented[state]:
                self._unrepresented[state] -= 1
                self._usage_revision += 1

    def _begin_finalization(
        self,
        gateway_request_id: str,
        represented: bool,
        state: WorkloadState,
    ) -> None:
        """Move one request out of active accounting before external work."""
        with self._lock:
            if represented:
                self._active.pop(gateway_request_id, None)
                self._usage_revision += 1
            elif self._unrepresented[state]:
                self._unrepresented[state] -= 1
                self._usage_revision += 1
            self._finalizing[state] += 1

    def _end_finalization(self, state: WorkloadState) -> None:
        with self._lock:
            if self._finalizing[state]:
                self._finalizing[state] -= 1


class RouterWorkloadToken:
    """One inert-then-active, ordered, idempotently finalized request token."""

    __slots__ = (
        "_registry", "_gateway_request_id", "_lock", "_activated", "_represented",
        "_entry", "_pending", "_finalized", "_disabled",
    )

    def __init__(
        self, registry: RouterWorkloadRegistry, gateway_request_id: str | None
    ) -> None:
        self._registry = registry
        self._gateway_request_id = gateway_request_id
        self._lock = threading.Lock()
        self._activated = False
        self._represented = False
        self._entry: _ActiveEntry | None = None
        self._pending: tuple[DecisionRecord, WorkloadOutcome] | None = None
        self._finalized = False
        self._disabled = gateway_request_id is None

    def activate(self) -> bool:
        """Create ``checking`` once; duplicate or invalid identity stays inert."""
        with self._lock:
            if self._finalized or self._disabled:
                return False
            if self._activated:
                return True
            when = self._registry._now()
            if when is None:
                self._disabled = True
                return False
            try:
                activated = self._registry._activate(self._gateway_request_id, when)
            except Exception:
                activated = None
            if activated is None:
                self._disabled = True
                return False
            self._represented, self._entry = activated
            self._activated = True
            return True

    def advance(self, state: WorkloadState) -> bool:
        """Advance exactly one active phase, or repeat the current phase."""
        with self._lock:
            if self._finalized or self._disabled or not self._activated:
                return False
            if type(state) is not WorkloadState or state not in _ACTIVE_PHASES:
                return False
            assert self._entry is not None
            if state is self._entry.state:
                return True
            if _NEXT_PHASE.get(self._entry.state) is not state:
                return False
            when = self._registry._now()
            if when is None:
                self._disable_locked()
                return False
            try:
                self._entry = self._registry._advance(
                    self._entry, state, when, represented=self._represented
                )
            except Exception:
                self._disable_locked()
                return False
            return True

    def propose_terminal(
        self, decision: DecisionRecord, outcome: WorkloadOutcome
    ) -> bool:
        """Retain the first fixed terminal proposal without writing the log."""
        with self._lock:
            if self._finalized or self._disabled or not self._activated:
                return False
            if not isinstance(decision, DecisionRecord) or type(outcome) is not WorkloadOutcome:
                return False
            if outcome not in _TERMINAL_OUTCOMES:
                return False
            if self._pending is None:
                self._pending = (decision, outcome)
            return True

    def finish(self, delivery_outcome: WorkloadOutcome | None = None) -> bool:
        """Commit at most one proposal and always release active accounting."""
        with self._lock:
            if self._finalized:
                return False
            self._finalized = True
            gateway_request_id = self._gateway_request_id
            activated = self._activated
            represented = self._represented
            entry = self._entry
            pending = self._pending
            if (
                delivery_outcome is not None
                and (type(delivery_outcome) is not WorkloadOutcome
                     or delivery_outcome not in _TERMINAL_OUTCOMES)
            ):
                delivery_outcome = None
            self._entry = None
            self._pending = None

        finalizing = False
        try:
            if activated and gateway_request_id is not None and entry is not None:
                self._registry._begin_finalization(
                    gateway_request_id, represented, entry.state
                )
                finalizing = True
            record: DecisionRecord | None = None
            when = self._registry._now() if pending is not None else None
            if pending is not None:
                # A failed observation clock must not erase the authoritative
                # routing decision.  Keep its legacy shape when safe workload
                # timestamps cannot be produced.
                record = pending[0]
            if (
                gateway_request_id is not None
                and entry is not None
                and pending is not None
                and when is not None
            ):
                decision, proposed_outcome = pending
                outcome = delivery_outcome or proposed_outcome
                updated = max(entry.updated_at, when)
                try:
                    record = dataclasses.replace(
                        decision,
                        gateway_request_id=gateway_request_id,
                        workload_created_at=format_workload_timestamp(
                            entry.created_at
                        ),
                        workload_updated_at=format_workload_timestamp(updated),
                        workload_outcome=outcome.value,
                    )
                except Exception:
                    record = None
            if record is not None:
                self._registry._decision_log.record(record)
        except Exception:
            pass
        finally:
            if finalizing:
                try:
                    assert entry is not None
                    self._registry._end_finalization(entry.state)
                except Exception:
                    pass
        return True

    def _disable_locked(self) -> None:
        gateway_request_id = self._gateway_request_id
        if self._activated and gateway_request_id is not None:
            try:
                assert self._entry is not None
                self._registry._release(
                    gateway_request_id, self._represented, self._entry.state
                )
            except Exception:
                pass
        self._activated = False
        self._entry = None
        self._pending = None
        self._disabled = True


def _active_record(entry: _ActiveEntry, host: str) -> WorkloadRecord:
    return WorkloadRecord(
        id=workload_id(
            host, WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER,
            entry.gateway_request_id,
        ),
        kind=WorkloadKind.ROUTER_REQUEST,
        owner=WorkloadOwner.ROUTER,
        host=host,
        state=entry.state,
        phase=_ACTIVE_PHASES[entry.state],
        outcome=None,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
        source_timestamp=entry.updated_at,
        source_authority=SourceAuthority.ROUTER_MEMORY,
        observation_quality=ObservationQuality.RECORDED,
    )


def _terminal_record(decision: object, host: str) -> WorkloadRecord | None:
    if not isinstance(decision, DecisionRecord):
        return None
    gateway_request_id = decision.gateway_request_id
    if (
        type(gateway_request_id) is not str
        or len(gateway_request_id) != 36
        or safe_gateway_request_id(gateway_request_id) != gateway_request_id
    ):
        return None
    try:
        outcome = WorkloadOutcome(decision.workload_outcome)
    except (TypeError, ValueError):
        return None
    if outcome not in _TERMINAL_OUTCOMES:
        return None
    created = parse_workload_timestamp(decision.workload_created_at)
    updated = parse_workload_timestamp(decision.workload_updated_at)
    phase = (
        WorkloadPhase.COMPLETED
        if outcome is WorkloadOutcome.SUCCESS
        else WorkloadPhase.CANCELLED
        if outcome is WorkloadOutcome.CANCELLED
        else WorkloadPhase.FAILED
    )
    return WorkloadRecord(
        id=workload_id(
            host, WorkloadKind.ROUTER_REQUEST, WorkloadOwner.ROUTER,
            gateway_request_id,
        ),
        kind=WorkloadKind.ROUTER_REQUEST,
        owner=WorkloadOwner.ROUTER,
        host=host,
        state=WorkloadState.TERMINAL,
        phase=phase,
        outcome=outcome,
        created_at=created,
        updated_at=updated,
        source_timestamp=updated,
        source_authority=SourceAuthority.ROUTER_MEMORY,
        observation_quality=ObservationQuality.RECORDED,
    )


def _select_source_records(
    records: tuple[WorkloadRecord, ...],
    query: WorkloadQuery,
    *,
    now: datetime,
) -> tuple[tuple[WorkloadRecord, ...], int]:
    """Apply canonical selection to a bounded 1024-active plus 512-recent set."""
    source_query = dataclasses.replace(query, limit=min(query.limit, SOURCE_LIMIT))
    candidates: list[WorkloadRecord] = []
    matching = 0
    for offset in range(0, len(records), _SELECTION_CHUNK):
        chunk = records[offset:offset + _SELECTION_CHUNK]
        returned, truncation = select_records(
            chunk, source_query, now=now, aggregate=True
        )
        candidates.extend(returned)
        matching += len(returned) + (truncation.omitted or 0)
    returned, _ = select_records(
        tuple(candidates), source_query, now=now, aggregate=True
    )
    return returned, matching - len(returned)


def _matching_anonymous(
    active: dict[WorkloadState, int],
    finalizing: dict[WorkloadState, int],
    query: WorkloadQuery,
    *,
    host: str,
) -> int | None:
    if query.owner not in (None, WorkloadOwner.ROUTER):
        return 0
    if query.kind not in (None, WorkloadKind.ROUTER_REQUEST):
        return 0
    if query.host is not None and query.host != host:
        return 0
    active_count = (
        sum(active.values())
        if query.state is None
        else active.get(query.state, 0)
    )
    finalizing_count = 0
    if query.state is None:
        finalizing_count = sum(finalizing.values())
    elif not query.active_only and query.state is WorkloadState.TERMINAL:
        finalizing_count = sum(finalizing.values())
    elif query.state in _ACTIVE_PHASES:
        finalizing_count = finalizing.get(query.state, 0)
    return None if active_count or finalizing_count else 0


def _record_error(
    current: WorkloadErrorCode | None, candidate: WorkloadErrorCode
) -> WorkloadErrorCode:
    if candidate is WorkloadErrorCode.FUTURE:
        return candidate
    return current or WorkloadErrorCode.INVALID


def _unavailable_source(collected: datetime) -> SourceResult:
    return SourceResult(
        owner=WorkloadOwner.ROUTER,
        status=ResultStatus.UNAVAILABLE,
        collection_timestamp=collected,
        records=(),
        truncation=Truncation(0, None),
        error=WorkloadErrorCode.UNAVAILABLE,
    )


def _query_includes_terminal(query: WorkloadQuery, *, host: str) -> bool:
    if query.active_only:
        return False
    if query.owner not in (None, WorkloadOwner.ROUTER):
        return False
    if query.kind not in (None, WorkloadKind.ROUTER_REQUEST):
        return False
    if query.host is not None and query.host != host:
        return False
    return query.state in (None, WorkloadState.TERMINAL)
