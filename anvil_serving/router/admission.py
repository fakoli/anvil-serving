"""Process-local tier admission and bounded drain coordination."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
import threading
import time
import contextvars
import functools
import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .availability import AvailabilityResult
from .replica_scheduler import (
    ReplicaCandidate, ReplicaDecision, ReplicaPressure, copy_replica_pressure, rank_replica_candidates,
)

_MAX_REASON_LENGTH = 128
_MEMBER_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def _reason_code(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("reason must be a non-empty string")
    if len(value) > _MAX_REASON_LENGTH:
        raise ValueError("reason is too long")
    if not all(ch.isalnum() or ch in "-_." for ch in value):
        raise ValueError("reason must be a content-free code")
    return value


def _member_id(value: object) -> str:
    if not isinstance(value, str) or not _MEMBER_ID_RE.fullmatch(value):
        raise ValueError("replica member ids must be bounded ASCII codes")
    return value


def _bounded_mapping_items(value: object, maximum: int) -> tuple[tuple[object, object], ...] | None:
    """Copy at most ``maximum`` mapping entries without trusting its iterator."""

    if not isinstance(value, Mapping):
        return None
    try:
        size = len(value)
        if size < 0 or size > maximum:
            return None
        iterator = iter(value.items())
        items = []
        for _ in range(maximum + 1):
            try:
                item = next(iterator)
            except StopIteration:
                break
            if not isinstance(item, tuple) or len(item) != 2:
                return None
            items.append(item)
        else:
            return None
        if len(items) != size:
            return None
        return tuple(items)
    except Exception:
        return None


@dataclass(frozen=True)
class MemberAdmissionSnapshot:
    tier_id: str
    member_id: str
    state: str
    reason: str
    active_requests: int
    max_concurrency: Optional[int]
    draining: bool = False

    @property
    def quiesced(self) -> bool:
        return self.state == "quiesced"

    def as_dict(self) -> dict:
        return {
            "tier_id": self.tier_id,
            "member_id": self.member_id,
            "state": self.state,
            "reason": self.reason,
            "active_requests": self.active_requests,
            "max_concurrency": self.max_concurrency,
            "draining": self.draining,
        }


@dataclass(frozen=True)
class AdmissionSnapshot:
    tier_id: str
    state: str
    reason: str
    active_requests: int
    draining: bool = False
    member_active_requests: tuple[tuple[str, int], ...] = ()
    max_concurrency: Optional[int] = None
    members: tuple[MemberAdmissionSnapshot, ...] = ()

    @property
    def quiesced(self) -> bool:
        return self.state == "quiesced"

    def as_dict(self) -> dict:
        result = {
            "tier_id": self.tier_id,
            "state": self.state,
            "reason": self.reason,
            "active_requests": self.active_requests,
            "draining": self.draining,
        }
        if self.member_active_requests:
            result["member_active_requests"] = dict(self.member_active_requests)
            result["max_concurrency"] = self.max_concurrency
            result["members"] = [member.as_dict() for member in self.members]
        return result


class AdmissionLease:
    def __init__(self, release: Callable[[], None]) -> None:
        self._release = release
        self._lock = threading.Lock()
        self._released = False

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._release()

    close = release

    def __enter__(self) -> "AdmissionLease":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class MemberAdmissionLease(AdmissionLease):
    def __init__(
        self, tier_id: str, member_id: str, release: Callable[[], None],
        *, selection: Optional[ReplicaDecision] = None,
    ) -> None:
        super().__init__(release)
        self._tier_id = tier_id
        self._member_id = member_id
        self._selection = selection

    @property
    def selection(self) -> Optional[ReplicaDecision]:
        return self._selection

    @property
    def tier_id(self) -> str:
        return self._tier_id

    @property
    def member_id(self) -> str:
        return self._member_id


@dataclass
class _MemberState:
    max_concurrency: Optional[int] = None
    quiesced: bool = False
    reason: str = "admitting"
    draining: bool = False


@dataclass
class _TierState:
    quiesced: bool = False
    reason: str = "admitting"
    active_requests: int = 0
    draining: bool = False
    members: tuple[str, ...] = ()
    member_active_requests: dict[str, int] = field(default_factory=dict)
    cursor: int = 0
    max_concurrency: Optional[int] = None
    member_states: dict[str, _MemberState] = field(default_factory=dict)
    replica_strategy: str = "round_robin"


class TierAdmission:
    """Atomic per-tier admission, quiesce, and condition-backed draining."""

    def __init__(
        self,
        tier_ids: Iterable[str],
        *,
        replica_members: Optional[Mapping[str, Sequence[str]]] = None,
        tier_max_concurrency: Optional[Mapping[str, int]] = None,
        member_max_concurrency: Optional[Mapping[str, Mapping[str, int]]] = None,
        replica_strategies: Optional[Mapping[str, str]] = None,
        on_state_change: Optional[Callable[[str], None]] = None,
    ) -> None:
        ids = tuple(tier_ids)
        if not ids or any(not isinstance(tid, str) or not tid for tid in ids):
            raise ValueError("tier_ids must contain non-empty strings")
        if len(set(ids)) != len(ids):
            raise ValueError("tier_ids must be unique")
        replicas = self._validate_replicas(ids, replica_members)
        strategies = self._validate_strategies(replica_strategies, tuple(replicas))
        tier_caps = self._validate_ceilings(tier_max_concurrency, tuple(replicas))
        member_caps: dict[str, dict[str, int]] = {}
        if member_max_concurrency is not None:
            items = _bounded_mapping_items(member_max_concurrency, len(replicas))
            if items is None:
                raise ValueError("member_max_concurrency must be a bounded mapping")
            for tid, caps in items:
                if type(tid) is not str or tid not in replicas or tid in member_caps:
                    raise ValueError("member_max_concurrency contains an unknown replica tier")
                if caps is None:
                    raise ValueError("member_max_concurrency must contain mappings")
                member_caps[tid] = self._validate_ceilings(caps, replicas[tid], maximum=100000)
        for tid, strategy in strategies.items():
            if strategy == "capacity" and len(member_caps.get(tid, {})) != len(replicas[tid]):
                raise ValueError("capacity requires a ceiling for every replica member")
        self._tier_ids = ids
        self._tiers = {
            tid: _TierState(
                members=replicas.get(tid, ()),
                member_active_requests={member: 0 for member in replicas.get(tid, ())},
                max_concurrency=tier_caps.get(tid),
                member_states={
                    member: _MemberState(max_concurrency=member_caps.get(tid, {}).get(member))
                    for member in replicas.get(tid, ())
                },
                replica_strategy=strategies.get(tid, "round_robin"),
            )
            for tid in ids
        }
        for tid, state in self._tiers.items():
            caps = member_caps.get(tid, {})
            if state.members and state.max_concurrency is None and len(caps) == len(state.members):
                state.max_concurrency = sum(caps.values())
        self._conditions = {tid: threading.Condition() for tid in ids}
        self._on_state_change = on_state_change

    @staticmethod
    def _validate_strategies(raw: object, known: tuple[str, ...]) -> dict[str, str]:
        if raw is None:
            return {}
        items = _bounded_mapping_items(raw, len(known))
        if items is None:
            raise ValueError("replica strategies must be a bounded mapping")
        result: dict[str, str] = {}
        for tid, strategy in items:
            if type(tid) is not str or tid not in known or tid in result:
                raise ValueError("replica strategies contain an unknown or duplicate tier")
            if type(strategy) is not str or strategy not in ("round_robin", "capacity"):
                raise ValueError("replica strategy must be round_robin or capacity")
            result[tid] = strategy
        return result

    @staticmethod
    def _validate_ceilings(
        raw: object, known: tuple[str, ...], *, maximum: Optional[int] = None
    ) -> dict[str, int]:
        if raw is None:
            return {}
        items = _bounded_mapping_items(raw, len(known))
        if items is None:
            raise ValueError("concurrency ceilings must be a bounded mapping")
        result: dict[str, int] = {}
        for key, cap in items:
            if type(key) is not str or key not in known or key in result:
                raise ValueError("concurrency ceiling contains an unknown or duplicate id")
            if type(cap) is not int or cap <= 0 or (maximum is not None and cap > maximum):
                raise ValueError("concurrency ceiling must be a positive bounded integer")
            result[key] = cap
        return result

    @staticmethod
    def _validate_replicas(
        ids: tuple[str, ...], replica_members: Optional[Mapping[str, Sequence[str]]]
    ) -> dict[str, tuple[str, ...]]:
        if replica_members is None:
            return {}
        if not isinstance(replica_members, Mapping):
            raise ValueError("replica_members must be a mapping")
        items = _bounded_mapping_items(replica_members, len(ids))
        if items is None:
            raise ValueError("replica_members must be a bounded stable mapping") from None
        result: dict[str, tuple[str, ...]] = {}
        known = set(ids)
        for tier_id, members in items:
            if not isinstance(tier_id, str) or tier_id not in known or tier_id in result:
                raise ValueError("replica_members contains an unknown tier")
            if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
                raise ValueError("replica member ids must be a sequence")
            try:
                member_count = len(members)
                if not 2 <= member_count <= 16:
                    raise ValueError
                copied = tuple(_member_id(members[index]) for index in range(member_count))
                if len(members) != member_count:
                    raise ValueError
            except Exception:
                raise ValueError("replica member ids must be a bounded stable sequence") from None
            if len(set(copied)) != len(copied):
                raise ValueError("replica tiers require 2..16 unique member ids")
            result[tier_id] = tuple(sorted(copied))
        return result

    def _state(self, tier_id: str) -> _TierState:
        try:
            return self._tiers[tier_id]
        except KeyError:
            raise KeyError("unknown tier") from None

    def _condition(self, tier_id: str) -> threading.Condition:
        try:
            return self._conditions[tier_id]
        except KeyError:
            raise KeyError("unknown tier") from None

    def acquire(self, tier_id: str) -> Optional[AdmissionLease]:
        condition = self._condition(tier_id)
        with condition:
            state = self._state(tier_id)
            if state.quiesced or state.members:
                return None
            state.active_requests += 1

        def _release() -> None:
            with condition:
                current = self._state(tier_id)
                if current.active_requests <= 0:
                    return
                current.active_requests -= 1
                if current.active_requests == 0:
                    condition.notify_all()

        return AdmissionLease(_release)

    @staticmethod
    def _eligible_members(
        members: tuple[str, ...], readiness: Mapping[str, AvailabilityResult]
    ) -> tuple[str, ...] | None:
        items = _bounded_mapping_items(readiness, len(members))
        if items is None or len(items) != len(members):
            return None
        copied: dict[str, AvailabilityResult] = {}
        for member, result in items:
            if not isinstance(member, str) or member not in members or member in copied:
                return None
            if type(result) is not AvailabilityResult or type(result.available) is not bool:
                return None
            copied[member] = result
        if set(copied) != set(members):
            return None
        return tuple(member for member in members if copied[member].available)

    @staticmethod
    def _member_pressures(members: tuple[str, ...], pressure: object) -> tuple[ReplicaPressure, ...] | None:
        if pressure is None:
            return tuple(ReplicaPressure() for _ in members)
        items = _bounded_mapping_items(pressure, len(members))
        if items is None or len(items) != len(members):
            return None
        copied = {}
        for member, value in items:
            if type(member) is not str or member not in members or member in copied:
                return None
            try:
                copied[member] = copy_replica_pressure(value)
            except (ValueError, AttributeError):
                return None
        return tuple(copied[member] for member in members)

    def acquire_member(
        self, tier_id: str, readiness: Mapping[str, AvailabilityResult],
        pressure: Optional[Mapping[str, ReplicaPressure]] = None,
    ) -> Optional[MemberAdmissionLease]:
        state = self._state(tier_id)
        if not state.members:
            return None
        eligible = self._eligible_members(state.members, readiness)
        if not eligible:
            return None
        pressures = self._member_pressures(state.members, pressure)
        if pressures is None:
            return None
        condition = self._condition(tier_id)
        with condition:
            state = self._state(tier_id)
            if state.quiesced or (
                state.max_concurrency is not None
                and state.active_requests >= state.max_concurrency
            ):
                return None
            eligible = tuple(
                member for member in eligible
                if not state.member_states[member].quiesced
                and (
                    state.member_states[member].max_concurrency is None
                    or state.member_active_requests[member] < state.member_states[member].max_concurrency
                )
            )
            selection = None
            if state.replica_strategy == "capacity":
                selection = rank_replica_candidates(tuple(
                    ReplicaCandidate(
                        member, member in eligible, state.member_active_requests[member],
                        state.member_states[member].max_concurrency, pressures[index],
                    )
                    for index, member in enumerate(state.members)
                ), cursor=state.cursor)
                selected_index = (
                    state.members.index(selection.selected_member_id)
                    if selection.selected_member_id is not None else None
                )
            else:
                selected_index = next(
                    (
                        index % len(state.members)
                        for index in range(state.cursor, state.cursor + len(state.members))
                        if state.members[index % len(state.members)] in eligible
                    ),
                    None,
                )
            if selected_index is None:
                return None
            selected = state.members[selected_index]
            state.active_requests += 1
            state.member_active_requests[selected] += 1
            state.cursor = (selected_index + 1) % len(state.members)

        def _release() -> None:
            with condition:
                current = self._state(tier_id)
                if current.active_requests <= 0 or current.member_active_requests[selected] <= 0:
                    raise RuntimeError("admission_member_count_invariant")
                current.active_requests -= 1
                current.member_active_requests[selected] -= 1
                if current.member_active_requests[selected] == 0:
                    condition.notify_all()

        return MemberAdmissionLease(tier_id, selected, _release, selection=selection)

    def quiesce(self, tier_id: str, reason: str = "promotion") -> AdmissionSnapshot:
        reason = _reason_code(reason)
        changed = False
        condition = self._condition(tier_id)
        with condition:
            state = self._state(tier_id)
            if state.draining:
                raise ValueError("tier drain is in progress")
            if not state.quiesced or state.reason != reason:
                state.quiesced, state.reason, changed = True, reason, True
            snapshot = self._snapshot_locked(tier_id, state)
        if changed and self._on_state_change is not None:
            self._on_state_change(tier_id)
        return snapshot

    def readmit(self, tier_id: str) -> AdmissionSnapshot:
        changed = False
        condition = self._condition(tier_id)
        with condition:
            state = self._state(tier_id)
            if state.draining:
                raise ValueError("tier drain is in progress")
            if state.quiesced or state.reason != "admitting":
                state.quiesced, state.reason, changed = False, "admitting", True
            snapshot = self._snapshot_locked(tier_id, state)
        if changed and self._on_state_change is not None:
            self._on_state_change(tier_id)
        return snapshot

    @staticmethod
    def _member_state(state: _TierState, member_id: str) -> _MemberState:
        if type(member_id) is not str or member_id not in state.member_states:
            raise KeyError("unknown replica member")
        return state.member_states[member_id]

    def _set_member_quiesce(
        self, tier_id: str, member_id: str, quiesced: bool, reason: str
    ) -> MemberAdmissionSnapshot:
        # Mirror tier transitions: mutate only this scope, then notify outside the lock.
        with self._condition(tier_id):
            state = self._state(tier_id)
            member = self._member_state(state, member_id)
            if member.draining:
                raise ValueError("member drain is in progress")
            changed = (member.quiesced, member.reason) != (quiesced, reason)
            member.quiesced, member.reason = quiesced, reason
            snapshot = self._member_snapshot_locked(tier_id, member_id, state)
        if changed and self._on_state_change is not None:
            self._on_state_change(tier_id)
        return snapshot

    def quiesce_member(
        self, tier_id: str, member_id: str, reason: str = "promotion"
    ) -> MemberAdmissionSnapshot:
        return self._set_member_quiesce(tier_id, member_id, True, _reason_code(reason))

    def readmit_member(self, tier_id: str, member_id: str) -> MemberAdmissionSnapshot:
        return self._set_member_quiesce(tier_id, member_id, False, "admitting")

    def member_snapshot(self, tier_id: str, member_id: str) -> MemberAdmissionSnapshot:
        with self._condition(tier_id):
            state = self._state(tier_id)
            self._member_state(state, member_id)
            return self._member_snapshot_locked(tier_id, member_id, state)

    def wait_for_member_drain(self, tier_id: str, member_id: str, timeout: float) -> dict:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise ValueError("timeout must be a finite positive number")
        deadline = time.monotonic() + float(timeout)
        condition = self._condition(tier_id)
        with condition:
            state = self._state(tier_id)
            member = self._member_state(state, member_id)
            if not member.quiesced:
                raise ValueError("member must be quiesced before drain")
            if member.draining:
                raise ValueError("member drain is already in progress")
            member.draining = True
            try:
                while state.member_active_requests[member_id]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    condition.wait(remaining)
                drained = state.member_active_requests[member_id] == 0
                member.draining = False
                return {
                    "drained": drained,
                    "timed_out": not drained,
                    "snapshot": self._member_snapshot_locked(tier_id, member_id, state).as_dict(),
                }
            finally:
                member.draining = False
                condition.notify_all()

    def wait_for_drain(self, tier_id: str, timeout: float) -> dict:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise ValueError("timeout must be a finite positive number")
        deadline = time.monotonic() + float(timeout)
        condition = self._condition(tier_id)
        with condition:
            state = self._state(tier_id)
            if not state.quiesced:
                raise ValueError("tier must be quiesced before drain")
            if state.draining:
                raise ValueError("tier drain is already in progress")
            state.draining = True
            try:
                while state.active_requests:
                    if not state.quiesced:
                        raise ValueError("tier was readmitted during drain")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        state.draining = False
                        return {
                            "drained": False,
                            "timed_out": True,
                            "snapshot": self._snapshot_locked(tier_id, state).as_dict(),
                        }
                    condition.wait(remaining)
                if not state.quiesced:
                    raise ValueError("tier was readmitted during drain")
                state.draining = False
                return {
                    "drained": True,
                    "timed_out": False,
                    "snapshot": self._snapshot_locked(tier_id, state).as_dict(),
                }
            finally:
                state.draining = False
                condition.notify_all()

    def snapshot(self, tier_id: str) -> AdmissionSnapshot:
        condition = self._condition(tier_id)
        with condition:
            return self._snapshot_locked(tier_id, self._state(tier_id))

    def snapshots(self) -> tuple[AdmissionSnapshot, ...]:
        lock_ids = tuple(sorted(self._tiers))
        conditions = [self._conditions[tier_id] for tier_id in lock_ids]
        for condition in conditions:
            condition.acquire()
        try:
            return tuple(
                self._snapshot_locked(tier_id, self._tiers[tier_id]) for tier_id in self._tier_ids
            )
        finally:
            for condition in reversed(conditions):
                condition.release()

    @staticmethod
    def _member_snapshot_locked(
        tier_id: str, member_id: str, state: _TierState
    ) -> MemberAdmissionSnapshot:
        member = state.member_states[member_id]
        return MemberAdmissionSnapshot(
            tier_id=tier_id,
            member_id=member_id,
            state="quiesced" if member.quiesced else "admitting",
            reason=member.reason,
            active_requests=state.member_active_requests[member_id],
            max_concurrency=member.max_concurrency,
            draining=member.draining,
        )

    @staticmethod
    def _snapshot_locked(tier_id: str, state: _TierState) -> AdmissionSnapshot:
        return AdmissionSnapshot(
            tier_id=tier_id,
            state="quiesced" if state.quiesced else "admitting",
            reason=state.reason,
            active_requests=state.active_requests,
            draining=state.draining,
            member_active_requests=tuple(
                (member, state.member_active_requests[member]) for member in state.members
            ),
            max_concurrency=state.max_concurrency,
            members=tuple(
                TierAdmission._member_snapshot_locked(tier_id, member, state)
                for member in state.members
            ),
        )


_ROUTER_WORK = contextvars.ContextVar("router_owned_work", default=None)
_WORK_FAMILIES = ("chat", "purpose", "audio", "memory", "media", "internal", "delivery", "maintenance")


class RouterAdmissionClosed(ValueError):
    """Fixed refusal; callers cannot mint an ownership permit."""


class RouterPermit(AdmissionLease):
    def __init__(self, owner, family, release):
        super().__init__(release)
        self.owner, self.family = owner, family

    @contextmanager
    def bind(self):
        with self.owner._condition:
            if self._released or self not in self.owner._permits:
                raise RouterAdmissionClosed("router_ownership_invalid")
        token = _ROUTER_WORK.set(self)
        try:
            yield self
        finally:
            _ROUTER_WORK.reset(token)


class RouterAdmission:
    """One process barrier, with actual root/child/delivery ownership.

    Native construction owns the protected persistent state and exclusive
    single-owner lock. An in-memory fixture cannot establish durable coverage.
    """

    def __init__(self, revision="unconfigured", *, persist=None, restored=None,
                 owner_scope=None, roster_revision="unconfigured", policy_revision=None):
        self._condition = threading.Condition(threading.RLock())
        self.revision, self.roster_revision = revision, roster_revision
        self._persist, self._owner_scope = persist, owner_scope
        self._policy_revision = policy_revision or (lambda:revision)
        self._permits = set()
        self._counts = dict.fromkeys(_WORK_FAMILIES, 0)
        self._unknown = set()
        self._observers = {}
        self._threads = set()
        self._on_readmit = []
        self._assembling = False
        self._closed = restored is not None
        self._token = restored.get("barrier_token") if restored else None
        self._barrier_revision = restored.get("configuration_revision") if restored else None
        self._barrier_roster = restored.get("roster_revision") if restored else None
        self._barrier_policy = restored.get("policy_revision") if restored else None
        self._generation = restored.get("generation", 0) if restored else 0
        self._consumed = bool(restored and restored.get("consumed"))
        self._failed = False

    def acquire(self, family, *, parent=None, completion=False):
        if family not in _WORK_FAMILIES:
            raise ValueError("unknown_work_family")
        parent = _ROUTER_WORK.get() if parent is None else parent
        with self._condition:
            inherited = (type(parent) is RouterPermit and parent.owner is self
                         and parent in self._permits and not parent._released)
            if parent is not None and not inherited:
                raise RouterAdmissionClosed("router_ownership_invalid")
            if self._closed and not inherited and not completion:
                raise RouterAdmissionClosed("router_quiesced")
            if self._consumed:
                raise RouterAdmissionClosed("router_cutover_pending")
            self._counts[family] += 1
            permit = RouterPermit(self, family, lambda:self._release(permit))
            self._permits.add(permit)
            return permit

    def _release(self, permit):
        with self._condition:
            if permit in self._permits:
                self._permits.remove(permit)
                self._counts[permit.family] -= 1
                self._condition.notify_all()

    def unknown(self, family):
        with self._condition:
            self._unknown.add(family)
            self._condition.notify_all()

    def track_thread(self, thread):
        with self._condition:
            self._threads.add(thread)

    def observe(self, family, callback):
        if family not in _WORK_FAMILIES or not callable(callback):
            raise ValueError("invalid_owner_observer")
        with self._condition:
            if family in self._observers or (self._closed and not self._assembling):
                raise ValueError("owner_roster_changed")
            self._observers[family] = callback

    def _state(self):
        return {"barrier_token":self._token, "configuration_revision":self._barrier_revision,
                "roster_revision":self._barrier_roster, "generation":self._generation,
                "policy_revision":self._barrier_policy,
                "consumed":self._consumed} if self._closed else None

    def _save(self):
        if self._persist is None:
            raise ValueError("router_persistence_unavailable")
        try:
            self._persist(self._state())
        except BaseException:
            self._failed = True
            raise

    def _check(self, token):
        if (type(token) is not str or not self._closed or not self._token
                or not secrets.compare_digest(token, self._token)
                or self._barrier_revision != self.revision
                or self._barrier_policy != self._policy_revision()
                or self._barrier_roster != self.roster_revision or self._failed):
            raise ValueError("router_barrier_stale")

    def status(self):
        with self._condition:
            return {"scope":"router", "state":"quiesced" if self._closed else "admitting",
                    "configuration_revision":self.revision, "roster_revision":self.roster_revision,
                    "generation":self._generation, "counts":dict(self._counts),
                    "durable":self._persist is not None and not self._failed,
                    "cutover_pending":self._consumed}

    def quiesce_router(self, reason="operator", *, dry_run=True, confirm=False):
        _reason_code(reason)
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise ValueError("confirmation_must_be_boolean")
        with self._condition:
            if dry_run or not confirm:
                return {"applied":False, "dry_run":True, **self.status()}
            if self._closed:
                self._check(self._token)
                return {"applied":True, **self.status(), "barrier_token":self._token}
            # Closure commits under the acquisition lock before the receipt.
            self._closed = True
            self._token = secrets.token_hex(32)
            self._barrier_revision, self._barrier_roster = self.revision, self.roster_revision
            self._barrier_policy = self._policy_revision()
            self._generation += 1
            self._save()  # Failure leaves the gate closed, never success.
            self._condition.notify_all()
            return {"applied":True, **self.status(), "barrier_token":self._token}

    def _drain_counts(self):
        counts, unknown = dict(self._counts), set(self._unknown)
        self._threads = {thread for thread in self._threads if thread.is_alive()}
        counts["delivery"] += len(self._threads)
        if self._owner_scope is None:
            unknown.add("owner_roster_unknown")
        else:
            try:
                self._owner_scope()
            except Exception:
                unknown.add("owner_roster_unknown")
        for family, callback in self._observers.items():
            try:
                count, unresolved = callback()
                if type(count) is not int or not 0 <= count <= 100000 or type(unresolved) is not bool:
                    raise ValueError("invalid_owner_readback")
                counts[family] += count
                if unresolved:
                    unknown.add(family)
            except Exception:
                unknown.add(family)
        return counts, sorted(unknown)

    def drain_router(self, barrier_token, timeout_s=30):
        if type(timeout_s) is not int or not 1 <= timeout_s <= 900:
            raise ValueError("timeout_must_be_integer_1_900")
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                self._check(barrier_token)
                counts, unknown = self._drain_counts()
                drained = not any(counts.values()) and not unknown
                if drained or time.monotonic() >= deadline:
                    return {**self.status(), "barrier_token":barrier_token, "counts":counts,
                            "unknown":unknown, "drained":drained, "timed_out":not drained}
                self._condition.wait(min(.1, max(0, deadline-time.monotonic())))

    def consume(self, barrier_token):
        """Recheck zero and persist consumption under the acquisition lock."""
        with self._condition:
            self._check(barrier_token)
            counts, unknown = self._drain_counts()
            if any(counts.values()) or unknown:
                raise ValueError("router_drain_required")
            self._consumed = True
            self._save()
            return {**self.status(), "barrier_token":barrier_token, "drained":True}

    def readmit_router(self, barrier_token, *, dry_run=True, confirm=False):
        if type(dry_run) is not bool or type(confirm) is not bool:
            raise ValueError("confirmation_must_be_boolean")
        with self._condition:
            self._check(barrier_token)
            if self._consumed:
                raise ValueError("router_cutover_pending")
            if dry_run or not confirm:
                return {"applied":False, "dry_run":True, **self.status()}
            _counts, unknown = self._drain_counts()
            if unknown:
                raise ValueError("router_owner_roster_unknown")
            # Persist removal before opening. Tier/member intentions are untouched.
            saved = self._state()
            self._closed = False
            try:
                self._save()
                for resume in self._on_readmit:
                    resume()
            except BaseException:
                self._closed = True
                self._token = saved["barrier_token"]
                self._save()
                raise
            self._token = None
            self._condition.notify_all()
            return {"applied":True, "readmitted":True, **self.status()}


def owned_dispatch(family):
    """Guard eager dispatch and retain returned iterators through real cleanup."""
    def decorate(operation):
        @functools.wraps(operation)
        def call(self, *args, **kwargs):
            owner = getattr(self, "_router_admission", None)
            if owner is None:
                return operation(self, *args, **kwargs)
            permit = owner.acquire(family)
            try:
                with permit.bind():
                    result = operation(self, *args, **kwargs)
                from collections.abc import Iterator
                if not isinstance(result, Iterator):
                    permit.release()
                    return result
            except BaseException:
                permit.release()
                raise
            started = False
            def generate():
                nonlocal started
                started = True
                try:
                    while True:
                        try:
                            with permit.bind():
                                value = next(result)
                        except StopIteration:
                            return
                        yield value
                finally:
                    try:
                        with permit.bind():
                            closer = getattr(result, "close", None)
                            if callable(closer):
                                closer()
                    except BaseException:
                        owner.unknown(family)
                        raise
                    else:
                        permit.release()
            def close_unstarted():
                if not started:
                    try:
                        with permit.bind():
                            closer = getattr(result, "close", None)
                            if callable(closer):
                                closer()
                    except BaseException:
                        owner.unknown(family)
                        raise
                    else:
                        permit.release()
            from .backends.relay import _ClosingIterator
            return _ClosingIterator(generate(), close_unstarted)
        return call
    return decorate


def managed_router_admission(server_config, revision):
    """Production producer bound to one explicitly configured native owner.

    Uses the existing protected owner directory and usage store, never creates
    schemas or recovers foreign runs at startup. Linux ownership is required;
    incomparable platforms and multiple potential admitters fail closed.
    """
    owner_id = server_config.router_owner_id
    if owner_id is None:
        return RouterAdmission(revision), None, None
    from .keys import KeyStore, _secure_directory, _private_created_descriptor, _secure_database
    from .usage_store import UsageStore, RunOwner
    import fcntl
    import hashlib
    if server_config.router_owner_roster != (owner_id,):
        raise ValueError("router_owner_roster_unsupported")
    path = Path(server_config.admission_state_path + ".router")
    _secure_directory(path.parent, create=False)
    lock_path = path.with_suffix(path.suffix + ".lock")
    if os.path.lexists(lock_path):
        _secure_database(lock_path, exists=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _private_created_descriptor(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        actual_owner = RunOwner.observe(owner_id)
        restored = None
        if os.path.lexists(path):
            _secure_database(path, exists=True)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                _private_created_descriptor(fd)
                raw = os.read(fd, 16385)
            finally:
                os.close(fd)
            from ..observability.dashboard.contracts import strict_json
            data = strict_json(raw)
            if (len(raw) > 16384 or type(data) is not dict or set(data) != {"schema", "owner_id", "closure"}
                    or data["schema"] != "router-admission/v1" or data["owner_id"] != owner_id):
                raise ValueError("router_admission_state_invalid")
            restored = data["closure"]
            if restored is not None and (type(restored) is not dict or set(restored) != {
                    "barrier_token", "configuration_revision", "roster_revision", "policy_revision", "generation", "consumed"}
                    or type(restored["barrier_token"]) is not str or not re.fullmatch("[0-9a-f]{64}", restored["barrier_token"])
                    or any(type(restored[k]) is not str or len(restored[k]) > 256 for k in ("configuration_revision", "roster_revision", "policy_revision"))
                    or type(restored["generation"]) is not int or not 1 <= restored["generation"] < 2**53
                    or type(restored["consumed"]) is not bool):
                raise ValueError("router_admission_state_invalid")
        # Roster identity binds the actual process owner, not the operator label.
        from dataclasses import asdict
        roster_revision = hashlib.sha256(json.dumps({"roster":server_config.router_owner_roster,
            "owner":asdict(actual_owner)}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

        def persist(closure):
            import tempfile
            _secure_directory(path.parent, create=False)
            if os.path.lexists(path):
                _secure_database(path, exists=True)
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".router-admission-")
            try:
                _private_created_descriptor(fd)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump({"schema":"router-admission/v1", "owner_id":owner_id, "closure":closure},
                              out, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    out.flush(); os.fsync(out.fileno())
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

        usage = UsageStore(KeyStore(server_config.api_keys_path))
        run_id = usage.register_run(actual_owner, domain_id=server_config.usage_domain_id,
                                    configuration_revision=revision, enabled=server_config.usage_enabled)
        def scope():
            return usage.managed_owner_scope(run_id, actual_owner, domain_id=server_config.usage_domain_id,
                                              configuration_revision=revision)
        scope()  # Incomparable/foreign runs refuse before the listener exists.
        owner = RouterAdmission(revision, persist=persist, restored=restored, owner_scope=scope,
                                roster_revision=roster_revision)
        owner._assembling = True
        owner._owner_descriptor = descriptor
        owner.usage_scope = scope
        owner.usage_run_id = run_id
        persist(owner._state())
        return owner, usage, run_id
    except BaseException:
        os.close(descriptor)
        raise
