"""Bounded memory-first telemetry retention and sampling profiles."""

from __future__ import annotations

import json
import os
import tempfile
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MIB = 1024 * 1024
MAX_ORDINARY_BYTES = 250 * MIB


@dataclass(frozen=True, slots=True)
class SamplingProfile:
    name: str
    core_seconds: float
    costly_seconds: float

    def __post_init__(self) -> None:
        if self.name not in {"normal", "benchmark"}:
            raise ValueError("sampling profile must be normal or benchmark")
        if not 0.25 <= self.core_seconds <= 60:
            raise ValueError("core sampling interval must be between 0.25 and 60 seconds")
        if not self.core_seconds <= self.costly_seconds <= 60:
            raise ValueError(
                "costly sampling interval must be between core interval and 60 seconds"
            )
        if self.name == "benchmark" and not 2 <= self.costly_seconds <= 5:
            raise ValueError("benchmark costly interval must be between 2 and 5 seconds")

    def interval_for(self, *, costly: bool) -> float:
        return self.costly_seconds if costly else self.core_seconds


NORMAL_PROFILE = SamplingProfile("normal", 2.0, 5.0)
BENCHMARK_PROFILE = SamplingProfile("benchmark", 1.0, 2.0)


@dataclass(frozen=True, slots=True)
class RetainedFrame:
    observed_at: datetime
    probe_duration_seconds: float
    expected_interval_seconds: float
    gap: bool
    resolution_seconds: int
    snapshot: Mapping[str, Any]
    aggregate_count: int = 1

    def __post_init__(self) -> None:
        _utc(self.observed_at)
        for name, value in (
            ("probe_duration_seconds", self.probe_duration_seconds),
            ("expected_interval_seconds", self.expected_interval_seconds),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
        if self.expected_interval_seconds == 0:
            raise ValueError("expected_interval_seconds must be greater than zero")
        if self.resolution_seconds not in {0, 15, 60}:
            raise ValueError("resolution_seconds must be 0, 15, or 60")
        if not isinstance(self.snapshot, Mapping):
            raise TypeError("snapshot must be a mapping")

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at": _format(self.observed_at),
            "probe_duration_seconds": self.probe_duration_seconds,
            "expected_interval_seconds": self.expected_interval_seconds,
            "gap": self.gap,
            "resolution_seconds": self.resolution_seconds,
            "snapshot": dict(self.snapshot),
        }


