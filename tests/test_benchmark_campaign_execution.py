"""Campaign binding and bounded release checks; no serving requests."""
import copy
import json
from pathlib import Path
import time

import pytest

from anvil_serving.benchmarking import campaign_execution as execution
from anvil_serving.benchmarking import worker
from anvil_serving.benchmarking.artifacts import atomic_write_json
from anvil_serving.benchmarking.campaigns import source_sha256
from anvil_serving.benchmarking.jobs import BenchmarkJobError, job_spec_sha256
from anvil_serving.control_plane.controller.store import BenchmarkJobStore
from tests.test_benchmark_worker import spec


def campaign(tmp_path, monkeypatch, count=1, endpoint=None, drain=None):
    root = str(tmp_path / 'runs')
    Path(root).mkdir()
    monkeypatch.setattr('anvil_serving.benchmarking.harnesses.prepare_harness_assets', lambda *a, **k: {'assets': {}})
    cells = []
    for index in range(count):
        base = spec()
        base['run_id'] = f'child-{index}'
        if endpoint:
            base['endpoint']['base_url'] = endpoint
        prepared = execution.prepare_child(base, run_root=root)
        cells.append({'cell_id': f'cell-{index}', 'batch_id': f'batch-{index}', 'arm_id': 'arm',
                      'spec': base, 'spec_sha256': job_spec_sha256(base), 'prepared': prepared})
    manifest = {'campaign_id': 'comparison', 'ownership_id': 'campaign', 'source_sha256': source_sha256(),
                'cells': cells, 'arms': [{'arm_id': 'arm', 'budget_s': 60}],
                'budgets': {'per_arm_s': 60, 'total_s': 120, 'drain_reserve_s': 10}, 'drain_policy': drain or {}}
    manifest['manifest_sha256'] = execution._manifest_digest(manifest)
    directory = execution._directory(manifest, root)
    directory.mkdir(parents=True)
    atomic_write_json(directory / 'manifest.json', manifest)
    bound = [execution.bind_campaign_child(c['spec'], manifest_sha256=manifest['manifest_sha256'],
        campaign_id=manifest['campaign_id'], cell_id=c['cell_id'], arm_id=c['arm_id'], batch_id=c['batch_id']) for c in cells]
    return root, manifest, bound


def ready(root, manifest, bound, index=0):
    value = {'campaign': bound[index]['campaign'], 'spec_sha256': job_spec_sha256(bound[index]),
             'status': 'ready', 'boot_id': execution._boot_id()}
    atomic_write_json(execution._path(bound[index], root, 'campaign-execution.json'), value)
    return value


def test_frozen_plan_and_closed_binding_reject_drift_before_traffic(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    execution.verify_child(bound[0], run_root=root)
    changed = copy.deepcopy(bound[0])
    changed['parameters']['case_limit'] = 2
    with pytest.raises(BenchmarkJobError, match='child differs'):
        execution.verify_child(changed, run_root=root)
    prepared = execution._path(bound[0], root, 'campaign-prepared/plan.json')
    payload = json.loads(prepared.read_text())
    payload['plan']['admission']['profile'] = 'changed'
    atomic_write_json(prepared, payload)
    with pytest.raises(BenchmarkJobError, match='bytes changed'):
        execution.verify_child(bound[0], run_root=root)
    changed = copy.deepcopy(bound[0])
    changed['campaign']['extra'] = True
    with pytest.raises(BenchmarkJobError):
        job_spec_sha256(changed)


def test_ready_barrier_union_budget_unknown_closure_and_clamp(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch, count=2)
    with pytest.raises(BenchmarkJobError, match='all frozen children'):
        execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=50)
    store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
    store.submit(bound[0])
    store.claim(bound[0]['run_id'])
    first = ready(root, manifest, bound)
    release = execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=50)
    ready(root, manifest, bound, 1)
    with pytest.raises(BenchmarkJobError, match='prior batch lacks'):
        execution.release_batch(manifest, 'batch-1', run_root=root, allowance_s=50)
    first.update(status='closed', started_monotonic=release['released_monotonic'],
        closed_monotonic=release['released_monotonic']+20, clients_stopped=True, drain={'status': 'verified'})
    atomic_write_json(execution._path(bound[0], root, 'campaign-execution.json'), first)
    store.transition(bound[0]['run_id'], 'completed', results=execution.campaign_result_reference(store, bound[0]))
    assert execution.reconcile_batch(manifest, 'batch-0', run_root=root, store=store)['active_seconds'] == 20
    second = execution.release_batch(manifest, 'batch-1', run_root=root, allowance_s=50, store=store)
    assert second['allowance_s'] == 40
    first['closed_monotonic'] = release['released_monotonic'] - 1
    atomic_write_json(execution._path(bound[0], root, 'campaign-execution.json'), first)
    assert execution.reconcile_batch(manifest, 'batch-0', run_root=root, store=store)['active_seconds'] is None


