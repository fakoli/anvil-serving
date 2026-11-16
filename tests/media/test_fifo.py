"""Independent callers share FIFO reservations, not process-local locks."""
import dataclasses
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from anvil_serving.media.artifacts import ArtifactStore
from anvil_serving.media.backends import BackendStatus
from anvil_serving.media.comfyui import WorkflowCompatibility
from anvil_serving.media.contracts import JobState, RenderedWorkflow, utc_now
from anvil_serving.media.errors import MediaError
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.operations import MediaOperations
from anvil_serving.media.worker import MediaJobReconciler, MediaReconciliationLoop
from anvil_serving.media.workflows import canonical_digest
from tests.media.test_cli import _workflow


class Registry:
    def __init__(self):
        self.workflows = {name: dataclasses.replace(_workflow(), id=name) for name in ('image.a', 'image.b', 'image.c')}

    def list(self):
        return [w.as_public_dict() for w in self.workflows.values()]

    def get(self, name, version):
        assert version == 'v1'
        return self.workflows[name]

    def render(self, name, version, parameters):
        workflow = self.get(name, version)
        values = workflow.validate_parameters(parameters)
        return RenderedWorkflow(workflow, {'1': {'class_type': 'Test', 'inputs': values}}, canonical_digest(values))


class Backend:
    base_url = 'http://127.0.0.1:8188'

    def __init__(self):
        self.submitted = []
        self.ready = True
        self.ambiguous = False

    def compatibility(self, workflow):
        return WorkflowCompatibility(workflow.id, workflow.version, self.ready, self.ready)

    def submit(self, rendered, *, job_id):
        self.submitted.append(job_id)
        if self.ambiguous:
            raise MediaError('backend_unavailable', 'response lost')
        return 'prompt-' + job_id

    def history(self, prompt_id):
        return BackendStatus(prompt_id, 'running')

    def find_prompt(self, job_id):
        return None


def operations(tmp_path, registry=None, preview=None):
    return MediaOperations(registry or Registry(), MediaJobStore(tmp_path / 'jobs.sqlite3'),
                           ArtifactStore(tmp_path / 'artifacts'), lifecycle_preview=preview)


def submit(ops, backend, key, workflow='image.a', owner='caller'):
    return ops.workflow_run(workflow, 'v1', {'prompt': key}, principal=owner, idempotency_key=key, backend=backend)


def finish(ops, job_id):
    job = ops.jobs.get(job_id)
    if job.state == JobState.QUEUED:
        ops.jobs.transition(job_id, JobState.RUNNING, principal=job.principal)
    ops.jobs.transition(job_id, JobState.COMPLETED, principal=job.principal)


def test_fifo_survives_disconnect_independent_writers_cancel_and_background_restart(tmp_path):
    backend = Backend()
    first = submit(operations(tmp_path), backend, 'first')
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending = list(pool.map(lambda i: submit(operations(tmp_path), backend, str(i),
            workflow=('image.a', 'image.b')[i % 2], owner=f'caller-{i}'), range(6)))
    assert backend.submitted == [first['job']['id']]
    ops = operations(tmp_path)  # New process-equivalent store/service instances.
    ordered = sorted(pending, key=lambda r: r['queue']['sequence'])
    canceled = ordered.pop(2)
    owner = ops.jobs.get(canceled['job']['id']).principal
    assert ops.job_cancel(canceled['job']['id'], principal=owner, backend=object())['canceled']
    first_pending = ordered[0]
    job = ops.jobs.get(first_pending['job']['id'])
    again = submit(ops, backend, job.principal.removeprefix('caller-'), job.workflow_id, job.principal)
    assert again['job']['id'] == job.id and not again['created']
    with pytest.raises(MediaError, match='not found'):
        ops.job_status(job.id, principal='foreign')
    reconciler = MediaJobReconciler(ops.jobs, backend.history, backend_endpoint=backend.base_url)
    # Waiting longer than the old recovery window must not expire an accepted job.
    assert reconciler.reconcile(dataclasses.replace(job, updated_at=utc_now() - dt.timedelta(minutes=5))).state == JobState.ACCEPTED
    loop = MediaReconciliationLoop(reconciler, dispatch=lambda: ops.dispatch_queue(backend=backend))
    for previous in [first, *ordered]:
        finish(ops, previous['job']['id'])
        loop.reconcile_once()
    assert backend.submitted == [r['job']['id'] for r in [first, *ordered]]
    assert not ops.jobs.nonterminal()
    assert all(ops.jobs.queue_request(r['job']['id']) is None for r in [first, *pending])


