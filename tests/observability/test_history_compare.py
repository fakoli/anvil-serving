from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from anvil_serving.observability.api import TelemetryRegistry, run_server_in_thread
from anvil_serving.observability.dashboard.app import create_dashboard_server

from anvil_serving.observability.dashboard.history import (
    BenchmarkHistory,
    RetainedBenchmark,
    bounded_history,
)
from anvil_serving.observability.retention import RetentionStore


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)


def _snapshot(timestamp, value, *, status="ok"):
    return {
        "samples": [
            {
                "metric": "host.cpu.utilization",
                "source_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "host_id": "dark",
                "labels": {},
                "value": value,
                "capability_status": status,
                "freshness": {
                    "age_seconds": 0,
                    "stale_after_seconds": 3,
                    "is_stale": status == "stale",
                },
            }
        ]
    }


def _store(offsets, *, base=NOW, expected=2):
    store = RetentionStore()
    for seconds, value, status in offsets:
        timestamp = base + timedelta(seconds=seconds)
        store.add(
            _snapshot(timestamp, value, status=status),
            observed_at=timestamp,
            probe_duration_seconds=0.1,
            expected_interval_seconds=expected,
        )
    return store


def test_bounded_recent_history_comes_from_retention() -> None:
    store = _store(((0, 10, "ok"), (2, 20, "ok")))

    result = bounded_history(store)

    assert result["retained_bytes"] == store.total_bytes
    assert result["frame_count"] == 2
    assert len(result["frames"]) == 2


def test_comparison_aligns_different_sampling_by_timestamp_and_freshness() -> None:
    current = _store(((0, 10, "ok"), (2, 20, "ok"), (4, 30, "ok")))
    benchmark_store = _store(
        ((1, 11, "ok"), (5, 31, "ok")),
        base=NOW + timedelta(hours=1),
        expected=5,
    )
    history = BenchmarkHistory(
        (
            RetainedBenchmark(
                "benchmark-a",
                benchmark_store.frames(),
                {"capture_quality": "complete"},
            ),
        )
    )

    result = history.compare("benchmark-a", current, metric="host.cpu.utilization")

    assert len(result["current_points"]) == 3
    assert [item["benchmark"]["value"] for item in result["aligned"]] == [11, 11, 31]
    assert not any(item["gap"] for item in result["aligned"])
    assert result["benchmark_points"][0]["timestamp"] != result["current_points"][0]["timestamp"]
    assert result["benchmark_points"][0]["elapsed_seconds"] == 0


def test_benchmark_gaps_are_explicit_and_not_zero_filled() -> None:
    current = _store(((0, 10, "ok"), (10, 20, "ok")))
    benchmark_store = _store(((0, 11, "ok"), (10, None, "stale")))
    history = BenchmarkHistory((RetainedBenchmark("benchmark-gap", benchmark_store.frames(), {}),))

    result = history.compare("benchmark-gap", current, metric="host.cpu.utilization")

    assert result["aligned"][1]["benchmark"] is not None
    assert result["aligned"][1]["benchmark"]["value"] is None
    assert result["aligned"][1]["gap"] is True
    assert result["benchmark_gaps"]


def test_retained_session_catalog_is_selectable() -> None:
    store = _store(((0, 1, "ok"),))
    history = BenchmarkHistory(
        (RetainedBenchmark("session-2", store.frames(), {"capture_quality": "degraded"}),)
    )

    assert history.list_sessions() == [
        {
            "session_id": "session-2",
            "capture_quality": "degraded",
            "frame_count": 1,
            "gap_count": 0,
        }
    ]


def test_dashboard_history_api_selects_session_and_rejects_bad_query() -> None:
    current = _store(((0, 10, "ok"),))
    benchmark = _store(((1, 11, "ok"),))
    sessions = BenchmarkHistory(
        (RetainedBenchmark("selectable", benchmark.frames(), {"capture_quality": "complete"}),)
    )
    server = create_dashboard_server(
        TelemetryRegistry(), port=0, retention=current, benchmark_history=sessions
    )
    thread = run_server_in_thread(server)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/v1/history", timeout=2) as response:
            history = json.loads(response.read())["data"]
        query = urllib.parse.urlencode({"session": "selectable", "metric": "host.cpu.utilization"})
        with urllib.request.urlopen(base + "/v1/compare?" + query, timeout=2) as response:
            comparison = json.loads(response.read())["data"]
        with pytest.raises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(base + "/v1/compare?session=selectable", timeout=2)
        assert bad.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert history["sessions"][0]["session_id"] == "selectable"
    assert comparison["aligned"][0]["benchmark"]["value"] == 11
