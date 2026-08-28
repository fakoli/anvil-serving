from __future__ import annotations

import io
import json

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.cli import main
from anvil_serving.media.comfyui import WorkflowCompatibility
from anvil_serving.media.contracts import ParameterBinding, ParameterSpec, RenderedWorkflow, WorkflowDescriptor
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.operations import MediaOperations
from anvil_serving.media.workflows import canonical_digest


def _workflow() -> WorkflowDescriptor:
    graph = {"1": {"class_type": "Test", "inputs": {"prompt": ""}}}
    return WorkflowDescriptor(
        id="image.test",
        version="v1",
        kind="image",
        service_target="media-worker",
        graph_digest=canonical_digest(graph),
        parameters={"prompt": ParameterSpec("string", max_length=100)},
        bindings=(ParameterBinding("prompt", "1", "prompt"),),
        output_nodes=("1",),
        output_mime_types=("image/png",),
        available=True,
        unavailable_reasons=(),
    )


class _Registry:
    descriptor = _workflow()

    def list(self):
        return [self.descriptor.as_public_dict()]

    def get(self, workflow_id, version):
        assert (workflow_id, version) == (self.descriptor.id, self.descriptor.version)
        return self.descriptor

    def render(self, workflow_id, version, parameters):
        descriptor = self.get(workflow_id, version)
        validated = descriptor.validate_parameters(parameters)
        graph = {"1": {"class_type": "Test", "inputs": {"prompt": validated["prompt"]}}}
        return RenderedWorkflow(descriptor, graph, canonical_digest(validated))


class _Backend:
    def __init__(self):
        self.submissions = 0
        self.deleted = []

    def compatibility(self, workflow):
        return WorkflowCompatibility(workflow.id, workflow.version, True, True)

    def submit(self, workflow, *, job_id):
        self.submissions += 1
        return "prompt-one"

    def delete_queued_prompt(self, prompt_id):
        self.deleted.append(prompt_id)

    def interrupt_exclusive_prompt(self):
        raise AssertionError("aggregate CLI observation must never interrupt a running prompt")


def test_operations_run_retry_status_and_cancel_share_domain_records(tmp_path):
    backend = _Backend()
    operations = MediaOperations(
        _Registry(),
        MediaJobStore(tmp_path / "jobs.sqlite3"),
        ArtifactStore(tmp_path / "artifacts"),
    )
    first = operations.workflow_run(
        "image.test", "v1", {"prompt": "mountain"},
        principal="hermes", idempotency_key="same-request", backend=backend,
    )
    retry = operations.workflow_run(
        "image.test", "v1", {"prompt": "mountain"},
        principal="hermes", idempotency_key="same-request", backend=backend,
    )
    assert first["job"] == retry["job"]
    assert retry["created"] is False
    assert backend.submissions == 1
    assert operations.job_status(first["job"]["id"], principal="hermes")["job"] == first["job"]
    canceled = operations.job_cancel(first["job"]["id"], principal="hermes", backend=backend)
    assert canceled["canceled"] is True
    assert backend.deleted == ["prompt-one"]


def test_cli_accepts_storage_options_at_declared_leaf_position(tmp_path, capsys):
    registry = "configs/media/workflows/registry.json"
    assert main([
        "capabilities",
        "--registry", registry,
        "--state-db", str(tmp_path / "jobs.sqlite3"),
        "--artifact-root", str(tmp_path / "artifacts"),
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["capabilities"] == [
        "image-generation", "video-generation", "named-workflow"
    ]
    assert {item["kind"] for item in payload["workflows"]} == {"image", "video"}


def test_full_video_is_never_inlined_by_artifact_inspection(tmp_path):
    operations = MediaOperations(
        _Registry(), MediaJobStore(tmp_path / "jobs.sqlite3"), ArtifactStore(tmp_path / "artifacts")
    )
    job = operations.jobs.create(
        principal="hermes", workflow_id="image.test", workflow_version="v1",
        input_digest="a" * 64, idempotency_key="artifact-job",
    )[0]
    artifact = operations.artifacts.ingest(
        job, io.BytesIO(b"\x00\x00\x00\x18ftypmp42payload"),
        media_type="video/mp4", max_bytes=1024, retention_seconds=60,
    )
    metadata = operations.artifact_inspect(artifact.id, principal="hermes")
    assert metadata["artifact"]["resource"] == f"/artifacts/{artifact.id}"
    assert "data" not in metadata["artifact"]
