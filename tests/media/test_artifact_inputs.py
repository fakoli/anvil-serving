import datetime as dt
import io
import json

import pytest

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.comfyui import WorkflowCompatibility
from anvil_serving.media.contracts import utc_now
from anvil_serving.media.errors import MediaError
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.operations import MediaOperations
from anvil_serving.media.workflows import WorkflowRegistry, canonical_digest
from tests.media.test_artifacts import job, mp4, png
from tests.media.test_image_workflow_integration import _registry


class Backend:
    base_url = "http://127.0.0.1:8188"

    def __init__(self):
        self.uploads = []
        self.submissions = []

    def compatibility(self, workflow):
        return WorkflowCompatibility(workflow.id, workflow.version, True, True)

    def upload_input(self, filename, content, *, max_bytes):
        assert len(content) <= max_bytes
        self.uploads.append((filename, content))
        return filename

    def submit(self, workflow, *, job_id):
        self.submissions.append(workflow)
        return "prompt_test"


def setup(tmp_path, *, media_types=None):
    _registry(tmp_path, kind="video", mime="video/mp4")
    graph = {"1": {"class_type": "LoadVideo", "inputs": {"file": ""}}}
    (tmp_path / "graph.json").write_text(json.dumps(graph))
    path = tmp_path / "descriptor.json"
    descriptor = json.loads(path.read_text())
    spec = {"kind": "artifact"}
    if media_types is not None:
        spec["media_types"] = media_types
    descriptor.update(
        parameters={"source": spec},
        bindings=[{"parameter": "source", "node": "1", "input": "file"}],
        graph_digest=canonical_digest(graph),
        required_nodes=["LoadVideo"],
        available=True,
        unavailable_reasons=[],
    )
    path.write_text(json.dumps(descriptor))
    registry = WorkflowRegistry(tmp_path / "registry.json")
    operations = MediaOperations(
        registry, MediaJobStore(tmp_path / "jobs.sqlite3"), ArtifactStore(tmp_path / "artifacts")
    )
    return operations, Backend()


def source(operations, content=None, *, media_type="video/mp4", expired=False):
    return operations.artifacts.ingest(
        job(), io.BytesIO(mp4() if content is None else content), media_type=media_type,
        max_bytes=4096, retention_seconds=60,
        now=utc_now() - dt.timedelta(minutes=2) if expired else None,
    )


def run(operations, backend, artifact_id):
    return operations.workflow_run(
        "video.test", "v1", {"source": artifact_id}, principal="hermes",
        idempotency_key="same-request", backend=backend,
    )


def test_owned_mp4_is_staged_without_mutating_request_or_resubmitting(tmp_path):
    operations, backend = setup(tmp_path)
    artifact = source(operations)
    first = run(operations, backend, artifact.id)
    assert backend.uploads == [(f"anvil-{artifact.sha256}.mp4", mp4())]
    submitted = backend.submissions[0]
    assert submitted.graph["1"]["inputs"]["file"] == backend.uploads[0][0]
    assert submitted.resolved_parameters == {"source": artifact.id}
    assert operations.registry.render("video.test", "v1", {"source": artifact.id}).graph["1"]["inputs"]["file"] == artifact.id
    metadata = operations.artifacts._metadata_path(artifact.id)
    record = json.loads(metadata.read_text())
    record["expires_at"] = (utc_now() - dt.timedelta(minutes=1)).isoformat()
    metadata.write_text(json.dumps(record))
    again = run(operations, backend, artifact.id)
    assert again["job"]["id"] == first["job"]["id"] and again["created"] is False
    assert len(backend.uploads) == len(backend.submissions) == 1
    assert str(tmp_path) not in json.dumps(first)


@pytest.mark.parametrize("problem", ["wrong_owner", "expired", "oversize", "corrupted", "mime"])
def test_invalid_source_never_uploads_or_submits(tmp_path, monkeypatch, problem):
    operations, backend = setup(tmp_path, media_types=["video/mp4"])
    artifact = source(
        operations, png() if problem == "mime" else None,
        media_type="image/png" if problem == "mime" else "video/mp4",
        expired=problem == "expired",
    )
    if problem == "wrong_owner":
        metadata = operations.artifacts._metadata_path(artifact.id)
        record = json.loads(metadata.read_text())
        record["principal"] = "other"
        metadata.write_text(json.dumps(record))
    if problem == "oversize":
        monkeypatch.setattr("anvil_serving.media.operations.MAX_UPLOAD_BYTES", 1)
    if problem == "corrupted":
        operations.artifacts._data_path(artifact.id).write_bytes(b"x" * artifact.byte_length)
    with pytest.raises(MediaError):
        run(operations, backend, artifact.id)
    assert backend.uploads == backend.submissions == []


def test_descriptor_publishes_accepted_artifact_media_types(tmp_path):
    operations, _ = setup(tmp_path, media_types=["video/mp4"])
    spec = operations.workflow_show("video.test", "v1")["workflow"]["schema"]["properties"]["source"]
    assert spec["format"] == "anvil-artifact-id"
    assert spec["x-anvil-media-types"] == ["video/mp4"]
