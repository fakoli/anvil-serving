"""Process-local tier admission and bounded drain coordination."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from .availability import AvailabilityResult

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
class AdmissionSnapshot:
    tier_id: str
    state: str
    reason: str
    active_requests: int
    draining: bool = False
    member_active_requests: tuple[tuple[str, int], ...] = ()

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
    def __init__(self, tier_id: str, member_id: str, release: Callable[[], None]) -> None:
        super().__init__(release)
        self._tier_id = tier_id
        self._member_id = member_id

    @property
    def tier_id(self) -> str:
        return self._tier_id

    @property
    def member_id(self) -> str:
        return self._member_id


@dataclass
class _TierState:
    quiesced: bool = False
    reason: str = "admitting"
    active_requests: int = 0
    draining: bool = False
    members: tuple[str, ...] = ()
    member_active_requests: dict[str, int] = field(default_factory=dict)
    cursor: int = 0


class TierAdmission:
    """Atomic per-tier admission, quiesce, and condition-backed draining."""

    def __init__(
        self,
        tier_ids: Iterable[str],
        *,
        replica_members: Optional[Mapping[str, Sequence[str]]] = None,
        on_state_change: Optional[Callable[[str], None]] = None,
    ) -> None:
        ids = tuple(tier_ids)
        if not ids or any(not isinstance(tid, str) or not tid for tid in ids):
            raise ValueError("tier_ids must contain non-empty strings")
        if len(set(ids)) != len(ids):
            raise ValueError("tier_ids must be unique")
        replicas = self._validate_replicas(ids, replica_members)
        self._tier_ids = ids
        self._tiers = {
            tid: _TierState(
                members=replicas.get(tid, ()),
                member_active_requests={member: 0 for member in replicas.get(tid, ())},
            )
            for tid in ids
        }
        self._conditions = {tid: threading.Condition() for tid in ids}
        self._on_state_change = on_state_change

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

    def acquire_member(
        self, tier_id: str, readiness: Mapping[str, AvailabilityResult]
    ) -> Optional[MemberAdmissionLease]:
        state = self._state(tier_id)
        if not state.members:
            return None
        eligible = self._eligible_members(state.members, readiness)
        if not eligible:
            return None
        condition = self._condition(tier_id)
        with condition:
            state = self._state(tier_id)
            if state.quiesced:
                return None
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
            state.cursor = (selected_index + 1) % len(state.members)
            state.active_requests += 1
            state.member_active_requests[selected] += 1

        def _release() -> None:
            with condition:
                current = self._state(tier_id)
                if current.active_requests <= 0 or current.member_active_requests[selected] <= 0:
                    raise RuntimeError("admission_member_count_invariant")
                current.active_requests -= 1
                current.member_active_requests[selected] -= 1
                if current.active_requests == 0:
                    condition.notify_all()

        return MemberAdmissionLease(tier_id, selected, _release)

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
        )
