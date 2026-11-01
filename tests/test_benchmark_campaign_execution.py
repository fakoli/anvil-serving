"""Campaign binding and bounded release checks; no serving requests."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from anvil_serving.benchmarking import campaign_execution as execution
from anvil_serving.benchmarking import worker
from anvil_serving.benchmarking.artifacts import atomic_write_json
from anvil_serving.benchmarking.campaigns import source_sha256
from anvil_serving.benchmarking.jobs import BenchmarkJobError, job_spec_sha256
from anvil_serving.control_plane.controller.store import BenchmarkJobStore
from tests.test_benchmark_worker import spec


def campaign(tmp_path, monkeypatch, count=1, endpoint=None, drain=None, activation=None, drain_reserve_s=10, timeout_s=10):
    root = str(tmp_path / 'runs')
    Path(root).mkdir()
    arm_budget = 60 + timeout_s + drain_reserve_s
    activation = activation or {'status': 'verified', 'generation': 'a' * 64,
                  'metrics_url': 'http://127.0.0.1:19100/engine/' + 'a' * 64 + '/metrics',
                  'container_id': 'b' * 64, 'started_at': 'test', 'served_model': 'model'}
    activation['binding_sha256'] = execution._digest({key: activation[key] for key in ('generation', 'metrics_url', 'container_id', 'started_at', 'served_model')})
    monkeypatch.setattr(execution, '_activation', lambda arm, expected=None: activation if expected in (None, activation) else pytest.fail('activation drift'))
    monkeypatch.setattr('anvil_serving.benchmarking.harnesses.prepare_harness_assets', lambda *a, **k: {'assets': {}})
    cells = []
    for index in range(count):
        base = spec()
        base['timeout_s'] = timeout_s
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


def test_ready_barrier_union_budget_unknown_closure_and_no_clamp(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch, count=2)
    release_clock = 492.40178837899526  # Reproduces the hosted Python 3.11 binary64 subtraction.
    monkeypatch.setattr(execution.time, 'monotonic', lambda: release_clock)
    with pytest.raises(BenchmarkJobError, match='all frozen children'):
        execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=80)
    store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
    store.submit(bound[0])
    store.claim(bound[0]['run_id'])
    first = ready(root, manifest, bound)
    release = execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=80)
    ready(root, manifest, bound, 1)
    with pytest.raises(BenchmarkJobError, match='prior batch lacks'):
        execution.release_batch(manifest, 'batch-1', run_root=root, allowance_s=80)
    first.update(status='closed', started_monotonic=release['released_monotonic'],
        closed_monotonic=release['released_monotonic']+20, clients_stopped=True, clients_stopped_wall=time.time(),
        activation=release['activation'], drain={'status': 'verified'})
    atomic_write_json(execution._path(bound[0], root, 'campaign-execution.json'), first)
    shared = {'schema': 'anvil-serving.campaign-batch-closure/v1', 'manifest_sha256': manifest['manifest_sha256'],
        'release_sha256': execution._digest(release), 'batch_id': 'batch-0', 'status': 'closed',
        'stops': [execution._stop_fact(first)], 'drain': {'status': 'verified'}, 'closed_at': 'fixture',
        'closed_monotonic': first['closed_monotonic'], 'finalized_monotonic': first['closed_monotonic']}
    target = execution._directory(manifest, root) / 'closures/batch-0.json'
    target.parent.mkdir(parents=True)
    atomic_write_json(target, shared)
    store.transition(bound[0]['run_id'], 'completed', results=execution.campaign_result_reference(store, bound[0]))
    active_seconds = execution.reconcile_batch(manifest, 'batch-0', run_root=root, store=store)['active_seconds']
    assert active_seconds == pytest.approx(20, rel=0, abs=1e-12)
    with pytest.raises(BenchmarkJobError, match='requires 80s.*remaining 60s'):
        execution.release_batch(manifest, 'batch-1', run_root=root, allowance_s=80, store=store)
    assert not (execution._directory(manifest, root) / 'batches/batch-1.json').exists()
    first['closed_monotonic'] = release['released_monotonic'] - 1
    atomic_write_json(execution._path(bound[0], root, 'campaign-execution.json'), first)
    assert execution.reconcile_batch(manifest, 'batch-0', run_root=root, store=store)['active_seconds'] is None


def test_batch_allowance_counts_parallel_task_timeout_once():
    manifest = {'budgets': {'drain_reserve_s': 120}, 'cells': [
        {'batch_id': 'batch', 'arm_id': 'arm', 'spec': {'timeout_s': timeout}} for timeout in (10, 20, 30, 40)]}
    assert execution.required_batch_allowance(manifest, 'batch') == 60 + 40 + 120


def test_engine_drain_requires_fresh_resolved_zero(monkeypatch):
    from types import SimpleNamespace

    from anvil_serving.router import model_capacity

    clock = [0.0]
    monkeypatch.setattr(
        execution,
        'time',
        SimpleNamespace(
            monotonic=lambda: clock[0],
            time=lambda: clock[0],
            sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )
    generation = 'a' * 64
    policy = {
        'metrics_url': f'http://127.0.0.1:30001/engine/{generation}/metrics',
        'generation': generation,
        'running_metric': 'running',
        'waiting_metric': 'waiting',
        'metric_labels': {'model_name': 'candidate'},
        'poll_s': .1,
        'timeout_s': 40,
    }
    monkeypatch.setattr(
        model_capacity,
        '_default_metrics_read',
        lambda *args, **kwargs: (
            200,
            (
                'running{model_name="candidate"} 0\n'
                'waiting{model_name="candidate"} 0\n'
                'anvil_observer_metrics_sample_timestamp_seconds{model="candidate"} '
                f'{clock[0]}'
            ).encode(),
        ),
    )
    assert execution.observe_drain(policy, deadline=50)['status'] == 'verified'


def test_watchdog_stops_owned_worker_when_job_state_is_unreadable_at_deadline(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
    store.submit(bound[0])
    ready(root, manifest, bound)
    release = execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=80, store=store)
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
    assert receipt['status'] == 'ready'  # Unreadable durable state cannot authorize a post-terminal write.


def test_watchdog_stops_owned_worker_when_cancel_fails_after_missing_state(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root)
    store.submit(bound[0]); ready(root, manifest, bound)
    release = execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=80, store=store)
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


def install_test_owned_activation(
    tmp_path, monkeypatch, activation, *, watchdog_startup_delay_s=0, bypass_suite_window=False
):
    """Pin source before startup hooks can import a different editable checkout."""
    hook = tmp_path / 'hook'
    hook.mkdir()
    source_root = Path(__file__).resolve().parents[1]
    (hook / 'sitecustomize.py').write_text(
        'import sys, time\n'
        f'if "--campaign-watchdog" in sys.argv: time.sleep({watchdog_startup_delay_s!r})\n'
        f'sys.path.insert(0, {str(source_root)!r})\n'
        'from anvil_serving.benchmarking import campaign_execution as e\n'
        'a = ' + repr(activation) + '\n'
        'e._activation = lambda arm, expected=None: a if expected in (None, a) else (_ for _ in ()).throw(RuntimeError("activation drift"))\n'
        + ('e.require_campaign_suite_window = lambda context: None\n' if bypass_suite_window else '')
    )
    monkeypatch.setenv('PYTHONPATH', str(hook))


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
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True); thread.start()
    launched = None
    try:
        address = f'http://127.0.0.1:{server.server_port}'
        activation = {'status': 'verified', 'generation': 'a' * 64,
            'metrics_url': address + '/engine/' + 'a' * 64 + '/metrics', 'container_id': 'b' * 64,
            'started_at': 'test', 'served_model': 'model'}
        activation['binding_sha256'] = execution._digest({key: activation[key] for key in ('generation', 'metrics_url', 'container_id', 'started_at', 'served_model')})
        install_test_owned_activation(tmp_path, monkeypatch, activation)
        root, manifest, bound = campaign(tmp_path, monkeypatch, endpoint=address+'/v1', activation=activation,
            drain={'running_metric': 'running', 'waiting_metric': 'waiting', 'metric_labels': {'model_name': 'model'}, 'timeout_s': 40, 'poll_s': .01}, drain_reserve_s=70)
        observed_drains = []

        def verify_configured_drain(policy, *, deadline, **kwargs):
            observed_drains.append((policy, deadline))
            assert policy['metrics_url'] == activation['metrics_url']
            assert policy['generation'] == activation['generation']
            assert policy['metric_labels'] == {'model_name': 'model'}
            return {'status': 'verified', 'fixture': 'coordinator-seam'}

        monkeypatch.setattr(execution, 'observe_drain', verify_configured_drain)
        store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root); store.submit(bound[0])
        launched = worker.launch_benchmark_job(path=store.path, run_root=root, run_id=bound[0]['run_id'])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not execution.batch_ready(manifest, 'batch-0', run_root=root): time.sleep(.02)
        assert execution.batch_ready(manifest, 'batch-0', run_root=root), store.logs(bound[0]['run_id'])
        execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=140)
        while time.monotonic() < deadline:
            execution.coordinate_batch(manifest, 'batch-0', store=store)
            result = store.status(bound[0]['run_id'])
            if result['state'] in {'completed', 'failed', 'cancelled'}: break
            time.sleep(.05)
        assert result['state'] == 'completed', store.logs(bound[0]['run_id'])
        assert execution.reconcile_batch(manifest, 'batch-0', run_root=root, store=store)['status'] == 'closed'
        assert len(observed_drains) == 1
    finally:
        if launched and worker._process_group_running(launched['pid']): worker.cancel_benchmark_job(store, bound[0]['run_id'])
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_real_released_watchdog_stops_owned_child_at_traffic_deadline(tmp_path, monkeypatch):
    """The released worker and its detached watchdog use the real custody path."""
    from http.server import ThreadingHTTPServer
    import threading
    from tests.test_benchmark_worker import EndpointHandler

    request_entered = threading.Event()

    class SlowEndpoint(EndpointHandler):
        def do_POST(self):
            request_entered.set()
            time.sleep(60)
            super().do_POST()

    class SlowServer(ThreadingHTTPServer):
        daemon_threads = True

    server = SlowServer(('127.0.0.1', 0), SlowEndpoint)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True); thread.start()
    launched = None
    try:
        address = f'http://127.0.0.1:{server.server_port}'
        activation = {'status': 'verified', 'generation': 'a' * 64,
            'metrics_url': address + '/engine/' + 'a' * 64 + '/metrics', 'container_id': 'b' * 64,
            'started_at': 'test', 'served_model': 'model'}
        activation['binding_sha256'] = execution._digest({key: activation[key] for key in ('generation', 'metrics_url', 'container_id', 'started_at', 'served_model')})
        # Reproduce a loaded runner where watchdog startup exceeds the former
        # two-second traffic window, without weakening real process custody.
        install_test_owned_activation(
            tmp_path, monkeypatch, activation, watchdog_startup_delay_s=3, bypass_suite_window=True
        )
        root, manifest, bound = campaign(tmp_path, monkeypatch, endpoint=address+'/v1', activation=activation, timeout_s=30)
        store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root); store.submit(bound[0])
        launched = worker.launch_benchmark_job(path=store.path, run_root=root, run_id=bound[0]['run_id'])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not execution.batch_ready(manifest, 'batch-0', run_root=root): time.sleep(.02)
        assert execution.batch_ready(manifest, 'batch-0', run_root=root), store.logs(bound[0]['run_id'])
        # Model a historical short release: reading it must retain watchdog
        # enforcement even though new admission now refuses this allowance.
        with monkeypatch.context() as historical:
            historical.setattr(execution, 'require_batch_allowance', lambda *a, **k: k['allowance_s'])
            # Eight seconds of historical traffic remains after the required
            # ten-second drain reserve. It retains the deliberate three-second
            # watchdog startup and enough time to prove the owned child entered
            # the blocking endpoint before custody cancels it.
            execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=18)
        assert request_entered.wait(6), store.logs(bound[0]['run_id'])
        while time.monotonic() < deadline:
            result = store.status(bound[0]['run_id'])
            if result['state'] in {'completed', 'failed', 'cancelled'}: break
            time.sleep(.05)
        assert result['state'] == 'cancelled', json.dumps(store.logs(bound[0]['run_id']), sort_keys=True)
        assert not worker._process_group_running(launched['pid'])
    finally:
        if launched and worker._process_group_running(launched['pid']): worker.cancel_benchmark_job(store, bound[0]['run_id'])
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def entry_campaign(tmp_path, monkeypatch):
    """Frozen real child/store and sampler; collector failures involve no network."""
    from tests.test_campaign_telemetry import contract
    from anvil_serving.benchmarking import campaign_telemetry as telemetry
    root, manifest, bound = campaign(tmp_path, monkeypatch, drain_reserve_s=120,
        drain={'running_metric': 'running', 'waiting_metric': 'waiting',
               'metric_labels': {'model_name': 'model'}, 'timeout_s': 40, 'poll_s': .1})
    manifest['telemetry_collection'] = contract()
    manifest['arms'][0]['identity'] = {'hardware': ['GPU-a']}
    manifest['manifest_sha256'] = execution._manifest_digest(manifest)
    cell = manifest['cells'][0]
    child = execution.bind_campaign_child(cell['spec'], manifest_sha256=manifest['manifest_sha256'],
        campaign_id=manifest['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
    directory = execution._directory(manifest, root)
    atomic_write_json(directory / 'manifest.json', manifest)
    atomic_write_json(directory / 'state.json', execution._new_state(manifest))
    store = BenchmarkJobStore(str(tmp_path / 'jobs.db'), run_root=root)
    store.submit(child)
    ready(root, manifest, [child])
    def unavailable(*args, **kwargs):
        raise OSError('offline collector fixture')
    monkeypatch.setattr('anvil_serving.router.model_capacity._default_metrics_read', unavailable)
    monkeypatch.setattr(telemetry, '_container', unavailable)
    monkeypatch.setattr(execution, '_clients_stopped', lambda: True)
    monkeypatch.setattr(execution, 'observe_drain', lambda *args, **kwargs:
                        {'status': 'unknown', 'reason': 'offline_drain_fixture'})
    monkeypatch.setattr(worker, 'run_benchmark_preflight', lambda *args, **kwargs: pytest.fail('model traffic'))
    monkeypatch.setattr(worker, '_run_suite', lambda *args, **kwargs: pytest.fail('model traffic'))
    sampler = telemetry.BatchSampler(manifest, cell['batch_id'], run_root=root)
    release = execution.release_batch(manifest, cell['batch_id'], run_root=root,
                                      allowance_s=190, store=store, sampler=sampler)
    return store, manifest, child, sampler, release


@pytest.mark.parametrize('failure', ['no_ack', 'early_exit', 'spawn', 'activation', 'closure', 'prepared', 'cancel', 'budget'])
def test_entry_failure_closes_before_shared_terminal_and_durable_failure(tmp_path, monkeypatch, failure):
    from concurrent.futures import ThreadPoolExecutor
    from anvil_serving.benchmarking import campaign_telemetry as telemetry
    store, manifest, child, sampler, release = entry_campaign(tmp_path, monkeypatch)
    original_popen = subprocess.Popen
    supervisors = []
    original_verify = execution.verify_child
    calls = []
    expected = 'campaign_watchdog_unavailable'
    def spawn(*args, **kwargs):
        assert execution._read(execution._path(child, store.run_root, 'campaign-execution.json'))['status'] == 'active'
        if failure == 'spawn':
            raise OSError('watchdog spawn fixture')
        process = original_popen([sys.executable, '-c', 'pass' if failure == 'early_exit' else 'import time; time.sleep(60)'], **kwargs)
        supervisors.append(process)
        return process
    monkeypatch.setattr(execution.subprocess, 'Popen', spawn)
    if failure != 'early_exit':
        def no_ack(read, write, error, timeout):
            assert 0 < timeout <= min(30, release['traffic_deadline_monotonic'] - release['released_monotonic'])
            return [], [], []
        monkeypatch.setattr(execution.select, 'select', no_ack)
    if failure == 'spawn':
        expected = 'OSError'
    if failure == 'activation':
        expected = 'campaign_activation_mismatch'
        def drift(*args, **kwargs):
            raise BenchmarkJobError(expected, 'activation fixture drift')
        monkeypatch.setattr(execution, '_activation', drift)
    if failure == 'closure':
        def broken_close(*args, **kwargs):
            raise OSError('closure fixture unavailable')
        monkeypatch.setattr(execution, 'close_campaign', broken_close)
    if failure == 'prepared':
        expected = 'campaign_prepared_mismatch'
        def changed(*args, **kwargs):
            calls.append(True)
            if len(calls) > 1:
                raise BenchmarkJobError(expected, 'post-barrier fixture drift')
            return original_verify(*args, **kwargs)
        monkeypatch.setattr(execution, 'verify_child', changed)
    if failure == 'cancel':
        expected = 'campaign_cancelled'
        state = execution._new_state(manifest); state['cancel_requested'] = True
        atomic_write_json(execution._directory(manifest, store.run_root) / 'state.json', state)
    if failure == 'budget':
        expected = 'campaign_budget_exceeded'
        monkeypatch.setattr(execution, '_remaining_budget', lambda *args, **kwargs: 0)
    path = execution._path(child, store.run_root, 'campaign-execution.json')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.execute_benchmark_job, store, child['run_id'])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            receipt = execution._read(path)
            if receipt['status'] in {'blocked', 'draining'}:
                break
            time.sleep(.01)
        assert receipt['status'] in {'blocked', 'draining'}
        assert receipt['clients_stopped'] is True
        assert receipt['drain']['status'] in {'unknown', 'pending'}
        assert receipt['entry_failure']['code'] == expected
        assert not future.done()  # The immutable artifact still waits for the coordinator.
        assert store.status(child['run_id'])['state'] == 'running'
        assert not execution._anchored_closure(store, manifest['cells'][0], manifest, store.run_root)
        assert all(p.poll() is not None and p.stdout.closed for p in supervisors)
        # Actual coordinator closes the shared barrier before terminal artifacts.
        execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler, force=True)
        result = future.result(timeout=3)
    assert result['state'] == 'failed' and result['finished_at']
    assert result['failure']['code'] == expected
    artifact = store.artifact(child['run_id'])
    receipt = execution._read(path)
    terminal, _, reference = telemetry.verify_terminal(manifest, sampler.batch_id, release,
        run_root=store.run_root, reference=receipt['telemetry_terminal'])
    assert terminal['complete'] is False
    assert reference == receipt['telemetry_terminal']
    assert artifact['results']['campaign_execution']['sha256']
    assert execution._anchored_closure(store, manifest['cells'][0], manifest, store.run_root)
    assert execution.reconcile_batch(manifest, sampler.batch_id, run_root=store.run_root, store=store)['status'] == 'blocked'
    original = path.read_bytes()
    execution.campaign_result_reference(store, child)
    assert path.read_bytes() == original  # Terminal references are immutable.
    receipt.update(status='closed', drain={'status': 'verified'}, started_monotonic=release['released_monotonic'],
                   closed_monotonic=time.monotonic())
    atomic_write_json(path, receipt)
    assert not execution._anchored_closure(store, manifest['cells'][0], manifest, store.run_root)
    assert execution.reconcile_batch(manifest, sampler.batch_id, run_root=store.run_root, store=store)['active_seconds'] is None


@pytest.mark.parametrize('kind', ['receipt', 'release'])
def test_entry_failure_fallback_keeps_binding_and_release_guards(tmp_path, monkeypatch, kind):
    store, manifest, child, sampler, release = entry_campaign(tmp_path, monkeypatch)
    store.claim(child['run_id'])
    path = execution._path(child, store.run_root, 'campaign-execution.json')
    receipt = execution._read(path)
    receipt.update(status='blocked', clients_stopped=True, entry_failure={'code': 'fixture'})
    if kind == 'receipt':
        receipt['spec_sha256'] = '0' * 64
    else:
        receipt.pop('entry_failure')
        release['allowance_s'] = 3601
    atomic_write_json(path, receipt)
    atomic_write_json(sampler.root / 'batches' / (sampler.batch_id + '.json'), release)
    with pytest.raises(BenchmarkJobError):
        execution.campaign_result_reference(store, child)
    assert store.status(child['run_id'])['state'] == 'running'
    assert 'telemetry_terminal' not in execution._read(path)
