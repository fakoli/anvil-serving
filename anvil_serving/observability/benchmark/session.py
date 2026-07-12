"""Non-blocking programmatic benchmark telemetry capture lifecycle."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from ..retention import RetainedFrame, RetentionStore


PHASES = frozenset({"load", "readiness", "warm-up", "measured-run", "release", "stabilization"})
PRE_HISTORY = timedelta(minutes=5)
MIN_POST_HISTORY = timedelta(minutes=5)
STABILITY_WINDOW = timedelta(seconds=60)
HARD_POST_LIMIT = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class PhaseMarker:
    phase: str
    timestamp: datetime

    def __post_init__(self) -> None:
        if self.phase not in PHASES:
            raise ValueError("unknown benchmark lifecycle phase")
        _utc(self.timestamp)


@dataclass(slots=True)
class CaptureSession:
    store: RetentionStore
    session_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: datetime | None = None
    benchmark_ended_at: datetime | None = None
    completed_at: datetime | None = None
    pre_history: tuple[RetainedFrame, ...] = ()
    captured: list[RetainedFrame] = field(default_factory=list)
    markers: list[PhaseMarker] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    stable_since: datetime | None = None

    @property
    def state(self) -> str:
        if self.completed_at is not None:
            return "complete"
        if self.benchmark_ended_at is not None:
            return "post-capture"
        if self.started_at is not None:
            return "capturing"
        return "created"

    def start(self, *, now: datetime) -> dict[str, Any]:
        current = _utc(now)
        if self.started_at is not None:
            return self.status()
        self.started_at = current
        cutoff = current - PRE_HISTORY
        self.pre_history = tuple(
            frame for frame in self.store.frames(0) if frame.observed_at >= cutoff
        )
        self.store.set_capture_active(True)
        return self.status()

    def mark(self, phase: str, *, now: datetime) -> None:
        if self.started_at is None or self.completed_at is not None:
            raise RuntimeError("capture session is not active")
        marker = PhaseMarker(phase, _utc(now))
        if self.markers and marker.timestamp < self.markers[-1].timestamp:
            raise ValueError("phase timestamps must not move backwards")
        self.markers.append(marker)

    def collect(
        self,
        collector: Callable[[], Mapping[str, Any]],
        *,
        now: datetime,
        probe_duration_seconds: float = 0,
        expected_interval_seconds: float = 1,
    ) -> dict[str, Any]:
        """Collect one frame; failures are evidence and never escape to inference."""

        current = _utc(now)
        try:
            snapshot = collector()
            frame = self.store.add(
                snapshot,
                observed_at=current,
                probe_duration_seconds=probe_duration_seconds,
                expected_interval_seconds=expected_interval_seconds,
            )
            self.captured.append(frame)
            return {"ok": True, "state": self.state}
        except Exception as exc:
            self.failures.append({"timestamp": _format(current), "error": str(exc)[:4096]})
            return {"ok": False, "state": self.state, "error": "capture-degraded"}

    def stop(self, *, now: datetime) -> dict[str, Any]:
        current = _utc(now)
        if self.started_at is None:
            raise RuntimeError("capture session has not started")
        if self.benchmark_ended_at is None:
            self.benchmark_ended_at = current
            self.markers.append(PhaseMarker("release", current))
            self.markers.append(PhaseMarker("stabilization", current))
        return self.status()

    def observe_stability(self, *, stable: bool, now: datetime) -> dict[str, Any]:
        """Advance post-capture without blocking the caller."""

        current = _utc(now)
        if self.benchmark_ended_at is None:
            raise RuntimeError("benchmark lifecycle has not stopped")
        if self.completed_at is not None:
            return self.status()
        self.stable_since = (
            current
            if stable and self.stable_since is None
            else self.stable_since
            if stable
            else None
        )
        post_age = current - self.benchmark_ended_at
        stable_age = current - self.stable_since if self.stable_since is not None else timedelta(0)
        if post_age >= HARD_POST_LIMIT or (
            post_age >= MIN_POST_HISTORY and stable_age >= STABILITY_WINDOW
        ):
            self.completed_at = current
            self.store.set_capture_active(False)
        return self.status()

    def status(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "started_at": _optional(self.started_at),
            "benchmark_ended_at": _optional(self.benchmark_ended_at),
            "completed_at": _optional(self.completed_at),
            "pre_history_frames": len(self.pre_history),
            "captured_frames": len(self.captured),
            "failures": list(self.failures),
            "markers": [
                {"phase": marker.phase, "timestamp": _format(marker.timestamp)}
                for marker in self.markers
            ],
        }


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _format(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _optional(value: datetime | None) -> str | None:
    return None if value is None else _format(value)
