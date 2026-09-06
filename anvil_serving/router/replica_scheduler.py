"""Pure, bounded ordering within one already-selected qualified replica tier.

There is deliberately no clock, transport, lock, or cursor owner here. Admission
supplies current reservations under its own condition and owns the resulting lease.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
import math
import re

PPM = 1_000_000
MAX_REQUEST_COUNT = 1_000_000_000
MAX_MEMBER_CAPACITY = 100_000
MAX_PRESSURE_PPM = 2 * MAX_REQUEST_COUNT * PPM
PRESSURE_STALE_SECONDS = 5.0
_MEMBER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}")


class PressureFreshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    FAILED = "failed"
    UNKNOWN = "unknown"


class PressureSignalState(str, Enum):
    VALID = "valid"
    MISSING = "missing"
    INVALID = "invalid"


class ReplicaDecisionReason(str, Enum):
    SELECTED = "selected"
    NO_ELIGIBLE_MEMBER = "no-eligible-member"


def _require(valid: bool) -> None:
    if not valid:
        raise ValueError("invalid replica scheduler input")


def _integer(value: object, lower: int, upper: int) -> bool:
    return type(value) is int and lower <= value <= upper


def _member(value: object) -> bool:
    return type(value) is str and _MEMBER.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class ReplicaPressure:
    freshness: PressureFreshness = PressureFreshness.UNKNOWN
    pressure_ppm: int | None = None
    requests_state: PressureSignalState = PressureSignalState.MISSING
    kv_state: PressureSignalState = PressureSignalState.MISSING

    def __post_init__(self) -> None:
        _require(type(self.freshness) is PressureFreshness)
        _require(type(self.requests_state) is PressureSignalState and type(self.kv_state) is PressureSignalState)
        if self.freshness is PressureFreshness.FRESH:
            _require(_integer(self.pressure_ppm, 0, MAX_PRESSURE_PPM))
            _require(PressureSignalState.INVALID not in (self.requests_state, self.kv_state))
            _require(PressureSignalState.VALID in (self.requests_state, self.kv_state))
        else:
            _require(self.pressure_ppm is None)


def copy_replica_pressure(value: ReplicaPressure) -> ReplicaPressure:
    """Validate and detach a completed provider value before admission locking."""
    _require(type(value) is ReplicaPressure)
    return ReplicaPressure(value.freshness, value.pressure_ppm, value.requests_state, value.kv_state)


def _finite(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _count(value: object) -> int | None:
    number = _finite(value)
    if number is None or number > MAX_REQUEST_COUNT or not number.is_integer():
        return None
    return int(number)


def _signal(value: object, valid: bool) -> PressureSignalState:
    if value is None:
        return PressureSignalState.MISSING
    return PressureSignalState.VALID if valid else PressureSignalState.INVALID


def normalize_replica_pressure(
    *, observed_at: object, now_monotonic: object, successful: object,
    requests_running: object = None, requests_waiting: object = None,
    scheduler_capacity: object = None, kv_cache_usage_fraction: object = None,
) -> ReplicaPressure:
    """Normalize optional signals conservatively, without retaining defective input."""
    running, waiting = _count(requests_running), _count(requests_waiting)
    request_fields = (
        _signal(requests_running, running is not None),
        _signal(requests_waiting, waiting is not None),
        _signal(scheduler_capacity, _integer(scheduler_capacity, 1, MAX_MEMBER_CAPACITY)),
    )
    requests_state = (
        PressureSignalState.INVALID if PressureSignalState.INVALID in request_fields
        else PressureSignalState.MISSING if PressureSignalState.MISSING in request_fields
        else PressureSignalState.VALID
    )
    kv = _finite(kv_cache_usage_fraction)
    kv_state = _signal(kv_cache_usage_fraction, kv is not None and kv <= 1)
    observed, now = _finite(observed_at), _finite(now_monotonic)
    freshness = PressureFreshness.UNKNOWN
    if successful is False:
        freshness = PressureFreshness.FAILED
    elif (
        successful is True and observed is not None and now is not None and now >= observed
        and PressureSignalState.INVALID not in (requests_state, kv_state)
        and PressureSignalState.VALID in (requests_state, kv_state)
    ):
        freshness = (
            PressureFreshness.FRESH if now - observed <= PRESSURE_STALE_SECONDS
            else PressureFreshness.STALE
        )
    pressure = None
    if freshness is PressureFreshness.FRESH:
        signals = []
        if requests_state is PressureSignalState.VALID:
            signals.append(((running + waiting) * PPM + scheduler_capacity - 1) // scheduler_capacity)
        if kv_state is PressureSignalState.VALID:
            numerator, denominator = kv.as_integer_ratio()
            signals.append((numerator * PPM + denominator - 1) // denominator)
        pressure = max(signals)
    return ReplicaPressure(freshness, pressure, requests_state, kv_state)


@dataclass(frozen=True, slots=True)
class ReplicaCandidate:
    member_id: str
    eligible: bool
    active_requests: int
    max_concurrency: int
    pressure: ReplicaPressure

    def __post_init__(self) -> None:
        _require(_member(self.member_id) and type(self.eligible) is bool)
        _require(_integer(self.active_requests, 0, MAX_REQUEST_COUNT))
        _require(_integer(self.max_concurrency, 1, MAX_MEMBER_CAPACITY))
        copy_replica_pressure(self.pressure)


@dataclass(frozen=True, slots=True)
class ReplicaScore:
    member_id: str
    local_numerator: int
    local_denominator: int
    upstream_unknown: bool
    upstream_pressure_ppm: int | None
    rotating_rank: int
    freshness: PressureFreshness

    def __post_init__(self) -> None:
        _require(_member(self.member_id))
        _require(_integer(self.local_numerator, 0, MAX_REQUEST_COUNT))
        _require(_integer(self.local_denominator, 1, MAX_MEMBER_CAPACITY))
        _require(type(self.upstream_unknown) is bool and type(self.freshness) is PressureFreshness)
        _require(self.upstream_unknown == (self.freshness is not PressureFreshness.FRESH))
        _require(_integer(self.rotating_rank, 0, 15))
        _require(
            self.upstream_pressure_ppm is None if self.upstream_unknown
            else _integer(self.upstream_pressure_ppm, 0, MAX_PRESSURE_PPM)
        )

    def to_dict(self) -> dict:
        self.__post_init__()
        return {
            "member_id": self.member_id,
            "local_numerator": self.local_numerator,
            "local_denominator": self.local_denominator,
            "upstream_unknown": self.upstream_unknown,
            "upstream_pressure_ppm": self.upstream_pressure_ppm,
            "rotating_rank": self.rotating_rank,
            "freshness": self.freshness.value,
        }


def _score_key(score: ReplicaScore) -> tuple:
    return (
        Fraction(score.local_numerator, score.local_denominator),
        score.upstream_unknown, score.upstream_pressure_ppm or 0, score.rotating_rank,
    )


@dataclass(frozen=True, slots=True)
class ReplicaDecision:
    selected_member_id: str | None
    eligible_member_ids: tuple[str, ...]
    scores: tuple[ReplicaScore, ...]
    reason: ReplicaDecisionReason

    def __post_init__(self) -> None:
        _require(type(self.eligible_member_ids) is tuple and len(self.eligible_member_ids) <= 16)
        _require(all(_member(member) for member in self.eligible_member_ids))
        _require(tuple(sorted(set(self.eligible_member_ids))) == self.eligible_member_ids)
        _require(type(self.scores) is tuple and len(self.scores) == len(self.eligible_member_ids))
        for score in self.scores:
            _require(type(score) is ReplicaScore)
            score.__post_init__()
        _require(tuple(sorted(score.member_id for score in self.scores)) == self.eligible_member_ids)
        _require(tuple(sorted(self.scores, key=_score_key)) == self.scores)
        _require(type(self.reason) is ReplicaDecisionReason)
        if self.scores:
            _require(self.selected_member_id == self.scores[0].member_id)
            _require(self.reason is ReplicaDecisionReason.SELECTED)
        else:
            _require(self.selected_member_id is None and self.reason is ReplicaDecisionReason.NO_ELIGIBLE_MEMBER)

    def to_dict(self) -> dict:
        self.__post_init__()
        return {
            "selected_member_id": self.selected_member_id,
            "eligible_member_ids": list(self.eligible_member_ids),
            "scores": [score.to_dict() for score in self.scores],
            "reason": self.reason.value,
        }


def rank_replica_candidates(candidates: tuple[ReplicaCandidate, ...], *, cursor: int) -> ReplicaDecision:
    """Return one deterministic ordering; never advance a cursor or reserve capacity."""
    _require(type(candidates) is tuple and 2 <= len(candidates) <= 16)
    _require(_integer(cursor, 0, len(candidates) - 1))
    for candidate in candidates:
        _require(type(candidate) is ReplicaCandidate)
        candidate.__post_init__()
    _require(len({candidate.member_id for candidate in candidates}) == len(candidates))
    scores = tuple(
        ReplicaScore(
            candidate.member_id, candidate.active_requests, candidate.max_concurrency,
            candidate.pressure.freshness is not PressureFreshness.FRESH,
            candidate.pressure.pressure_ppm, (index - cursor) % len(candidates),
            candidate.pressure.freshness,
        )
        for index, candidate in enumerate(sorted(candidates, key=lambda row: row.member_id))
        if candidate.eligible and candidate.active_requests < candidate.max_concurrency
    )
    ordered = tuple(sorted(scores, key=_score_key))
    return ReplicaDecision(
        ordered[0].member_id if ordered else None,
        tuple(sorted(score.member_id for score in ordered)), ordered,
        ReplicaDecisionReason.SELECTED if ordered else ReplicaDecisionReason.NO_ELIGIBLE_MEMBER,
    )
