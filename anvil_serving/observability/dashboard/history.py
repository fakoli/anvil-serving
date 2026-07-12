"""Bounded recent-history and timestamp-aware benchmark comparison."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..retention import RetainedFrame, RetentionStore


@dataclass(frozen=True, slots=True)
class RetainedBenchmark:
    session_id: str
    frames: tuple[RetainedFrame, ...]
    manifest: Mapping[str, Any]


class BenchmarkHistory:
    def __init__(self, sessions: Sequence[RetainedBenchmark] = ()) -> None:
        self._sessions = {session.session_id: session for session in sessions}

    def register(self, session: RetainedBenchmark) -> None:
        if session.session_id in self._sessions:
            raise ValueError("benchmark session is already retained")
        self._sessions[session.session_id] = session

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "session_id": session.session_id,
                "capture_quality": session.manifest.get("capture_quality", "unknown"),
                "frame_count": len(session.frames),
                "gap_count": sum(frame.gap for frame in session.frames),
            }
            for session in sorted(self._sessions.values(), key=lambda item: item.session_id)
        ]

    def compare(
        self,
        session_id: str,
        current: RetentionStore,
        *,
        metric: str,
        max_points: int = 5000,
    ) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise ValueError("benchmark session is not retained")
        if not metric or len(metric) > 256:
            raise ValueError("metric must be a bounded non-empty string")
        if not 1 <= max_points <= 10_000:
            raise ValueError("max_points must be between 1 and 10000")
        current_points = _points(current.frames(0), metric)[-max_points:]
        benchmark_points = _points(self._sessions[session_id].frames, metric)[-max_points:]
        aligned = []
        for point in current_points:
            candidate = _nearest(point, benchmark_points)
            aligned.append(
                {
                    "current": point,
                    "benchmark": candidate,
                    "gap": candidate is None or point["gap"] or candidate["gap"],
                }
            )
        return {
            "session_id": session_id,
            "metric": metric,
            "current_points": current_points,
            "benchmark_points": benchmark_points,
            "aligned": aligned,
            "benchmark_gaps": [point for point in benchmark_points if point["gap"]],
        }


def bounded_history(
    store: RetentionStore, *, resolution_seconds: int = 0, max_frames: int = 500
) -> dict[str, Any]:
    if not 1 <= max_frames <= 5000:
        raise ValueError("max_frames must be between 1 and 5000")
    all_frames = store.frames(resolution_seconds)
    frames = all_frames[-max_frames:]
    return {
        "resolution_seconds": resolution_seconds,
        "retained_bytes": store.total_bytes,
        "frame_count": len(all_frames),
        "returned_frame_count": len(frames),
        "frames": [frame.as_dict() for frame in frames],
    }


def _points(frames: Sequence[RetainedFrame], metric: str) -> list[dict[str, Any]]:
    output = []
    for frame in frames:
        samples = frame.snapshot.get("samples", [])
        if not isinstance(samples, list):
            continue
        for sample in samples:
            if not isinstance(sample, Mapping) or sample.get("metric") != metric:
                continue
            freshness = sample.get("freshness")
            stale_after = (
                freshness.get("stale_after_seconds") if isinstance(freshness, Mapping) else None
            )
            timestamp = sample.get("source_timestamp")
            observed = _parse(timestamp) if isinstance(timestamp, str) else frame.observed_at
            status = sample.get("capability_status", "ok")
            output.append(
                {
                    "timestamp": _format(observed),
                    "value": sample.get("value") if status == "ok" else None,
                    "freshness_seconds": stale_after,
                    "gap": frame.gap or status != "ok" or sample.get("value") is None,
                    "host_id": sample.get("host_id"),
                    "labels": dict(sample.get("labels") or {}),
                }
            )
    output = sorted(output, key=lambda point: point["timestamp"])
    if output:
        origin = _parse(output[0]["timestamp"])
        for point in output:
            point["elapsed_seconds"] = (_parse(point["timestamp"]) - origin).total_seconds()
    return output


def _nearest(point: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]):
    matching = [
        candidate
        for candidate in candidates
        if candidate.get("host_id") == point.get("host_id")
        and candidate.get("labels") == point.get("labels")
    ]
    if not matching:
        return None
    target = float(point["elapsed_seconds"])
    candidate = min(matching, key=lambda item: abs(float(item["elapsed_seconds"]) - target))
    distance = abs(float(candidate["elapsed_seconds"]) - target)
    windows = [
        value
        for value in (point.get("freshness_seconds"), candidate.get("freshness_seconds"))
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not windows or distance > max(windows):
        return None
    return candidate


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("history timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _format(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