def test_additional_slots_preserve_fifo_and_workflow_execution_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv('ANVIL_MEDIA_EXECUTION_SLOTS', '2')
    backend, ops = Backend(), operations(tmp_path)
    a = submit(ops, backend, 'a', 'image.a')
    b = submit(ops, backend, 'b', 'image.b')
    c = submit(ops, backend, 'c', 'image.a')
    d = submit(ops, backend, 'd', 'image.c')
    assert len(backend.submitted) == 2
    finish(ops, b['job']['id'])
    ops.dispatch_queue(backend=backend)
    assert len(backend.submitted) == 2  # A full workflow at the head blocks C.
    finish(ops, a['job']['id'])
    ops.dispatch_queue(backend=backend)
    assert backend.submitted == [r['job']['id'] for r in (a, b, c, d)]


def test_approval_contract_and_unknown_submission_block_execution(tmp_path):
    backend = Backend()
    backend.ready = False
    def preview(job, owner, service):
        return {'transactionId': 'approval', 'service': service,
                'action': 'prepare', 'humanRequired': True, 'manifest': 'serves.test.toml'}
    ops = operations(tmp_path, preview=preview)
    first = submit(ops, backend, 'first')
    second = submit(ops, backend, 'second', 'image.b')
    assert first['job']['state'] == 'awaiting_approval' and second['queue']['waiting']
    backend.ready = True
    ops.dispatch_queue(backend=backend)
    assert not backend.submitted  # Readiness alone cannot approve a cold start.
    ops.jobs.transition(first['job']['id'], JobState.PREPARING, principal='caller')
    backend.ambiguous = True
    ops.dispatch_queue(backend=backend)
    assert len(backend.submitted) == 1
    ops.jobs.transition(first['job']['id'], JobState.FAILED, principal='caller', reason='backend_submission_outcome_unknown')
    ops.dispatch_queue(backend=backend)
    assert len(backend.submitted) == 1
    assert ops.job_status(second['job']['id'], principal='caller')['queue']['blockedByUncertainSubmission']


def test_wait_is_bounded_owned_and_does_not_cancel_and_queue_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv('ANVIL_MEDIA_QUEUE_DEPTH', '2')
    ops, backend = operations(tmp_path), Backend()
    first = submit(ops, backend, 'one')
    second = submit(ops, backend, 'two')
    with pytest.raises(MediaError, match='queue is full'):
        submit(ops, backend, 'three')
    started = time.monotonic()
    assert ops.job_status(second['job']['id'], principal='caller', wait_seconds=.05)['job']['state'] == 'accepted'
    assert .04 <= time.monotonic() - started < 1
    with pytest.raises(MediaError, match='not found'):
        ops.job_status(first['job']['id'], principal='foreign', wait_seconds=20)
    for value in (True, -1, 21, float('nan'), float('inf')):
        with pytest.raises(MediaError, match='wait_seconds'):
            ops.job_status(first['job']['id'], principal='caller', wait_seconds=value)
    thread = threading.Thread(target=lambda: (time.sleep(.05), finish(ops, first['job']['id'])))
    thread.start()
    assert ops.job_status(first['job']['id'], principal='caller', wait_seconds=1)['job']['state'] == 'completed'
    thread.join()
    ops.registry.workflows['image.a'] = dataclasses.replace(ops.registry.workflows['image.a'], max_concurrency=2)
    ops.dispatch_queue(backend=backend)
    assert ops.jobs.get(second['job']['id']).state == JobState.FAILED
    assert len(backend.submitted) == 1  # Changed contract never executes.
