import dataclasses
import datetime as dt
import threading
from pathlib import Path

from anvil_serving.media import (
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


def unqualified_workflow():
    return dataclasses.replace(
        ready_workflow(),
        available=False,
        unavailable_reasons=("quality_failed",),
    )


def test_cold_start_requires_approval_and_preserves_exact_preview(tmp_path):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    preview = {"operation": "serves-up", "target": "media-worker", "confirm": False}
    result = MediaAdmissionService(store).evaluate(
        ready_workflow(), PARAMETERS, principal="hermes", backend_ready=False, lifecycle_preview=preview
    )
    assert result.state == "awaiting_approval"
    assert result.preview == preview
    assert store.nonterminal() == []


def test_parallel_admission_reserves_queue_capacity_atomically(tmp_path):
    state_path = tmp_path / "jobs.sqlite3"
    workflow = dataclasses.replace(ready_workflow(), max_queue_depth=1)
    barrier = threading.Barrier(3)
    outcomes = []

    def submit(number):
        service = MediaAdmissionService(MediaJobStore(state_path))
        barrier.wait()
        decision, job, created = service.admit(
            workflow,
            dict(PARAMETERS, seed=number),
            principal=f"caller-{number}",
            backend_ready=True,
            input_digest=str(number) * 64,
            idempotency_key=f"request-{number}",
        )
        outcomes.append((decision.reason, job is not None, created))

    threads = [threading.Thread(target=submit, args=(number,)) for number in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()

    assert sum(1 for _reason, has_job, created in outcomes if has_job and created) == 1
    assert sum(1 for reason, _has_job, _created in outcomes if reason == "workflow_queue_depth") == 2
    assert MediaJobStore(state_path).active_counts(workflow.id, principal="caller-0")["total"] == 1
