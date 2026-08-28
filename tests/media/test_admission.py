import dataclasses
import datetime as dt
from pathlib import Path

from anvil_serving.media import (
    JobState,
    MediaAdmissionService,
    MediaJobStore,
    WorkflowRegistry,
)


ROOT = Path(__file__).parents[2] / "configs" / "media" / "workflows"
PARAMETERS = {"prompt": "owl", "seed": 1, "width": 512, "height": 512, "steps": 10}
NOW = dt.datetime(2026, 8, 27, tzinfo=dt.timezone.utc)


def ready_workflow():
    configured = WorkflowRegistry(ROOT / "registry.json").get("image.flux2-klein-4b-fp8-v1", "v1")
    return dataclasses.replace(configured, available=True, unavailable_reasons=())


def test_unqualified_workflow_fails_before_lifecycle(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    workflow = WorkflowRegistry(ROOT / "registry.json").get("image.flux2-klein-4b-fp8-v1", "v1")
    result = MediaAdmissionService(store).evaluate(
        workflow, PARAMETERS, principal="hermes", backend_ready=False, lifecycle_preview={"wouldRun": ["managed"]}
    )
    assert result.state == "unavailable"
    assert result.preview is None


def test_cold_start_requires_approval_and_preserves_exact_preview(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    preview = {"operation": "serves-up", "target": "media-worker", "confirm": False}
    result = MediaAdmissionService(store).evaluate(
        ready_workflow(), PARAMETERS, principal="hermes", backend_ready=False, lifecycle_preview=preview
    )
    assert result.state == "awaiting_approval"
    assert result.preview == preview
    assert store.nonterminal() == []


def test_queue_and_concurrency_limits_reject_before_more_work(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    workflow = ready_workflow()
    for number in range(2):
        job, _ = store.create(
            principal="hermes",
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            input_digest=(str(number) * 64),
            idempotency_key=f"key-{number}",
            now=NOW,
        )
        if number == 0:
            store.transition(job.id, JobState.QUEUED, principal="hermes")
            store.transition(job.id, JobState.RUNNING, principal="hermes")
    decision = MediaAdmissionService(store).evaluate(
        workflow, PARAMETERS, principal="hermes", backend_ready=True
    )
    assert decision.allowed is False
    assert decision.reason == "principal_queue_depth"


def test_parameter_bounds_are_applied_before_queue_state(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    parameters = dict(PARAMETERS, width=2048)
    try:
        MediaAdmissionService(store).evaluate(
            ready_workflow(), parameters, principal="hermes", backend_ready=True
        )
    except Exception as error:
        assert getattr(error, "code", None) == "invalid_parameter"
    else:
        raise AssertionError("out-of-range dimensions were admitted")
