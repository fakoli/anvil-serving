"""Bounded, metadata-only router workload lifecycle projection.

This module owns active observation state only. ``DecisionLog`` remains the
sole terminal store, and no observation failure is allowed to affect routing.
"""
from __future__ import annotations

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
    safe_gateway_request_id,
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
            else:
                self._unrepresented[WorkloadState.CHECKING] += 1
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
                self._active[entry.gateway_request_id] = updated
            elif not represented:
                if self._unrepresented[entry.state]:
                    self._unrepresented[entry.state] -= 1
                self._unrepresented[state] += 1
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
            elif self._unrepresented[state]:
                self._unrepresented[state] -= 1

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
            elif self._unrepresented[state]:
                self._unrepresented[state] -= 1
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
