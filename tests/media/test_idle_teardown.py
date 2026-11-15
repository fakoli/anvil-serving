"""Idle release preserves job ownership and checks both durable and native work."""
import pytest
from types import SimpleNamespace

from anvil_serving.media import idle_teardown
from anvil_serving.media.contracts import JobState
from anvil_serving.media.jobs import MediaJobStore
from anvil_serving.media.lifecycle import MediaWorkerLifecycle
from anvil_serving.media.worker import MediaReconciliationLoop


def setup(tmp_path, *, terminal=True):
    store = MediaJobStore(tmp_path / "jobs.sqlite3")
    job = store.create(principal="operator", workflow_id="image.test", workflow_version="v1",
                       input_digest="a" * 64, idempotency_key="idle-release-check")[0]
    running = {}
    stops = []
    def status(args):
        service = args['names'][0]
        active = running.get(service, False)
        return {"data":{"serves":[{"name":service, "container":service + "-container",
                "running":active, "health_status":200 if active else None, "port":8188}]}}
    def manage(args):
        if not args["dry_run"]:
            running[args['names'][0]] = args["action"] == "up"
            if args["action"] == "down":
                stops.append(args)
        return {"applied":not args["dry_run"]}
    life = MediaWorkerLifecycle(store, status_operation=status, manage_operation=manage)
    preview = life.prepare(job.id, principal="operator", service="worker", manifest="serves.toml")
    life.prepare(job.id, principal="operator", service="worker", manifest="serves.toml",
                 transaction_id=preview.transaction_id, confirm=True, human_approved=True)
    if terminal:
        store.transition(job.id, JobState.CANCELED, principal="operator", reason="test_without_render")
    binding = dict(worker="worker-container", controller="lifecycle", service="worker",
                   manifest="serves.toml", principal="operator")
    return life, store, job, binding, stops


def test_idle_owned_worker_preview_then_managed_teardown(tmp_path):
    life, store, job, binding, stops = setup(tmp_path)
    def queue(_):
        return {"running":0, "pending":0}
    assert MediaReconciliationLoop(SimpleNamespace(store=store)).drain_readback() == (0, False)
    preview = idle_teardown.release(life, binding, queue=queue)
    assert preview == {"idle":True, "applied":False, "reason":"owned_worker_idle"}
    assert stops == []
    result = idle_teardown.release(life, binding, queue=queue, apply=True)
    assert result["idle"] and result["applied"]
    assert len(stops) == 1
    assert life.status(job.id, principal="operator")["transaction"]["applied"] is False
    assert store.get(job.id, principal="operator").state == JobState.CANCELED


@pytest.mark.parametrize("queue", [{"running":1,"pending":0}, {"running":0,"pending":1}])
def test_native_work_refused_even_when_recorded_jobs_are_terminal(tmp_path, queue):
    life, _, _, binding, stops = setup(tmp_path)
    assert idle_teardown.release(life, binding, queue=lambda _:queue, apply=True)["reason"] == "native_queue_busy"
    assert stops == []


def test_unlinked_warm_job_blocks_release(tmp_path):
    life, store, _, binding, stops = setup(tmp_path)
    store.create(principal="operator", workflow_id="image.test", workflow_version="v1",
                 input_digest="b" * 64, idempotency_key="warm-job-not-linked-to-cold-lease")
    assert idle_teardown.release(life, binding, queue=lambda _: {"running":0,"pending":0}, apply=True)["reason"] == "unfinished_media_jobs"
    assert stops == []


@pytest.mark.parametrize('outcome', ['canceled_prompt', 'lost_prompt'])
def test_terminal_facade_with_ambiguous_backend_never_releases(tmp_path, outcome):
    life, store, job, binding, stops = setup(tmp_path, terminal=False)
    if outcome == 'canceled_prompt':
        store.set_backend_prompt(job.id, 'backend-prompt', principal='operator')
        store.transition(job.id, JobState.CANCELED, principal='operator', reason='canceled_by_principal')
    else:
        store.transition(job.id, JobState.FAILED, principal='operator', reason='backend_prompt_lost')
    assert MediaReconciliationLoop(SimpleNamespace(store=store)).drain_readback() == (0, True)
    result = idle_teardown.release(life, binding, queue=lambda _: {'running':0,'pending':0}, apply=True)
    assert not result['idle'] and not result['applied']
    assert stops == []


