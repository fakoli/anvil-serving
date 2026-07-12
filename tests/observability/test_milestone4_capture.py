from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

from anvil_serving.observability.benchmark.artifact import (
    ArtifactMetadata,
    write_capture_artifact,
)
from anvil_serving.observability.benchmark.overhead import (
    BenchmarkOutcome,
    ResourceObservation,
    evaluate_overhead,
)
from anvil_serving.observability.benchmark.session import CaptureSession
from anvil_serving.observability.dashboard.history import BenchmarkHistory, RetainedBenchmark
from anvil_serving.observability.retention import RetentionStore


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
MIB = 1024 * 1024


def _snapshot(timestamp: datetime, value: float, secret: str = "") -> dict[str, object]:
    return {
        "samples": [
            {
                "metric": "host.memory.used",
                "source_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "host_id": "dark",
                "labels": {},
                "value": value,
                "capability_status": "ok",
                "detail": secret,
                "freshness": {
                    "age_seconds": 0,
                    "stale_after_seconds": 5,
                    "is_stale": False,
                },
            }
        ]
    }


def test_milestone4_capture_artifact_comparison_and_overhead_exit_gate(tmp_path) -> None:
    secret = "milestone-four-secret"
    store = RetentionStore()
    prehistory_time = NOW - timedelta(seconds=1)
    store.add(
        _snapshot(prehistory_time, 40),
        observed_at=prehistory_time,
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )
    session = CaptureSession(store, session_id="milestone-four")
    session.start(now=NOW)
    for phase in ("load", "readiness", "warm-up", "measured-run"):
        session.mark(phase, now=NOW)
    session.collect(lambda: _snapshot(NOW, 50, secret), now=NOW)
    session.stop(now=NOW + timedelta(minutes=1))
    session.observe_stability(stable=True, now=NOW + timedelta(minutes=5))
    session.observe_stability(stable=True, now=NOW + timedelta(minutes=6, seconds=1))

    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = write_capture_artifact(
        session,
        ArtifactMetadata(
            topology={"name": "dark-plus-mini"},
            hosts=({"id": "dark"}, {"id": "mini"}),
            hardware=({"gpu": "fixture"},),
            collector_versions={"host": "1"},
            capabilities=("host-resources",),
            sampling_intervals={"core": 1, "costly": 2},
        ),
        evidence_root=tmp_path / "private",
        repo_root=repo,
        secrets=(secret,),
    )

    current = RetentionStore()
    current.add(
        _snapshot(NOW + timedelta(hours=1), 55),
        observed_at=NOW + timedelta(hours=1),
        probe_duration_seconds=0.1,
        expected_interval_seconds=2,
    )
    history = BenchmarkHistory(
        (RetainedBenchmark(session.session_id, tuple(session.captured), artifact.manifest),)
    )
    comparison = history.compare(session.session_id, current, metric="host.memory.used")
    observations = [
        ResourceObservation(index, 0.5, 50 * MIB, 0, index, index, 0) for index in range(3)
    ]
    overhead = evaluate_overhead(
        "benchmark",
        observations,
        collection_off=BenchmarkOutcome(100, 1),
        collection_on=BenchmarkOutcome(100.5, 1.005),
    )

    raw = gzip.decompress(artifact.raw_path.read_bytes()).decode()
    assert session.state == "complete"
    assert len(session.pre_history) == 1
    assert [marker.phase for marker in session.markers] == [
        "load",
        "readiness",
        "warm-up",
        "measured-run",
        "release",
        "stabilization",
    ]
    assert artifact.raw_path.parent == tmp_path / "private"
    assert artifact.manifest["capture_quality"] == "complete"
    assert artifact.manifest["artifact_locator"].startswith("anvil-evidence://")
    assert secret not in raw
    assert json.loads(raw)["metadata"]["sampling_intervals"] == {"core": 1, "costly": 2}
    assert comparison["aligned"][0]["benchmark"]["value"] == 50
    assert comparison["aligned"][0]["gap"] is False
    assert overhead["passed"] is True
    assert overhead["limits"]["capture_disk_write_bytes"] == 0
