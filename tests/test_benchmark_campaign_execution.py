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


def campaign(tmp_path, monkeypatch, count=1, endpoint=None, drain=None, activation=None, drain_reserve_s=10):
    root = str(tmp_path / 'runs')
    Path(root).mkdir()
    arm_budget = max(60, drain_reserve_s + 10)
    activation = activation or {'status': 'verified', 'generation': 'a' * 64,
                  'metrics_url': 'http://127.0.0.1:19100/engine/' + 'a' * 64 + '/metrics',
                  'container_id': 'b' * 64, 'started_at': 'test', 'served_model': 'model'}
    activation['binding_sha256'] = execution._digest({key: activation[key] for key in ('generation', 'metrics_url', 'container_id', 'started_at', 'served_model')})
    monkeypatch.setattr(execution, '_activation', lambda arm, expected=None: activation if expected in (None, activation) else pytest.fail('activation drift'))
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
                'cells': cells, 'arms': [{'arm_id': 'arm', 'budget_s': 60, 'activation': {}}],
                'budgets': {'per_arm_s': arm_budget, 'total_s': arm_budget * 2, 'drain_reserve_s': drain_reserve_s}, 'drain_policy': drain or {}}
    manifest['arms'][0]['budget_s'] = arm_budget
    manifest['manifest_sha256'] = execution._manifest_digest(manifest)
    directory = execution._directory(manifest, root)
    directory.mkdir(parents=True)
    atomic_write_json(directory / 'manifest.json', manifest)
    atomic_write_json(directory / 'state.json', {
        'schema': 'anvil-serving.benchmark-campaign-state/v1',
        'manifest_sha256': manifest['manifest_sha256'], 'cancel_requested': False,
        'batches': {},
    })
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


def test_engine_drain_requires_fresh_resolved_zero(monkeypatch):
    from anvil_serving.router import model_capacity
    from types import SimpleNamespace
    clock = [0.0]
    monkeypatch.setattr(execution, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    generation = 'a' * 64
    policy = {'metrics_url': 'http://127.0.0.1:30001/engine/'+generation+'/metrics', 'generation': generation,
              'running_metric': 'running', 'waiting_metric': 'waiting', 'metric_labels': {'model_name': 'candidate'}, 'poll_s': .1, 'timeout_s': 40}
    monkeypatch.setattr(model_capacity, '_default_metrics_read', lambda *a, **k: (200, ('running{model_name="candidate"} 0\nwaiting{model_name="candidate"} 0\nanvil_observer_metrics_sample_timestamp_seconds{model="candidate"} ' + str(clock[0])).encode()))
    assert execution.observe_drain(policy, deadline=50)['status'] == 'verified'


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


def test_watchdog_stops_owned_worker_when_job_state_is_unreadable_at_deadline(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
    store.submit(bound[0])
    ready(root, manifest, bound)
    release = execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=20, store=store)
    stopped = []

    class UnreadableAtDeadline:
        path = store.path
        run_root = root

        def __init__(self):
            self.calls = 0

        def status(self, run_id):
            self.calls += 1
            if self.calls == 1:
                return store.status(run_id)
            raise OSError('job database unavailable')

    import anvil_serving.control_plane.controller.store as store_module
    unreadable = UnreadableAtDeadline()
    monkeypatch.setattr(store_module, 'BenchmarkJobStore', lambda *args, **kwargs: unreadable)
    monkeypatch.setattr(worker, '_stop_owned_worker', lambda *args, **kwargs: stopped.append((args, kwargs)) or {'stopped': True})
    monkeypatch.setattr(execution.time, 'monotonic', lambda: release['traffic_deadline_monotonic'])
    worker._campaign_watchdog(store.path, root, bound[0]['run_id'])
    assert stopped
    receipt = execution._read(execution._path(bound[0], root, 'campaign-execution.json'))
    assert receipt['status'] == 'blocked'
    assert receipt['watchdog']['reason'] == 'job_state_unreadable_at_traffic_deadline'


def test_watchdog_stops_owned_worker_when_cancel_fails_after_missing_state(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
    store.submit(bound[0]); ready(root, manifest, bound)
    release = execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=20, store=store)
    class MissingAtDeadline:
        path, run_root = store.path, root
        calls = 0
        def status(self, run_id):
            self.calls += 1
            return store.status(run_id) if self.calls == 1 else None
    import anvil_serving.control_plane.controller.store as store_module
    missing, stopped = MissingAtDeadline(), []
    monkeypatch.setattr(store_module, 'BenchmarkJobStore', lambda *args, **kwargs: missing)
    monkeypatch.setattr(worker, 'cancel_benchmark_job', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('database lost')))
    monkeypatch.setattr(worker, '_stop_owned_worker', lambda *args, **kwargs: stopped.append(True) or {'stopped': True})
    monkeypatch.setattr(execution.time, 'monotonic', lambda: release['traffic_deadline_monotonic'])
    worker._campaign_watchdog(store.path, root, bound[0]['run_id'])
    assert stopped == [True]