def test_unowned_mismatch_or_unknown_queue_never_stops(tmp_path):
    life, _, _, binding, stops = setup(tmp_path)
    for replacement in ({"manifest":"other.toml"}, {"worker":"another-container"}, {"principal":"another-user"}):
        result = idle_teardown.release(life, {**binding, **replacement}, queue=lambda _: {"running":0,"pending":0}, apply=True)
        assert not result["idle"] and not result["applied"]
    def unavailable(_):
        raise OSError("queue unavailable")
    assert idle_teardown.release(life, binding, queue=unavailable, apply=True)["reason"] == "native_queue_unavailable"
    assert stops == []


def test_render_arriving_after_preview_is_refused_on_apply(tmp_path):
    life, _, _, binding, stops = setup(tmp_path)
    assert idle_teardown.release(life, binding, queue=lambda _: {"running":0,"pending":0})["idle"]
    assert not idle_teardown.release(life, binding, queue=lambda _: {"running":1,"pending":0}, apply=True)["idle"]
    assert stops == []


def test_missing_health_does_not_consume_the_owned_lease(tmp_path):
    life, _, job, binding, stops = setup(tmp_path)
    original = life._status_operation
    def unknown(args):
        result = original(args)
        result['data']['serves'][0]['health_status'] = None
        return result
    life._status_operation = unknown
    result = idle_teardown.release(life, binding, queue=lambda _: {'running':0,'pending':0}, apply=True)
    assert not result['idle'] and not result['applied']
    assert life._transaction_for_job(job.id)['status'] == 'active'
    assert stops == []


def test_job_with_newer_other_service_lease_cannot_stop_that_worker(tmp_path):
    life, store, job, binding, stops = setup(tmp_path, terminal=False)
    target = life._transaction_for_job(job.id)
    other_job = store.create(principal="operator", workflow_id="image.test", workflow_version="v1",
                             input_digest="b" * 64, idempotency_key="other-worker")[0]
    other = life.prepare(other_job.id, principal="operator", service="other", manifest="serves.toml")
    life.prepare(other_job.id, principal="operator", service="other", manifest="serves.toml",
                 transaction_id=other.transaction_id, confirm=True, human_approved=True)
    life._link(other.transaction_id, job.id)
    store.transition(job.id, JobState.CANCELED, principal="operator", reason="test_without_render")
    store.transition(other_job.id, JobState.CANCELED, principal="operator", reason="test_without_render")
    assert life._transaction_for_job(job.id)['id'] == other.transaction_id
    result = idle_teardown.release(life, binding, queue=lambda _: {'running':0,'pending':0}, apply=True)
    assert not result['applied']
    assert stops == []
    with life._connect() as db:
        assert {r['id']:r['status'] for r in db.execute('SELECT id,status FROM media_lifecycle_transactions')} == {
            target['id']:'active', other.transaction_id:'active'}


def test_controller_preview_never_constructs_mutating_lifecycle(tmp_path, monkeypatch, capsys):
    import json
    from anvil_serving.control_plane.mcp.tools import media_worker
    life, _, _, binding, stops = setup(tmp_path)
    monkeypatch.setattr(media_worker, '_lifecycle', lambda: pytest.fail('preview must not initialize/migrate the lifecycle DB'))
    monkeypatch.setattr(media_worker, '_resource_status', life._status_operation)
    monkeypatch.setattr(idle_teardown, '_native_queue', lambda _: {'running':0,'pending':0})
    monkeypatch.setenv('ANVIL_MEDIA_STATE_DB', str(life.store.path))
    monkeypatch.setattr(idle_teardown.sys, 'argv', ['idle', json.dumps(binding), 'preview'])
    idle_teardown.main()
    assert json.loads(capsys.readouterr().out) == {'idle':True,'applied':False,'reason':'owned_worker_idle'}
    assert stops == []
