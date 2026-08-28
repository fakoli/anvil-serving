from __future__ import annotations

import io
import json
import datetime as dt

import pytest

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.cli import main
from anvil_serving.media.comfyui import WorkflowCompatibility
from anvil_serving.media.contracts import JobState, ParameterBinding, ParameterSpec, RenderedWorkflow, WorkflowDescriptor
from anvil_serving.media.errors import MediaError
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
        return True

    def find_prompt(self, job_id):
        return None

    def interrupt_exclusive_prompt(self):
        raise AssertionError("aggregate CLI observation must never interrupt a running prompt")


class _ColdBackend(_Backend):
    def __init__(self):
        super().__init__()
        self.ready = False

    def compatibility(self, workflow):
        return WorkflowCompatibility(
            workflow.id,
            workflow.version,
            self.ready,
            self.ready,
            reasons=() if self.ready else ("not_ready",),
        )


class _RecoveryBackend(_Backend):
    def __init__(self, recovered_prompt):
        super().__init__()
        self.recovered_prompt = recovered_prompt
        self.recovery_calls = []

    def submit(self, workflow, *, job_id):
        raise AssertionError("an ambiguous remote submission must never be repeated")

    def find_prompt(self, job_id):
        self.recovery_calls.append(job_id)
        if isinstance(self.recovered_prompt, MediaError):
            raise self.recovered_prompt
        return self.recovered_prompt


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


def test_cold_worker_requires_approval_then_exact_retry_submits_once(tmp_path):
    backend = _ColdBackend()
    preview_calls = []

    def preview(job_id, principal, service):
        preview_calls.append((job_id, principal, service))
        return {
            "transactionId": "preview-transaction",
            "service": service,
            "action": "prepare",
            "humanRequired": True,
            "manifest": "serves.comfyui.toml",
            "controllerReceipt": {"privatePath": "must-not-escape"},
        }

    operations = MediaOperations(
        _Registry(),
        MediaJobStore(tmp_path / "jobs.sqlite3"),
        ArtifactStore(tmp_path / "artifacts"),
        lifecycle_preview=preview,
    )
    waiting = operations.workflow_run(
        "image.test",
        "v1",
        {"prompt": "mountain"},
        principal="hermes",
        idempotency_key="cold-request",
        backend=backend,
    )
    assert waiting["job"]["state"] == JobState.AWAITING_APPROVAL.value
    assert backend.submissions == 0
    assert preview_calls == [
        (waiting["job"]["id"], "hermes", "media-worker")
    ]
    approval = waiting["job"]["approval"]
    assert approval["operatorAction"]["tool"] == "media_worker_prepare"
    assert approval["operatorAction"]["arguments"]["manifest"] == (
        "serves.comfyui.toml"
    )
    assert "controllerReceipt" not in approval
    assert "privatePath" not in json.dumps(approval)

    operations.jobs.transition(
        waiting["job"]["id"],
        JobState.PREPARING,
        principal="hermes",
        reason="operator_approved_worker_start",
    )
    backend.ready = True
    resumed = operations.workflow_run(
        "image.test",
        "v1",
        {"prompt": "mountain"},
        principal="hermes",
        idempotency_key="cold-request",
        backend=backend,
    )
    assert resumed["created"] is False
    assert resumed["job"]["state"] == JobState.QUEUED.value
    assert backend.submissions == 1


def test_failed_cold_worker_preview_is_terminal_without_backend_submission(tmp_path):
    backend = _ColdBackend()

    def fail_preview(_job_id, _principal, _service):
        raise RuntimeError("controller unavailable")

    operations = MediaOperations(
        _Registry(),
        MediaJobStore(tmp_path / "jobs.sqlite3"),
        ArtifactStore(tmp_path / "artifacts"),
        lifecycle_preview=fail_preview,
    )
    with pytest.raises(MediaError) as error:
        operations.workflow_run(
            "image.test",
            "v1",
            {"prompt": "mountain"},
            principal="hermes",
            idempotency_key="failed-preview",
            backend=backend,
        )
    assert error.value.code == "media_lifecycle_preview_failed"
    job = operations.jobs.lookup_idempotency(
        principal="hermes",
        workflow_id="image.test",
        workflow_version="v1",
        input_digest=canonical_digest({"prompt": "mountain"}),
        idempotency_key="failed-preview",
    )
    assert job is not None and job.state == JobState.FAILED
    assert backend.submissions == 0