def test_real_released_worker_closes_with_test_owned_activation(tmp_path, monkeypatch):
    """A child interpreter loads only this test-owned activation seam."""
    from http.server import ThreadingHTTPServer
    import threading
    from tests.test_benchmark_worker import EndpointHandler

    class Engine(EndpointHandler):
        def do_GET(self):
            if self.path.endswith('/metrics'):
                raw = ('running{model_name="model"} 0\nwaiting{model_name="model"} 0\nanvil_observer_metrics_sample_timestamp_seconds{model="model"} ' + str(time.time())).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Engine)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    launched = None
    try:
        address = f'http://127.0.0.1:{server.server_port}'
        activation = {'status': 'verified', 'generation': 'a' * 64,
            'metrics_url': address + '/engine/' + 'a' * 64 + '/metrics', 'container_id': 'b' * 64,
            'started_at': 'test', 'served_model': 'model'}
        activation['binding_sha256'] = execution._digest({key: activation[key] for key in ('generation', 'metrics_url', 'container_id', 'started_at', 'served_model')})
        hook = tmp_path / 'hook'; hook.mkdir()
        (hook / 'sitecustomize.py').write_text(
            'from anvil_serving.benchmarking import campaign_execution as e\n'
            'a = ' + repr(activation) + '\n'
            'e._activation = lambda arm, expected=None: a if expected in (None, a) else (_ for _ in ()).throw(RuntimeError("activation drift"))\n'
        )
        monkeypatch.setenv('PYTHONPATH', str(hook))
        root, manifest, bound = campaign(tmp_path, monkeypatch, endpoint=address+'/v1', activation=activation,
            drain={'running_metric': 'running', 'waiting_metric': 'waiting', 'metric_labels': {'model_name': 'model'}, 'timeout_s': 40, 'poll_s': .01}, drain_reserve_s=70)
        store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root); store.submit(bound[0])
        launched = worker.launch_benchmark_job(path=store.path, run_root=root, run_id=bound[0]['run_id'])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not execution.batch_ready(manifest, 'batch-0', run_root=root): time.sleep(.02)
        assert execution.batch_ready(manifest, 'batch-0', run_root=root)
        execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=80)
        while time.monotonic() < deadline:
            result = store.status(bound[0]['run_id'])
            if result['state'] in {'completed', 'failed', 'cancelled'}: break
            time.sleep(.05)
        assert result['state'] == 'completed', store.logs(bound[0]['run_id'])
        assert execution.reconcile_batch(manifest, 'batch-0', run_root=root, store=store)['status'] == 'closed'
    finally:
        if launched and worker._process_group_running(launched['pid']): worker.cancel_benchmark_job(store, bound[0]['run_id'])
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_real_released_watchdog_stops_owned_child_at_traffic_deadline(tmp_path, monkeypatch):
    """The released worker and its detached watchdog use the real custody path."""
    from http.server import ThreadingHTTPServer
    import threading
    from tests.test_benchmark_worker import EndpointHandler

    class SlowEndpoint(EndpointHandler):
        def do_POST(self):
            time.sleep(60)
            super().do_POST()

    class SlowServer(ThreadingHTTPServer):
        daemon_threads = True

    server = SlowServer(('127.0.0.1', 0), SlowEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    launched = None
    try:
        address = f'http://127.0.0.1:{server.server_port}'
        activation = {'status': 'verified', 'generation': 'a' * 64,
            'metrics_url': address + '/engine/' + 'a' * 64 + '/metrics', 'container_id': 'b' * 64,
            'started_at': 'test', 'served_model': 'model'}
        activation['binding_sha256'] = execution._digest({key: activation[key] for key in ('generation', 'metrics_url', 'container_id', 'started_at', 'served_model')})
        hook = tmp_path / 'hook'; hook.mkdir()
        (hook / 'sitecustomize.py').write_text(
            'import time\n'
            'from anvil_serving.benchmarking import campaign_execution as e\n'
            'a = ' + repr(activation) + '\n'
            'e._activation = lambda arm, expected=None: a if expected in (None, a) else (_ for _ in ()).throw(RuntimeError("activation drift"))\n'
        )
        monkeypatch.setenv('PYTHONPATH', str(hook))
        root, manifest, bound = campaign(tmp_path, monkeypatch, endpoint=address+'/v1', activation=activation)
        store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root); store.submit(bound[0])
        launched = worker.launch_benchmark_job(path=store.path, run_root=root, run_id=bound[0]['run_id'])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not execution.batch_ready(manifest, 'batch-0', run_root=root): time.sleep(.02)
        assert execution.batch_ready(manifest, 'batch-0', run_root=root)
        execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=12)
        while time.monotonic() < deadline:
            result = store.status(bound[0]['run_id'])
            if result['state'] in {'completed', 'failed', 'cancelled'}: break
            time.sleep(.05)
        assert result['state'] == 'cancelled', store.logs(bound[0]['run_id'])
        assert not worker._process_group_running(launched['pid'])
    finally:
        if launched and worker._process_group_running(launched['pid']): worker.cancel_benchmark_job(store, bound[0]['run_id'])
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_observer_generation_url_expires_cached_zero_before_drain(monkeypatch):
    from types import SimpleNamespace
    from anvil_serving.router import model_capacity
    clock = [100.0]
    monkeypatch.setattr(execution, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0]+seconds)))
    generation = 'a' * 64
    policy = {'generation': generation,
        'metrics_url': 'http://127.0.0.1:30001/engine/'+generation+'/metrics',
        'running_metric': 'running', 'waiting_metric': 'waiting', 'metric_labels': {'model_name': 'candidate'},
        'timeout_s': 40, 'poll_s': .1}
    observed = []
    def read(*args, **kwargs):
        observed.append(clock[0])
        return 200, ('running{model_name="candidate"} 0\nwaiting{model_name="candidate"} 0\nanvil_observer_metrics_sample_timestamp_seconds{model="candidate"} ' + str(clock[0])).encode()
    monkeypatch.setattr(model_capacity, '_default_metrics_read', read)
    execution.validate_drain_policy(policy, reserve_s=70, resolved=True)
    assert execution.observe_drain(policy, deadline=130)['status'] == 'unknown'
    assert not observed  # pre-stop cached zero cannot reclaim a short reservation
    result = execution.observe_drain(policy, deadline=160)
    assert result['status'] == 'verified' and min(observed) >= 135
    assert len(observed) == 1
    monkeypatch.setattr(model_capacity, '_default_metrics_read', lambda *a, **k: (503, b'superseded'))
    assert execution.observe_drain(policy, deadline=200)['status'] == 'unknown'
    with pytest.raises(BenchmarkJobError):
        execution.validate_drain_policy({**policy, 'generation': 'b'*64}, reserve_s=70, resolved=True)
