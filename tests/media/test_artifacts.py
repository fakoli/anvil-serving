import datetime as dt
import base64
import io

import pytest

from anvil_serving.media import (
    ArtifactStore,
    JobEvent,
    JobState,
    MediaError,
    MediaJob,
)
from anvil_serving.media.artifacts import MAX_MCP_IMAGE_BYTES
from anvil_serving.control_plane.mcp.controller_client import (
    MAX_REMOTE_CONTROLLER_RESPONSE_BYTES,
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


def test_ingest_hashes_and_hides_source_path(tmp_path):
    artifact = ArtifactStore(tmp_path).ingest(
        job(), io.BytesIO(png()), media_type="image/png", max_bytes=1024, retention_seconds=60, now=NOW
    )
    public = artifact.as_public_dict()
    assert public["byteLength"] == len(png())
    assert len(public["sha256"]) == 64
    assert str(tmp_path) not in repr(public)
    assert "source_path" not in public


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


def test_video_ranges_are_bounded_and_preview_is_never_inline(tmp_path):
    store = ArtifactStore(tmp_path)
    artifact = store.ingest(job(), io.BytesIO(mp4()), media_type="video/mp4", max_bytes=1024, retention_seconds=60)
    payload = store.read(artifact.id, principal="hermes", start=4, end=11)
    assert payload.data == b"ftypisom"
    assert store.inline_preview(artifact.id, principal="hermes") is None
    assert store.mcp_image_content(artifact.id, principal="hermes") is None
    with pytest.raises(MediaError) as error:
        store.read(artifact.id, principal="hermes", start=0, end=artifact.byte_length)
    assert error.value.status == 416


def test_bounded_image_can_be_returned_as_native_mcp_content(tmp_path):
    store = ArtifactStore(tmp_path)
    artifact = store.ingest(
        job(),
        io.BytesIO(png()),
        media_type="image/png",
        max_bytes=1024,
        retention_seconds=60,
    )
    content = store.mcp_image_content(artifact.id, principal="hermes")
    assert content["type"] == "image"
    assert content["mimeType"] == "image/png"
    assert base64.b64decode(content["data"]) == png()


def test_native_image_bound_fits_controller_and_sdk_stdio_frames():
    encoded_bytes = 4 * ((MAX_MCP_IMAGE_BYTES + 2) // 3)
    assert encoded_bytes + 1024 * 1024 < MAX_REMOTE_CONTROLLER_RESPONSE_BYTES
    assert MAX_REMOTE_CONTROLLER_RESPONSE_BYTES == 10 * 1024 * 1024


def test_oversize_removes_temporary_bytes(tmp_path):
    store = ArtifactStore(tmp_path)
    with pytest.raises(MediaError) as error:
        store.ingest(job(), io.BytesIO(png(b"x" * 100)), media_type="image/png", max_bytes=16, retention_seconds=60)
    assert error.value.code == "artifact_too_large"
    assert list(tmp_path.iterdir()) == []