def test_retry_recovers_crash_before_cold_worker_preview(tmp_path):
    backend = _ColdBackend()
    preview_calls = []

    def preview(job_id, principal, service):
        preview_calls.append((job_id, principal, service))
        return {
            "transactionId": "recovered-preview",
            "service": service,
            "action": "prepare",
            "humanRequired": True,
            "manifest": "serves.comfyui.toml",
        }

    operations = MediaOperations(
        _Registry(),
        MediaJobStore(tmp_path / "jobs.sqlite3"),
        ArtifactStore(tmp_path / "artifacts"),
        lifecycle_preview=preview,
    )
    accepted, created = operations.jobs.create(
        principal="hermes",
        workflow_id="image.test",
        workflow_version="v1",
        input_digest=canonical_digest({"prompt": "mountain"}),
        idempotency_key="pre-preview-crash",
    )
    assert created is True and accepted.state == JobState.ACCEPTED

    recovered = operations.workflow_run(
        "image.test",
        "v1",
        {"prompt": "mountain"},
        principal="hermes",
        idempotency_key="pre-preview-crash",
        backend=backend,
    )

    assert recovered["created"] is False
    assert recovered["job"]["state"] == JobState.AWAITING_APPROVAL.value
    assert preview_calls == [(accepted.id, "hermes", "media-worker")]
    assert backend.submissions == 0


@pytest.mark.parametrize(
    ("recovered_prompt", "expected_state", "expected_reason", "expected_prompt"),
    [
        (
            "prompt-recovered",
            JobState.QUEUED,
            "backend_submission_recovered",
            "prompt-recovered",
        ),
        (None, JobState.FAILED, "backend_submission_outcome_unknown", ""),
        (
            MediaError("backend_unavailable", "backend unavailable", status=503),
            JobState.FAILED,
            "backend_submission_recovery_failed",
            "",
        ),
    ],
)
def test_retry_never_resubmits_ambiguous_remote_acceptance(
    tmp_path,
    recovered_prompt,
    expected_state,
    expected_reason,
    expected_prompt,
):
    backend = _RecoveryBackend(recovered_prompt)
    operations = MediaOperations(
        _Registry(),
        MediaJobStore(tmp_path / "jobs.sqlite3"),
        ArtifactStore(tmp_path / "artifacts"),
    )
    old = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
    job, _ = operations.jobs.create(
        principal="hermes",
        workflow_id="image.test",
        workflow_version="v1",
        input_digest=canonical_digest({"prompt": "mountain"}),
        idempotency_key="ambiguous-submit",
        now=old,
    )
    operations.jobs.transition(
        job.id,
        JobState.PREPARING,
        principal="hermes",
        now=old,
    )
    operations.jobs.transition(
        job.id,
        JobState.SUBMITTING,
        principal="hermes",
        now=old,
    )

    recovered = operations.workflow_run(
        "image.test",
        "v1",
        {"prompt": "mountain"},
        principal="hermes",
        idempotency_key="ambiguous-submit",
        backend=backend,
    )

    assert recovered["created"] is False
    assert recovered["job"]["state"] == expected_state.value
    persisted = operations.jobs.get(job.id, principal="hermes")
    assert persisted.events[-1].reason == expected_reason
    assert persisted.backend_prompt_id == expected_prompt
    assert backend.recovery_calls == [job.id]
    assert backend.submissions == 0


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


def test_workflow_run_dry_run_validates_without_creating_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("anvil_serving.media.cli.WorkflowRegistry", lambda _path: _Registry())
    state = tmp_path / "missing" / "jobs.sqlite3"
    artifacts = tmp_path / "missing-artifacts"
    assert main([
        "workflow", "run", "image.test", "--version", "v1",
        "--parameters", '{"prompt":"mountain"}', "--principal", "hermes",
        "--backend-url", "http://127.0.0.1:65534", "--state-db", str(state),
        "--artifact-root", str(artifacts), "--dry-run",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dryRun"] is True
    assert payload["backendContacted"] is False
    assert payload["jobSubmitted"] is False
    assert not state.exists()
    assert not artifacts.exists()


def test_job_cancel_dry_run_is_non_mutating_and_requires_backend_shape(tmp_path, capsys):
    state = tmp_path / "missing" / "jobs.sqlite3"
    artifacts = tmp_path / "missing-artifacts"
    assert main([
        "job", "cancel", "job_0123456789abcdef", "--principal", "hermes",
        "--backend-url", "http://127.0.0.1:65534", "--state-db", str(state),
        "--artifact-root", str(artifacts), "--dry-run",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "backendContacted": False,
        "dryRun": True,
        "jobId": "job_0123456789abcdef",
        "ownershipCheckDeferred": True,
        "principal": "hermes",
        "schema": "anvil-serving.media-job-cancel-plan/v1",
        "stateChanged": False,
    }
    assert not state.exists()
    assert not artifacts.exists()
