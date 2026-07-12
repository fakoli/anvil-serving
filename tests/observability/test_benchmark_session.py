from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anvil_serving.observability.benchmark.session import CaptureSession
from anvil_serving.observability.retention import RetentionStore


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _snapshot(value=1):
    return {"samples": [{"host_id": "dark", "metric": "host.memory.used", "value": value}]}


def test_programmatic_session_includes_five_minutes_of_pre_history() -> None:
    store = RetentionStore()
    for minutes in (6, 4, 1):
        store.add(
            _snapshot(minutes),
            observed_at=NOW - timedelta(minutes=minutes),
            probe_duration_seconds=0.1,
            expected_interval_seconds=2,
        )
    session = CaptureSession(store)

    status = session.start(now=NOW)

    assert status["state"] == "capturing"
    assert status["pre_history_frames"] == 2
    assert store.capture_active is True


def test_collection_failure_is_reported_without_escaping() -> None:
    session = CaptureSession(RetentionStore())
    session.start(now=NOW)

    result = session.collect(
        lambda: (_ for _ in ()).throw(PermissionError("collector denied")), now=NOW
    )

    assert result == {"ok": False, "state": "capturing", "error": "capture-degraded"}
    assert "collector denied" in session.failures[0]["error"]


def test_stop_is_nonblocking_and_waits_for_minimum_post_history_and_stability() -> None:
    store = RetentionStore()
    session = CaptureSession(store)
    session.start(now=NOW)
    for phase in ("load", "readiness", "warm-up", "measured-run"):
        session.mark(phase, now=NOW)
    session.stop(now=NOW + timedelta(minutes=1))

    assert session.state == "post-capture"
    assert (
        session.observe_stability(stable=True, now=NOW + timedelta(minutes=5, seconds=30))["state"]
        == "post-capture"
    )
    status = session.observe_stability(stable=True, now=NOW + timedelta(minutes=6, seconds=31))

    assert status["state"] == "complete"
    assert store.capture_active is False
    assert [marker["phase"] for marker in status["markers"]] == [
        "load",
        "readiness",
        "warm-up",
        "measured-run",
        "release",
        "stabilization",
    ]


def test_hard_post_capture_limit_completes_even_without_stability() -> None:
    store = RetentionStore()
    session = CaptureSession(store)
    session.start(now=NOW)
    session.stop(now=NOW)

    status = session.observe_stability(stable=False, now=NOW + timedelta(minutes=15))

    assert status["state"] == "complete"
    assert store.capture_active is False
