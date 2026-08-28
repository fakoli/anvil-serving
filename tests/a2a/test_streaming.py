from __future__ import annotations

from anvil_serving.a2a.http import sse_frames
from anvil_serving.a2a.tasks import stream_events
from anvil_serving.media.contracts import JobState

from .test_tasks import CALLER, send_request, service


def test_stream_replays_ordered_wrapper_events_and_matches_polling(tmp_path):
    tasks = service(tmp_path)
    initial = tasks.send_message(send_request()["params"], caller=CALLER)["task"]
    job_id = initial["id"]
    store = tasks.operations.jobs
    store.transition(job_id, JobState.RUNNING, principal="hermes")
    store.transition(job_id, JobState.COMPLETED, principal="hermes")
    job = store.get(job_id, principal="hermes")
    updates = stream_events(job)
    assert list(updates[0]) == ["task"]
    assert updates[0]["task"]["status"]["state"] == "TASK_STATE_SUBMITTED"
    states = [
        update["statusUpdate"]["status"]["state"]
        for update in updates
        if "statusUpdate" in update
    ]
    assert states == ["TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED"]
    assert updates[-1]["statusUpdate"]["metadata"]["sequence"] == job.events[-1].sequence
    assert tasks.get_task(job_id, caller=CALLER)["status"]["state"] == states[-1]
    frames = list(sse_frames("stream-one", updates))
    assert all(frame.startswith(b"data: {") and frame.endswith(b"\n\n") for frame in frames)


def test_dropped_stream_does_not_cancel_or_duplicate_work(tmp_path):
    tasks = service(tmp_path)
    task = tasks.send_message(send_request()["params"], caller=CALLER)["task"]
    before = tasks.operations.jobs.get(task["id"], principal="hermes")
    observed = list(
        tasks.observe(
            task["id"], caller=CALLER, disconnected=lambda: True,
            timeout_seconds=1, poll_interval=0.01,
        )
    )
    after = tasks.operations.jobs.get(task["id"], principal="hermes")
    assert observed == []
    assert after == before
    assert after.state == JobState.QUEUED


def test_resume_cursor_returns_only_later_events(tmp_path):
    tasks = service(tmp_path)
    task = tasks.send_message(send_request()["params"], caller=CALLER)["task"]
    store = tasks.operations.jobs
    store.transition(task["id"], JobState.RUNNING, principal="hermes")
    job = store.get(task["id"], principal="hermes")
    updates = stream_events(job, after_sequence=2)
    assert len(updates) == 1
    assert updates[0]["statusUpdate"]["metadata"]["sequence"] == 3
