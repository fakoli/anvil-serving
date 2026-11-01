import datetime as dt
import io

import pytest

from anvil_serving.media import (
    ArtifactStore,
    JobEvent,
    JobState,
    MediaError,
    MediaJob,
)


NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)


def job():
    return MediaJob(
        id="job_0123456789abcdef",
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        state=JobState.RUNNING,
        created_at=NOW,
        updated_at=NOW,
        events=(JobEvent(1, JobState.RUNNING, NOW),),
        input_digest="a" * 64,
    )


def png(payload=b"hello"):
    return b"\x89PNG\r\n\x1a\n" + payload


def mp4(payload=b"video"):
    return b"\x00\x00\x00\x18ftypisom" + payload


def test_cross_principal_expiry_and_signature_fail_as_not_found_or_invalid(tmp_path):
    store = ArtifactStore(tmp_path)
    artifact = store.ingest(job(), io.BytesIO(png()), media_type="image/png", max_bytes=1024, retention_seconds=60, now=NOW)
    with pytest.raises(MediaError) as denied:
        store.metadata(artifact.id, principal="other", now=NOW)
    assert denied.value.code == "artifact_not_found"
    with pytest.raises(MediaError) as expired:
        store.metadata(artifact.id, principal="hermes", now=NOW + dt.timedelta(minutes=2))
    assert expired.value.code == "artifact_not_found"
    with pytest.raises(MediaError) as invalid:
        store.ingest(job(), io.BytesIO(b"not png"), media_type="image/png", max_bytes=1024, retention_seconds=60)
    assert invalid.value.code == "artifact_signature_invalid"
