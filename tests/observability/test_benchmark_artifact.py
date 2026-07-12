from __future__ import annotations

import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from anvil_serving.observability.benchmark.artifact import (
    ArtifactMetadata,
    write_capture_artifact,
)
from anvil_serving.observability.benchmark.session import CaptureSession
from anvil_serving.observability.retention import RetentionStore


NOW = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)
SECRET = "benchmark-super-secret"


def _snapshot(*, gap_domain=False):
    metrics = [
        "host.memory.used",
        "container.memory.used",
        "gpu.memory.used",
        "service.health",
    ]
    return {
        "samples": [
            {
                "metric": metric,
                "host_id": "dark",
                "value": index,
                "detail": f"Bearer {SECRET}",
                "freshness": {
                    "age_seconds": 0.5 if not gap_domain else 4,
                    "stale_after_seconds": 10,
                    "is_stale": False,
                },
            }
            for index, metric in enumerate(metrics)
        ]
    }


def _metadata():
    return ArtifactMetadata(
        topology={"id": "fixture", "token": SECRET},
        hosts=({"id": "dark"}, {"id": "mini"}),
        hardware=({"gpu": "fixture"},),
        collector_versions={"host": "1"},
        capabilities=("host-resources", "nvidia-gpu", "containers", "service-health"),
        sampling_intervals={"core": 1, "costly": 2},
    )


def _complete_session():
    store = RetentionStore()
    session = CaptureSession(store, session_id="fixture-session")
    session.start(now=NOW)
    session.collect(lambda: _snapshot(), now=NOW, expected_interval_seconds=1)
    session.collect(
        lambda: _snapshot(gap_domain=True),
        now=NOW + timedelta(seconds=3),
        expected_interval_seconds=1,
    )
    session.stop(now=NOW + timedelta(minutes=1))
    session.observe_stability(stable=False, now=NOW + timedelta(minutes=16))
    return session


def test_raw_artifact_is_compressed_external_complete_and_secret_free(tmp_path) -> None:
    repo = tmp_path / "repo"
    evidence = tmp_path / "private-evidence"
    repo.mkdir()
    result = write_capture_artifact(
        _complete_session(), _metadata(), evidence_root=evidence, repo_root=repo, secrets=(SECRET,)
    )

    compressed = result.raw_path.read_bytes()
    raw = json.loads(gzip.decompress(compressed))
    serialized = json.dumps(raw)
    assert result.raw_path.parent == evidence
    assert result.manifest["sha256"] == hashlib.sha256(compressed).hexdigest()
    assert result.manifest["compressed_bytes"] == len(compressed)
    assert result.manifest["capture_quality"] == "degraded"
    assert result.manifest["gap_count"] == 1
    assert result.manifest["domain_metrics"] == {
        "system": 2,
        "container": 2,
        "gpu": 2,
        "service": 2,
        "other": 0,
    }
    assert raw["metadata"]["sampling_intervals"] == {"core": 1, "costly": 2}
    assert raw["time_alignment"]["quality"] == "degraded"
    assert SECRET not in serialized


def test_raw_evidence_cannot_be_written_inside_git_repo(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="outside"):
        write_capture_artifact(
            _complete_session(),
            _metadata(),
            evidence_root=repo / "artifacts",
            repo_root=repo,
            secrets=(SECRET,),
        )


def test_existing_raw_artifact_is_never_overwritten(tmp_path) -> None:
    repo = tmp_path / "repo"
    evidence = tmp_path / "private"
    repo.mkdir()
    evidence.mkdir()
    target = evidence / "benchmark-telemetry-fixture-session.json.gz"
    target.write_bytes(b"existing-evidence")

    with pytest.raises(FileExistsError, match="already exists"):
        write_capture_artifact(
            _complete_session(), _metadata(), evidence_root=evidence, repo_root=repo
        )

    assert target.read_bytes() == b"existing-evidence"


def test_incomplete_capture_is_flagged_and_finding_is_linked(tmp_path) -> None:
    repo = tmp_path / "repo"
    finding_dir = repo / "docs" / "findings"
    finding_dir.mkdir(parents=True)
    index = finding_dir / "README.md"
    index.write_text(
        "# Findings index\n\n| Date | File | Subject |\n|------|------|---------|\n",
        encoding="utf-8",
    )
    session = CaptureSession(RetentionStore(), session_id="incomplete")
    session.start(now=NOW)
    finding = finding_dir / "2026-07-11-capture.md"

    result = write_capture_artifact(
        session,
        _metadata(),
        evidence_root=tmp_path / "external",
        repo_root=repo,
        secrets=(SECRET,),
        finding_path=finding,
        findings_index=index,
    )

    assert result.manifest["capture_quality"] == "incomplete"
    assert result.manifest["artifact_locator"].startswith("anvil-evidence://")
    assert result.manifest["sha256"] in finding.read_text(encoding="utf-8")
    assert SECRET not in finding.read_text(encoding="utf-8")
    assert f"]({finding.name})" in index.read_text(encoding="utf-8")
