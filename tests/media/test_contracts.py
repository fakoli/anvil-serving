import datetime as dt

import pytest

from anvil_serving.media import (
    JobEvent,
    JobState,
    MediaArtifact,
    MediaError,
    MediaJob,
    ParameterBinding,
    ParameterSpec,
    QualityProfile,
    WorkflowDescriptor,
)


NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)
JOB_ID = "job_0123456789abcdef"
ARTIFACT_ID = "art_0123456789abcdef"


def descriptor(**changes):
    values = {
        "id": "image.test-v1",
        "version": "v1",
        "kind": "image",
        "service_target": "media-worker",
        "graph_digest": "a" * 64,
        "parameters": {
            "prompt": ParameterSpec("string", max_length=32),
            "width": ParameterSpec("integer", minimum=64, maximum=1024),
        },
        "bindings": (
            ParameterBinding("prompt", "1", "text"),
            ParameterBinding("width", "2", "width"),
        ),
        "output_nodes": ("3",),
        "output_mime_types": ("image/png",),
    }
    values.update(changes)
    return WorkflowDescriptor(**values)


def job(**changes):
    values = {
        "id": JOB_ID,
        "principal": "hermes",
        "workflow_id": "image.test-v1",
        "workflow_version": "v1",
        "state": JobState.ACCEPTED,
        "created_at": NOW,
        "updated_at": NOW,
        "events": (JobEvent(1, JobState.ACCEPTED, NOW),),
        "backend_prompt_id": "private-upstream-id",
        "input_digest": "b" * 64,
    }
    values.update(changes)
    return MediaJob(**values)


def test_workflow_contract_validates_bounds_and_schema():
    workflow = descriptor()
    assert workflow.validate_parameters({"prompt": "owl", "width": 512}) == {
        "prompt": "owl",
        "width": 512,
    }
    assert workflow.as_public_dict()["schema"]["additionalProperties"] is False
    with pytest.raises(MediaError, match="unknown fields"):
        workflow.validate_parameters({"prompt": "owl", "width": 512, "node": "7"})
    with pytest.raises(MediaError, match="exceeds"):
        workflow.validate_parameters({"prompt": "owl", "width": 2048})


def test_workflow_descriptor_is_immutable_and_has_one_target():
    workflow = descriptor()
    with pytest.raises(TypeError):
        workflow.parameters["raw_graph"] = ParameterSpec("string")
    assert "service_target" not in workflow.as_public_dict()


def test_quality_profiles_lock_server_owned_parameters_and_publish_exact_settings():
    workflow = descriptor(
        quality_profiles={
            "draft": QualityProfile("Fast draft", {"width": 256}),
            "high": QualityProfile("Largest output", {"width": 1024}),
        },
        default_quality_profile="draft",
    )
    resolved, selected = workflow.resolve_parameters(
        {"prompt": "owl"},
        quality_profile="high",
    )
    assert selected == "high"
    assert resolved == {"width": 1024, "prompt": "owl"}
    public = workflow.as_public_dict()
    assert public["defaultQualityProfile"] == "draft"
    assert [item["id"] for item in public["qualityProfiles"]] == ["draft", "high"]
    assert public["qualityProfileParameters"] == ["width"]
    assert public["profiledParameterSchema"] == {
        "type": "object",
        "properties": {"prompt": {"type": "string", "maxLength": 32}},
        "required": ["prompt"],
        "additionalProperties": False,
        "maxProperties": 1,
    }
    assert public["schema"]["required"] == ["prompt", "width"]
    with pytest.raises(MediaError) as override:
        workflow.resolve_parameters(
            {"prompt": "owl", "width": 512},
            quality_profile="high",
        )
    assert override.value.code == "quality_profile_parameter_override"


def test_job_transition_is_monotonic_and_terminal_is_final():
    running = job().transition(JobState.QUEUED).transition(JobState.RUNNING)
    completed = running.transition(JobState.COMPLETED, reason="ok")
    assert [event.sequence for event in completed.events] == [1, 2, 3, 4]
    with pytest.raises(MediaError, match="cannot transition"):
        completed.transition(JobState.RUNNING)


def test_job_latency_is_derived_from_durable_events():
    preparing = job(quality_profile="standard").transition(
        JobState.PREPARING,
        at=NOW + dt.timedelta(seconds=1),
    )
    submitting = preparing.transition(
        JobState.SUBMITTING,
        at=NOW + dt.timedelta(seconds=2),
    )
    queued = submitting.transition(
        JobState.QUEUED,
        at=NOW + dt.timedelta(seconds=3),
    )
    running = queued.transition(
        JobState.RUNNING,
        at=NOW + dt.timedelta(seconds=5),
    )
    completed = running.transition(
        JobState.COMPLETED,
        at=NOW + dt.timedelta(seconds=11),
    )
    public = completed.as_public_dict()
    assert public["qualityProfile"] == "standard"
    assert public["latency"] == {
        "recordedSeconds": 11.0,
        "acceptedToQueuedSeconds": 3.0,
        "submissionSeconds": 1.0,
        "queueSeconds": 2.0,
        "endToEndSeconds": 11.0,
        "generationSeconds": 6.0,
    }


def test_job_latency_uses_latest_completed_approval_cycle():
    first_waiting = job().transition(
        JobState.AWAITING_APPROVAL,
        at=NOW + dt.timedelta(seconds=2),
    )
    first_preparing = first_waiting.transition(
        JobState.PREPARING,
        at=NOW + dt.timedelta(seconds=12),
    )
    retry_waiting = first_preparing.transition(
        JobState.AWAITING_APPROVAL,
        at=NOW + dt.timedelta(seconds=20),
    )
    refreshed_waiting = retry_waiting.transition(
        JobState.AWAITING_APPROVAL,
        at=NOW + dt.timedelta(seconds=30),
    )
    final_preparing = refreshed_waiting.transition(
        JobState.PREPARING,
        at=NOW + dt.timedelta(seconds=80),
    )

    assert final_preparing.latency()["approvalWaitSeconds"] == 50.0


def test_public_records_hide_private_fields_and_generated_bytes():
    artifact = MediaArtifact(
        id=ARTIFACT_ID,
        job_id=JOB_ID,
        principal="hermes",
        workflow_id="image.test-v1",
        workflow_version="v1",
        media_type="image/png",
        byte_length=128,
        sha256="c" * 64,
        expires_at=NOW + dt.timedelta(hours=1),
        source_path="C:/private/output.png",
    )
    public = job(artifacts=(artifact,)).as_public_dict()
    rendered = repr(public)
    assert "private-upstream-id" not in rendered
    assert "C:/private" not in rendered
    assert "principal" not in public
    assert public["artifacts"][0]["resource"] == f"/artifacts/{ARTIFACT_ID}"


@pytest.mark.parametrize(
    "changes",
    [
        {"graph_digest": "not-a-digest"},
        {"output_mime_types": ("application/octet-stream",)},
        {"service_target": "http://private-host"},
        {"max_artifact_bytes": 0},
    ],
)
def test_invalid_contract_values_fail_deterministically(changes):
    with pytest.raises(MediaError):
        descriptor(**changes)