def test_engine_drain_requires_identity_two_samples_and_actual_zero(monkeypatch):
    from anvil_serving.router import model_capacity
    policy = {'metrics_url': 'http://127.0.0.1:30001/metrics', 'running_metric': 'running',
              'waiting_metric': 'waiting', 'identity_contract': 'generation-label/v1', 'metric_labels': {'model': 'candidate', 'generation': 'one'}, 'poll_s': .001, 'timeout_s': .02}
    bodies = iter([b'running{model="candidate",generation="one"} 1\nwaiting{model="candidate",generation="one"} 0',
                   b'running{model="candidate",generation="one"} 0\nwaiting{model="candidate",generation="one"} 0',
                   b'running{model="candidate",generation="one"} 0\nwaiting{model="candidate",generation="one"} 0'])
    monkeypatch.setattr(model_capacity, '_default_metrics_read', lambda *a, **k: (200, next(bodies)))
    assert execution.observe_drain(policy, deadline=time.monotonic()+1)['status'] == 'verified'
    monkeypatch.setattr(model_capacity, '_default_metrics_read', lambda *a, **k: (200, b'running{model="other"} 0\nwaiting{model="other"} 0'))
    assert execution.observe_drain(policy, deadline=time.monotonic()+1)['status'] == 'unknown'
    assert execution.observe_drain({}, deadline=time.monotonic()+1)['status'] == 'unknown'