class RetentionStore:
    """Three deterministic time windows sharing one strict byte budget."""

    def __init__(
        self,
        *,
        max_bytes: int = MAX_ORDINARY_BYTES,
        persistence_path: str | Path | None = None,
        persistence_batch_seconds: float = 60,
    ) -> None:
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= MAX_ORDINARY_BYTES
        ):
            raise ValueError("max_bytes must be between 1 and 250 MiB")
        if not 1 <= persistence_batch_seconds <= 3600:
            raise ValueError("persistence batch interval must be between 1 and 3600 seconds")
        self.max_bytes = max_bytes
        self.persistence_path = Path(persistence_path) if persistence_path else None
        self.persistence_batch_seconds = float(persistence_batch_seconds)
        self.capture_active = False
        self._tiers: dict[int, deque[RetainedFrame]] = {0: deque(), 15: deque(), 60: deque()}
        self._sizes: dict[int, deque[int]] = {0: deque(), 15: deque(), 60: deque()}
        self._total_bytes = 0
        self._last_observed: datetime | None = None
        self._last_persisted: datetime | None = None
        self._dirty = False

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def frames(self, resolution_seconds: int = 0) -> tuple[RetainedFrame, ...]:
        return tuple(self._tiers[resolution_seconds])

    def set_capture_active(self, active: bool) -> None:
        if type(active) is not bool:
            raise TypeError("capture state must be boolean")
        self.capture_active = active

    def add(
        self,
        snapshot: Mapping[str, Any],
        *,
        observed_at: datetime,
        probe_duration_seconds: float,
        expected_interval_seconds: float,
    ) -> RetainedFrame:
        observed = _utc(observed_at)
        if self._last_observed is not None and observed < self._last_observed:
            raise ValueError("observed_at must not move backwards")
        gap = (
            self._last_observed is not None
            and (observed - self._last_observed).total_seconds() > expected_interval_seconds * 1.5
        )
        frame = RetainedFrame(
            observed,
            probe_duration_seconds,
            expected_interval_seconds,
            gap,
            0,
            _bounded_snapshot(snapshot),
        )
        self._append(0, frame)
        self._rollup(15, frame)
        self._rollup(60, frame)
        self._last_observed = observed
        self._evict_by_time(observed)
        self._evict_to_budget()
        self._dirty = True
        return frame

    def flush_if_due(self, *, now: datetime) -> bool:
        current = _utc(now)
        if self.capture_active or self.persistence_path is None or not self._dirty:
            return False
        if (
            self._last_persisted is not None
            and (current - self._last_persisted).total_seconds() < self.persistence_batch_seconds
        ):
            return False
        payload = json.dumps(self.as_dict(), separators=(",", ":"), sort_keys=True).encode()
        target = self.persistence_path.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        self._last_persisted = current
        self._dirty = False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "max_bytes": self.max_bytes,
            "tiers": {
                str(resolution): [frame.as_dict() for frame in frames]
                for resolution, frames in self._tiers.items()
            },
        }

    def _append(self, resolution: int, frame: RetainedFrame) -> None:
        size = _frame_size(frame)
        self._tiers[resolution].append(frame)
        self._sizes[resolution].append(size)
        self._total_bytes += size

    def _replace_last(self, resolution: int, frame: RetainedFrame) -> None:
        self._total_bytes -= self._sizes[resolution].pop()
        self._tiers[resolution].pop()
        self._append(resolution, frame)

    def _rollup(self, resolution: int, frame: RetainedFrame) -> None:
        tier = self._tiers[resolution]
        bucket = int(frame.observed_at.timestamp()) // resolution
        if tier and int(tier[-1].observed_at.timestamp()) // resolution == bucket:
            self._replace_last(resolution, _merge(tier[-1], frame, resolution))
        else:
            self._append(
                resolution,
                RetainedFrame(
                    frame.observed_at,
                    frame.probe_duration_seconds,
                    frame.expected_interval_seconds,
                    frame.gap,
                    resolution,
                    frame.snapshot,
                ),
            )

    def _evict_by_time(self, now: datetime) -> None:
        for resolution, age in (
            (0, timedelta(hours=1)),
            (15, timedelta(hours=24)),
            (60, timedelta(days=7)),
        ):
            while self._tiers[resolution] and now - self._tiers[resolution][0].observed_at > age:
                self._evict_left(resolution)

    def _evict_to_budget(self) -> None:
        while self._total_bytes > self.max_bytes:
            candidates = [
                (frames[0].observed_at, resolution)
                for resolution, frames in self._tiers.items()
                if frames
            ]
            if not candidates:
                break
            _, resolution = min(candidates, key=lambda item: (item[0], item[1]))
            self._evict_left(resolution)

    def _evict_left(self, resolution: int) -> None:
        self._tiers[resolution].popleft()
        self._total_bytes -= self._sizes[resolution].popleft()


def _merge(old: RetainedFrame, new: RetainedFrame, resolution: int) -> RetainedFrame:
    count = old.aggregate_count + 1
    snapshot = dict(new.snapshot)
    old_samples = old.snapshot.get("samples")
    new_samples = new.snapshot.get("samples")
    if isinstance(old_samples, list) and isinstance(new_samples, list):
        prior = {
            _series_key(sample): sample for sample in old_samples if isinstance(sample, Mapping)
        }
        merged = []
        for sample in new_samples:
            value = dict(sample) if isinstance(sample, Mapping) else sample
            if isinstance(value, dict):
                before = prior.get(_series_key(value))
                if before and _number(before.get("value")) and _number(value.get("value")):
                    value["value"] = (
                        before["value"] * old.aggregate_count + value["value"]
                    ) / count
            merged.append(value)
        snapshot["samples"] = merged
    return RetainedFrame(
        new.observed_at,
        (old.probe_duration_seconds * old.aggregate_count + new.probe_duration_seconds) / count,
        new.expected_interval_seconds,
        old.gap or new.gap,
        resolution,
        snapshot,
        count,
    )


def _series_key(sample: Mapping[str, Any]) -> str:
    return json.dumps(
        [sample.get("host_id"), sample.get("metric"), sample.get("labels")],
        separators=(",", ":"),
        sort_keys=True,
    )


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _bounded_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise TypeError("snapshot must be a mapping")
    encoded = json.dumps(snapshot, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()
    if len(encoded) > 8 * MIB:
        raise ValueError("snapshot exceeds 8 MiB")
    return json.loads(encoded)


def _frame_size(frame: RetainedFrame) -> int:
    return len(json.dumps(frame.as_dict(), separators=(",", ":"), sort_keys=True).encode())


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _format(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")
