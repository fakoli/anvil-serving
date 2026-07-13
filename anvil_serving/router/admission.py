"""Process-local tier admission and bounded drain coordination.

The router owns this state.  It deliberately has no container or lifecycle
side effects: operators quiesce a tier, wait for its counted requests to drain,
and only then perform a serve transition through the existing guarded workflow.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional


_MAX_REASON_LENGTH = 128


def _reason_code(value: str) -> str:
    """Return a bounded, content-free operator reason code."""
    if not isinstance(value, str) or not value:
        raise ValueError("reason must be a non-empty string")
    if len(value) > _MAX_REASON_LENGTH:
        raise ValueError("reason is too long")
    if not all(ch.isalnum() or ch in "-_." for ch in value):
        raise ValueError("reason must be a content-free code")
    return value


@dataclass(frozen=True)
class AdmissionSnapshot:
    """Bounded status for one configured tier."""

    tier_id: str
    state: str
    reason: str
    active_requests: int

    @property
    def quiesced(self) -> bool:
        return self.state == "quiesced"

    def as_dict(self) -> dict:
        return {
            "tier_id": self.tier_id,
            "state": self.state,
            "reason": self.reason,
            "active_requests": self.active_requests,
        }


class AdmissionLease:
    """Idempotent lease covering one complete upstream generation."""

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


@dataclass
class _TierState:
    quiesced: bool = False
    reason: str = "admitting"
    active_requests: int = 0


class TierAdmission:
    """Atomic per-tier admission, quiesce, and condition-backed draining."""

    def __init__(
        self,
        tier_ids: Iterable[str],
        *,
        on_state_change: Optional[Callable[[str], None]] = None,
    ) -> None:
        ids = tuple(tier_ids)
        if not ids or any(not isinstance(tid, str) or not tid for tid in ids):
            raise ValueError("tier_ids must contain non-empty strings")
        if len(set(ids)) != len(ids):
            raise ValueError("tier_ids must be unique")
        self._condition = threading.Condition()
        self._tiers = {tid: _TierState() for tid in ids}
        self._on_state_change = on_state_change

    def _state(self, tier_id: str) -> _TierState:
        try:
            return self._tiers[tier_id]
        except KeyError:
            raise KeyError("unknown tier") from None

    def acquire(self, tier_id: str) -> Optional[AdmissionLease]:
        """Atomically reject a quiesced tier or count a new active request."""
        with self._condition:
            state = self._state(tier_id)
            if state.quiesced:
                return None
            state.active_requests += 1

        def _release() -> None:
            with self._condition:
                current = self._state(tier_id)
                if current.active_requests <= 0:  # defensive invariant guard
                    return
                current.active_requests -= 1
                if current.active_requests == 0:
                    self._condition.notify_all()

        return AdmissionLease(_release)

    def quiesce(self, tier_id: str, reason: str = "promotion") -> AdmissionSnapshot:
        reason = _reason_code(reason)
        changed = False
        with self._condition:
            state = self._state(tier_id)
            if not state.quiesced or state.reason != reason:
                state.quiesced = True
                state.reason = reason
                changed = True
            snapshot = self._snapshot_locked(tier_id, state)
        if changed and self._on_state_change is not None:
            self._on_state_change(tier_id)
        return snapshot

    def readmit(self, tier_id: str) -> AdmissionSnapshot:
        changed = False
        with self._condition:
            state = self._state(tier_id)
            if state.quiesced or state.reason != "admitting":
                state.quiesced = False
                state.reason = "admitting"
                changed = True
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
        with self._condition:
            state = self._state(tier_id)
            if not state.quiesced:
                raise ValueError("tier must be quiesced before drain")
            while state.active_requests:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {
                        "drained": False,
                        "timed_out": True,
                        "snapshot": self._snapshot_locked(tier_id, state).as_dict(),
                    }
                self._condition.wait(remaining)
            return {
                "drained": True,
                "timed_out": False,
                "snapshot": self._snapshot_locked(tier_id, state).as_dict(),
            }

    def snapshot(self, tier_id: str) -> AdmissionSnapshot:
        with self._condition:
            state = self._state(tier_id)
            return self._snapshot_locked(tier_id, state)

    def snapshots(self) -> tuple[AdmissionSnapshot, ...]:
        with self._condition:
            return tuple(
                self._snapshot_locked(tid, state)
                for tid, state in self._tiers.items()
            )

    @staticmethod
    def _snapshot_locked(tier_id: str, state: _TierState) -> AdmissionSnapshot:
        return AdmissionSnapshot(
            tier_id=tier_id,
            state="quiesced" if state.quiesced else "admitting",
            reason=state.reason,
            active_requests=state.active_requests,
        )