def test_worker_rejects_drift_before_preflight(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    path = execution._path(bound[0], root, 'campaign-prepared/plan.json')
    path.write_text('{}')
    store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
    store.submit(bound[0])
    monkeypatch.setattr(worker, 'run_benchmark_preflight', lambda *a, **k: pytest.fail('traffic'))
    monkeypatch.setattr(worker, '_run_suite', lambda *a, **k: pytest.fail('traffic'))
    result = worker.execute_benchmark_job(store, bound[0]['run_id'])
    assert result['state'] == 'failed'
    assert result['failure']['code'] == 'campaign_prepared_mismatch'


def test_real_worker_waits_for_release_and_times_out_without_coordinator(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
    store.submit(bound[0])
    # Exercise real custody and entry gate; terminate the waiting worker with the
    # existing owner-verified cancellation path, before any model traffic.
    launched = worker.launch_benchmark_job(path=store.path, run_root=root, run_id=bound[0]['run_id'])
    deadline = time.monotonic()+10
    while time.monotonic() < deadline and not execution.batch_ready(manifest, 'batch-0', run_root=root):
        time.sleep(.05)
    assert execution.batch_ready(manifest, 'batch-0', run_root=root)
    result = worker.cancel_benchmark_job(store, bound[0]['run_id'])
    assert result['state'] == 'cancelled'
    assert result['worker_terminated'] is True
    assert not worker._process_group_running(launched['pid'])
    assert execution.reconcile_batch(manifest, 'batch-0', run_root=root)['active_seconds'] is None


@pytest.mark.parametrize('stall', [False, True])
def test_real_release_closure_and_watchdog_survive_no_coordinator(tmp_path, monkeypatch, stall):
    from http.server import ThreadingHTTPServer
    import threading
    from tests.test_benchmark_worker import EndpointHandler

    class FakeEngine(EndpointHandler):
        def do_GET(self):
            if self.path == '/metrics':
                raw = b'running{model="deepseek",generation="test"} 0\nwaiting{model="deepseek",generation="test"} 0\n'
                self.send_response(200)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif stall:
                time.sleep(2)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeEngine)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    launched = None
    try:
        address = f'http://127.0.0.1:{server.server_port}'
        root, manifest, bound = campaign(tmp_path, monkeypatch, endpoint=address+'/v1', drain={
            'metrics_url': address+'/metrics', 'running_metric': 'running', 'waiting_metric': 'waiting',
            'identity_contract': 'generation-label/v1', 'metric_labels': {'model': 'deepseek', 'generation': 'test'}, 'timeout_s': 1, 'poll_s': .01})
        store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
        store.submit(bound[0])
        launched = worker.launch_benchmark_job(path=store.path, run_root=root, run_id=bound[0]['run_id'])
        deadline = time.monotonic()+10
        while time.monotonic() < deadline and not execution.batch_ready(manifest, 'batch-0', run_root=root):
            time.sleep(.02)
        assert execution.batch_ready(manifest, 'batch-0', run_root=root)
        release = execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=10.5 if stall else 20)
        # No coordinator loop participates in stopping or closing the child.
        while time.monotonic() < deadline:
            result = store.status(bound[0]['run_id'])
            if result['state'] in {'completed', 'failed', 'cancelled'}:
                break
            time.sleep(.02)
        assert result['state'] == ('cancelled' if stall else 'completed'), store.logs(bound[0]['run_id'])
        observed = execution.reconcile_batch(manifest, 'batch-0', run_root=root, store=store)
        assert observed['status'] == 'closed'
        assert 0 < observed['active_seconds'] <= release['allowance_s']
        receipt = execution._read(execution._path(bound[0], root, 'campaign-execution.json'))
        assert receipt['drain']['status'] == 'verified'
        assert all(v['status'] == 'unavailable' for v in receipt['telemetry_coverage'].values())
    finally:
        if launched and worker._process_group_running(launched['pid']):
            worker.cancel_benchmark_job(store, bound[0]['run_id'])
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_observer_generation_url_expires_cached_zero_before_drain(monkeypatch):
    from types import SimpleNamespace
    from anvil_serving.router import model_capacity
    clock = [100.0]
    monkeypatch.setattr(execution, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0]+seconds)))
    generation = 'a' * 64
    policy = {'identity_contract': 'generation-bound-observer/v1', 'generation': generation,
        'metrics_url': 'http://127.0.0.1:30001/engine/'+generation+'/metrics',
        'running_metric': 'running', 'waiting_metric': 'waiting', 'metric_labels': {'model_name': 'candidate'},
        'timeout_s': 40, 'poll_s': .1}
    observed = []
    def read(*args, **kwargs):
        observed.append(clock[0])
        return 200, b'running{model_name="candidate"} 0\nwaiting{model_name="candidate"} 0'
    monkeypatch.setattr(model_capacity, '_default_metrics_read', read)
    execution.validate_drain_policy(policy, reserve_s=60)
    assert execution.observe_drain(policy, deadline=130)['status'] == 'unknown'
    assert not observed  # pre-stop cached zero cannot reclaim a short reservation
    result = execution.observe_drain(policy, deadline=160)
    assert result['status'] == 'verified' and min(observed) >= 135
    assert len(observed) == 2
    monkeypatch.setattr(model_capacity, '_default_metrics_read', lambda *a, **k: (503, b'superseded'))
    assert execution.observe_drain(policy, deadline=200)['status'] == 'unknown'
    with pytest.raises(BenchmarkJobError):
        execution.validate_drain_policy({**policy, 'generation': 'b'*64}, reserve_s=60)
