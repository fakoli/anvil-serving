"""Campaign binding and bounded release checks; no serving requests."""
import copy
import json
import os
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
    value = {'schema': 'anvil-serving.campaign-execution/v1', 'campaign': bound[index]['campaign'],
             'spec_sha256': job_spec_sha256(bound[index]), 'status': 'ready', 'boot_id': execution._boot_id(),
             'ready_at': 'fixture', 'clients_stopped': False,
             'telemetry_coverage': {key: {'status': 'unavailable'} for key in ('engine', 'gpu', 'host', 'cgroup')}}
    atomic_write_json(execution._path(bound[index], root, 'campaign-execution.json'), value)
    return value


def test_cancel_before_release_preserves_ready_receipt_as_unavailable(tmp_path, monkeypatch):
    root, manifest, bound = campaign(tmp_path, monkeypatch, count=2)
    store = BenchmarkJobStore(str(tmp_path / 'jobs.sqlite'), run_root=root)
    store.submit(bound[0])
    store.claim(bound[0]['run_id'])
    ready(root, manifest, bound)
    stopped = []
    monkeypatch.setattr(worker, '_stop_owned_worker', lambda *args, **kwargs:
                        stopped.append(args[1]) or {'stopped': True})
    monkeypatch.setattr(worker, '_retain_native_partial_outputs', lambda **kwargs: {'state': 'retained'})

    result = worker.cancel_campaign_jobs(manifest, store)

    assert result['phase'] == 'completed'
    assert store.status(bound[0]['run_id'])['state'] == 'cancelled'
    assert store.status(bound[1]['run_id']) is None
    receipt = execution._read(execution._path(bound[0], root, 'campaign-execution.json'))
    assert receipt['status'] == 'ready'
    assert receipt['telemetry_terminal'] == {'status': 'unavailable', 'reason': 'campaign_cancelled_before_release'}
    assert all(value == {'status': 'unavailable'} for value in receipt['telemetry_coverage'].values())
    assert not (execution._directory(manifest, root) / 'batches' / 'batch-0.json').exists()
    assert worker.cancel_campaign_jobs(manifest, store)['phase'] == 'completed'
    assert stopped == [bound[0]['run_id']]


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


@pytest.mark.parametrize('allowance', [79, 81])
def test_release_refuses_short_or_unfunded_allowance_before_activation(tmp_path, monkeypatch, allowance):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    ready(root, manifest, bound)
    monkeypatch.setattr(execution, '_activation', lambda *a, **k: pytest.fail('refused release activated'))
    with pytest.raises(BenchmarkJobError, match='full batch requires 80s.*remaining 80s'):
        execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=allowance)
    assert not (execution._directory(manifest, root) / 'batches/batch-0.json').exists()


def test_batch_allowance_counts_parallel_task_timeout_once():
    manifest = {'budgets': {'drain_reserve_s': 120}, 'cells': [
        {'batch_id': 'batch', 'arm_id': 'arm', 'spec': {'timeout_s': timeout}} for timeout in (10, 20, 30, 40)]}
    assert execution.required_batch_allowance(manifest, 'batch') == 60 + 40 + 120


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


@pytest.mark.parametrize('outcome, reason', [
    ('http', 'http_status'), ('stale', 'stale_engine_sample'), ('busy', 'busy'),
    ('transport', 'transport'), ('ready', None),
])
def test_release_requires_single_fresh_idle_observer_read_before_sampler(tmp_path, monkeypatch, outcome, reason):
    """Observer readiness fails before telemetry setup, receipt, or worker traffic."""
    from anvil_serving.router import model_capacity
    root, manifest, bound = campaign(tmp_path, monkeypatch, drain_reserve_s=70,
        drain={'running_metric': 'running', 'waiting_metric': 'waiting',
               'metric_labels': {'model_name': 'model'}, 'timeout_s': 40, 'poll_s': .1})
    manifest['telemetry_collection'] = {}
    manifest['manifest_sha256'] = execution._manifest_digest(manifest)
    atomic_write_json(execution._directory(manifest, root) / 'manifest.json', manifest)
    bound = [execution.bind_campaign_child(manifest['cells'][0]['spec'], manifest_sha256=manifest['manifest_sha256'],
        campaign_id=manifest['campaign_id'], cell_id='cell-0', arm_id='arm', batch_id='batch-0')]
    ready(root, manifest, bound)
    monkeypatch.setattr(execution.time, 'time', lambda: 100.0)
    calls = []
    def read(*args, **kwargs):
        calls.append((args, kwargs))
        if outcome == 'transport':
            raise OSError('offline')
        if outcome == 'http':
            return 503, b'unavailable'
        stamp = '0' if outcome == 'stale' else '100'
        running = '1' if outcome == 'busy' else '0'
        raw = ('running{model_name="model"} ' + running + '\n'
               'waiting{model_name="model"} 0\n'
               'anvil_observer_metrics_sample_timestamp_seconds{model="model"} ' + stamp).encode()
        return 200, raw
    monkeypatch.setattr(model_capacity, '_default_metrics_read', read)
    class Sampler:
        started = None
        def start(self, activation): self.started = activation
    sampler = Sampler()
    release_path = execution._directory(manifest, root) / 'batches/batch-0.json'
    if reason is not None:
        with pytest.raises(BenchmarkJobError) as error:
            execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=140, sampler=sampler)
        assert error.value.code == 'campaign_observer_not_ready'
        evidence = execution._read(execution._directory(manifest, root) / 'readiness/batch-0.json')
        assert evidence['status'] == 'not_ready' and evidence['reason'] == reason
        outcome = 'busy'
        with pytest.raises(BenchmarkJobError):
            execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=140, sampler=sampler)
        assert execution._read(execution._directory(manifest, root) / 'readiness/batch-0.json') == evidence
        assert sampler.started is None and not release_path.exists()
        assert not (execution._directory(manifest, root) / 'telemetry').exists()
    else:
        release = execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=140, sampler=sampler)
        assert release['observer_readiness'] == {'status': 'ready', 'source_timestamp_seconds': 100.0}
        assert sampler.started == release['activation'] and release_path.exists()
        legacy = dict(release); legacy.pop('observer_readiness')
        execution._validate_release(legacy, manifest, 'batch-0')
        with pytest.raises(BenchmarkJobError) as error:
            execution._require_observer_readiness(legacy, manifest)
        assert error.value.code == 'campaign_observer_not_ready'
        atomic_write_json(release_path, legacy)
        assert execution.reconcile_batch(manifest, 'batch-0', run_root=root)['status'] == 'blocked'
    assert len(calls) == (2 if reason is not None else 1) and calls[0][1]['timeout'] == 2 and calls[0][1]['max_bytes'] == 1024 * 1024


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


def install_test_owned_activation(tmp_path, monkeypatch, activation, *, watchdog_startup_delay_s=0):
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
    )
    monkeypatch.setenv('PYTHONPATH', str(hook))


def test_activation_hook_pins_checkout_before_conflicting_package(tmp_path, monkeypatch):
    activation = {'status': 'test-owned'}
    install_test_owned_activation(tmp_path, monkeypatch, activation)
    conflicting_root = tmp_path / 'other-install'
    conflicting_package = conflicting_root / 'anvil_serving'
    conflicting_package.mkdir(parents=True)
    (conflicting_package / '__init__.py').write_text('raise RuntimeError("wrong checkout imported")\n')
    monkeypatch.setenv('PYTHONPATH', os.environ['PYTHONPATH'] + os.pathsep + str(conflicting_root))
    result = subprocess.run(
        [sys.executable, '-c',
         'import json; from anvil_serving.benchmarking import campaign_execution as e; '
         'print(json.dumps({"source": e.__file__, "activation": e._activation({})}))'],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    assert json.loads(result.stdout) == {'source': execution.__file__, 'activation': activation}


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
        install_test_owned_activation(tmp_path, monkeypatch, activation)
        root, manifest, bound = campaign(tmp_path, monkeypatch, endpoint=address+'/v1', activation=activation,
            drain={'running_metric': 'running', 'waiting_metric': 'waiting', 'metric_labels': {'model_name': 'model'}, 'timeout_s': 40, 'poll_s': .01}, drain_reserve_s=70)
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
    finally:
        if launched and worker._process_group_running(launched['pid']): worker.cancel_benchmark_job(store, bound[0]['run_id'])
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_real_released_watchdog_stops_owned_child_at_traffic_deadline(tmp_path, monkeypatch):
    """The released worker and its detached watchdog use the real custody path."""
    from http.server import ThreadingHTTPServer
    import threading
    from tests.test_benchmark_worker import EndpointHandler

    post_started = threading.Event()
    stop_stream = threading.Event()

    class SlowEndpoint(EndpointHandler):
        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(size)
            post_started.set()
            # Keep an actual in-flight HTTP response active past the socket
            # timeout: urllib's timeout applies to an idle recv, not its
            # total response lifetime.  The watchdog must own cancellation.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(65536))
            self.end_headers()
            try:
                while not stop_stream.is_set():
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass

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
        # Reproduce a loaded runner where watchdog startup exceeds the former
        # two-second traffic window, without weakening real process custody.
        install_test_owned_activation(tmp_path, monkeypatch, activation, watchdog_startup_delay_s=3)
        root, manifest, bound = campaign(tmp_path, monkeypatch, endpoint=address+'/v1', activation=activation, timeout_s=1)
        store = BenchmarkJobStore(str(tmp_path/'jobs.db'), run_root=root); store.submit(bound[0])
        launched = worker.launch_benchmark_job(path=store.path, run_root=root, run_id=bound[0]['run_id'])
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not execution.batch_ready(manifest, 'batch-0', run_root=root): time.sleep(.02)
        assert execution.batch_ready(manifest, 'batch-0', run_root=root), store.logs(bound[0]['run_id'])
        # Model a historical short release: reading it must retain watchdog
        # enforcement even though new admission now refuses this allowance.
        with monkeypatch.context() as historical:
            historical.setattr(execution, 'require_batch_allowance', lambda *a, **k: k['allowance_s'])
            execution.release_batch(manifest, 'batch-0', run_root=root, allowance_s=20)
        assert post_started.wait(timeout=5), store.logs(bound[0]['run_id'])
        while time.monotonic() < deadline:
            result = store.status(bound[0]['run_id'])
            if result['state'] in {'completed', 'failed', 'cancelled'}: break
            time.sleep(.05)
        assert result['state'] == 'cancelled', json.dumps(store.logs(bound[0]['run_id']), sort_keys=True)
        assert not worker._process_group_running(launched['pid'])
    finally:
        if launched and worker._process_group_running(launched['pid']): worker.cancel_benchmark_job(store, bound[0]['run_id'])
        stop_stream.set()
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
    monkeypatch.setattr(execution, '_release_readiness', lambda *args, **kwargs:
                        {'status': 'ready', 'source_timestamp_seconds': 0})
    monkeypatch.setattr(worker, 'run_benchmark_preflight', lambda *args, **kwargs: pytest.fail('model traffic'))
    monkeypatch.setattr(worker, '_run_suite', lambda *args, **kwargs: pytest.fail('model traffic'))
    sampler = telemetry.BatchSampler(manifest, cell['batch_id'], run_root=root)
    release = execution.release_batch(manifest, cell['batch_id'], run_root=root,
                                      allowance_s=190, store=store, sampler=sampler)
    return store, manifest, child, sampler, release


@pytest.mark.parametrize('damage', ['malformed', 'symlink', 'unreadable'])
def test_unreleased_cancellation_requires_genuinely_absent_release(tmp_path, monkeypatch, damage):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    child = bound[0]
    store = BenchmarkJobStore(str(tmp_path / 'jobs.sqlite'), run_root=root)
    store.submit(child)
    store.claim(child['run_id'])
    store.transition(child['run_id'], 'cancelling')
    ready(root, manifest, bound)
    release_path = execution._directory(manifest, root) / 'batches' / 'batch-0.json'
    release_path.parent.mkdir()
    if damage == 'malformed':
        release_path.write_text('{')
    elif damage == 'symlink':
        target = release_path.with_name('saved-release.json')
        target.write_text('{}')
        release_path.symlink_to(target)
    else:
        monkeypatch.setattr(Path, 'lstat', lambda self: (_ for _ in ()).throw(PermissionError()))

    with pytest.raises((OSError, ValueError)):
        execution.campaign_result_reference(store, child, cancelling=True)

    receipt = execution._read(execution._path(child, store.run_root, 'campaign-execution.json'))
    assert receipt['status'] == 'ready' and 'telemetry_terminal' not in receipt
    assert store.status(child['run_id'])['state'] == 'cancelling'


@pytest.mark.parametrize('field, value', [('clients_stopped', True), ('drain', {'status': 'unknown'})])
def test_unreleased_cancellation_rejects_lifecycle_smuggling(tmp_path, monkeypatch, field, value):
    root, manifest, bound = campaign(tmp_path, monkeypatch)
    child = bound[0]
    store = BenchmarkJobStore(str(tmp_path / 'jobs.sqlite'), run_root=root)
    store.submit(child)
    store.claim(child['run_id'])
    store.transition(child['run_id'], 'cancelling')
    receipt = ready(root, manifest, bound)
    receipt[field] = value
    path = execution._path(child, root, 'campaign-execution.json')
    atomic_write_json(path, receipt)

    with pytest.raises(BenchmarkJobError, match='receipt shows traffic or closure'):
        execution.campaign_result_reference(store, child, cancelling=True)

    assert execution._read(path) == receipt
    assert store.status(child['run_id'])['state'] == 'cancelling'


@pytest.mark.parametrize('failure', ['no_ack', 'early_exit', 'spawn', 'activation', 'closure', 'prepared', 'cancel', 'budget'])
def test_entry_failure_closes_before_shared_terminal_and_durable_failure(tmp_path, monkeypatch, failure):
    from concurrent.futures import ThreadPoolExecutor
    import subprocess
    import sys
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


@pytest.mark.parametrize('failure', ['missing', 'invalid', 'malformed', 'expired', 'future', 'wrong_boot'])
def test_untrusted_entry_release_reaches_failed_without_fabricating_closure(tmp_path, monkeypatch, failure):
    from types import SimpleNamespace
    store, manifest, child, sampler, release = entry_campaign(tmp_path, monkeypatch)
    path = sampler.root / 'batches' / (sampler.batch_id + '.json')
    expected = 'campaign_release_invalid'
    if failure == 'missing':
        path.unlink()
        clock = [time.monotonic()]
        monkeypatch.setattr(execution, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=time.time,
            perf_counter=time.perf_counter, sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
        expected = 'campaign_release_missing'
    elif failure == 'invalid':
        atomic_write_json(path, {**release, 'manifest_sha256': 'f' * 64})
    elif failure == 'malformed':
        path.write_text('{')
        expected = 'JSONDecodeError'
    else:
        if failure == 'wrong_boot':
            release['boot_id'] = 'different-boot'
        else:
            for key in ('released_monotonic', 'traffic_deadline_monotonic', 'deadline_monotonic'):
                release[key] += 1000 if failure == 'future' else -1000
        atomic_write_json(path, release)
    monkeypatch.setattr(execution.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('untrusted release spawned watchdog'))
    result = worker.execute_benchmark_job(store, child['run_id'])
    assert result['state'] == 'failed' and result['finished_at']
    assert result['failure']['code'] == expected
    receipt_path = execution._path(child, store.run_root, 'campaign-execution.json')
    receipt = execution._read(receipt_path)
    assert receipt['status'] == 'blocked' and receipt['clients_stopped'] is True
    assert receipt['drain']['status'] == 'unknown'
    assert receipt['telemetry_terminal']['status'] == 'unavailable'
    assert all(v['status'] == 'unavailable' for v in receipt['telemetry_coverage'].values())
    assert store.artifact(child['run_id'])['results']['campaign_execution']['sha256']
    # The failure fallback cannot bless a foreign receipt or unverified release.
    assert 'closed_monotonic' not in receipt and 'active_seconds' not in receipt


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


@pytest.mark.parametrize('mode', ['delayed', 'partial_deadline'])
def test_watchdog_acknowledgement_is_bounded_and_allows_verified_slow_start(tmp_path, monkeypatch, mode):
    import subprocess
    import sys
    store, manifest, child, sampler, release = entry_campaign(tmp_path, monkeypatch)
    store.claim(child['run_id'])
    path = sampler.root / 'batches' / (sampler.batch_id + '.json')
    if mode == 'partial_deadline':
        release['released_monotonic'] = time.monotonic() - 69.7
        release['traffic_deadline_monotonic'] = release['released_monotonic'] + 70
        release['deadline_monotonic'] = release['released_monotonic'] + 190
        atomic_write_json(path, release)
    original_popen = subprocess.Popen
    supervisors = []
    def spawn(*args, **kwargs):
        code = ('import time; time.sleep(2.1); print("ready", flush=True); time.sleep(60)' if mode == 'delayed'
                else 'import time; print("r", end="", flush=True); time.sleep(60)')
        process = original_popen([sys.executable, '-c', code], **kwargs)
        supervisors.append(process)
        return process
    monkeypatch.setattr(execution.subprocess, 'Popen', spawn)
    started = time.monotonic()
    try:
        if mode == 'delayed':
            context = execution.enter_campaign(store, child)
            assert context['receipt']['status'] == 'active'
            assert 2 < time.monotonic() - started < 8
            assert supervisors[0].poll() is None and supervisors[0].stdout.closed
        else:
            with pytest.raises(BenchmarkJobError, match='did not acknowledge'):
                execution.enter_campaign(store, child)
            assert time.monotonic() - started < 2
            assert supervisors[0].poll() is not None
            assert execution._read(execution._path(child, store.run_root, 'campaign-execution.json'))['status'] == 'draining'
    finally:
        for process in supervisors:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)


def test_campaign_aider_child_forwards_admitted_task_to_preparation(tmp_path, monkeypatch):
    from anvil_serving.benchmarking import aider, harnesses
    from anvil_serving.benchmarking.submission import bind_submission_spec
    requested = ['python/exercises/practice/example']
    child = spec()
    child.update(suite='aider', profile='pilot-v2', parameters={'instance_ids': requested})
    child = bind_submission_spec(child)
    calls = []
    def prepare(profile, **kwargs):
        calls.append(kwargs)
        return {'selection': kwargs.get('aider_instance_ids')}
    def plan(profile, assets, **kwargs):
        assert assets['selection'] == kwargs['instance_ids']
        return {'selection': kwargs['instance_ids']}
    monkeypatch.setattr(harnesses, 'prepare_harness_assets', prepare)
    monkeypatch.setattr(aider, 'build_aider_run_plan', plan)
    (tmp_path/'runs').mkdir()
    execution.prepare_child(child, run_root=str(tmp_path/'runs'), cache_root=str(tmp_path/'cache'), offline=True)
    assert calls[0]['aider_instance_ids'] == requested and calls[0]['offline'] is True
    # An invalid child must fail admission without touching the preparer.
    invalid = copy.deepcopy(child)
    invalid['parameters']['instance_ids'] = ['python/../escape']
    with pytest.raises(BenchmarkJobError):
        execution.prepare_child(invalid, run_root=str(tmp_path/'runs'), cache_root=str(tmp_path/'cache'))
    assert len(calls) == 1


def test_swe_preparation_receipt_binds_job_and_image_id_and_registry_digest(tmp_path):
    from anvil_serving.benchmarking.swe_images import instance_image_tag
    from anvil_serving.benchmarking.submission import bind_submission_spec
    import socket
    value = spec()
    value['worker']['id'] = socket.gethostname()
    instance, image, digest = 'example__project-1', 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64
    value.update(profile='pilot-v2', suite='swe', timeout_s=30,
                 parameters={'instance_ids': [instance], 'paired_image_ids': {instance: image}, 'paired_image_repo_digests': {instance: digest}})
    value = bind_submission_spec(value)
    tag = instance_image_tag(instance)
    observed = {'tag': tag, 'image_id': image, 'repo_digests': ['example@' + digest]}
    row = {'instance_id': instance, 'tag': tag, 'image_id': image, 'registry_digest': digest, 'status': 'verified',
           'observation': observed, 'pinned_observation': {**observed, 'tag': 'docker.io/' + tag + '@' + digest}}
    receipt = {'schema': 'anvil-serving.swe-image-preparation/v1', 'status': 'verified',
        'spec_sha256': job_spec_sha256(value), 'ownership_id': value['ownership_id'], 'run_id': value['run_id'],
        'worker': {'declared_id': value['worker']['id'], 'observed_hostname': socket.gethostname()}, 'images': [row]}
    path = execution._path(value, str(tmp_path), 'swe-images/' + job_spec_sha256(value) + '/receipt-' + 'c' * 32 + '.json')
    path.parent.mkdir(parents=True); atomic_write_json(path, receipt)
    reference = execution._swe_image_receipt(value, str(tmp_path))
    assert execution._swe_image_receipt(value, str(tmp_path), reference) == reference
    receipt['images'][0]['observation']['repo_digests'] = ['example@sha256:' + 'd' * 64]
    atomic_write_json(path, receipt)
    with pytest.raises(BenchmarkJobError, match='absent or changed'):
        execution._swe_image_receipt(value, str(tmp_path), reference)


@pytest.mark.parametrize('custody', ['completed', 'partial', 'unknown', 'cancel_unknown'])
def test_swe_real_entry_and_closure_keep_unknown_custody_blocked(tmp_path, monkeypatch, custody):
    from concurrent.futures import ThreadPoolExecutor
    from anvil_serving.benchmarking.submission import bind_submission_spec

    root, manifest, _ = campaign(tmp_path, monkeypatch, timeout_s=10, drain_reserve_s=70,
        drain={'running_metric': 'running', 'waiting_metric': 'waiting',
               'metric_labels': {'model_name': 'model'}, 'timeout_s': 40, 'poll_s': .01})
    cell = manifest['cells'][0]
    base = copy.deepcopy(cell['spec'])
    base.update(suite='swe', profile='pilot-v2', parameters={
        'instance_ids': ['example__project-1'],
        'paired_image_ids': {'example__project-1': 'sha256:' + 'a' * 64},
        'paired_image_repo_digests': {'example__project-1': 'sha256:' + 'b' * 64}})
    base = bind_submission_spec(base)
    cell.update(spec=base, spec_sha256=job_spec_sha256(base))
    manifest['manifest_sha256'] = execution._manifest_digest(manifest)
    directory = execution._directory(manifest, root)
    atomic_write_json(directory / 'manifest.json', manifest)
    atomic_write_json(directory / 'state.json', execution._new_state(manifest))
    child = execution.bind_campaign_child(base, manifest_sha256=manifest['manifest_sha256'],
        campaign_id=manifest['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
    store = BenchmarkJobStore(str(tmp_path / 'jobs.db'), run_root=root)
    store.submit(child)
    frozen = {'fixture': 'exact-prepared-plan'}
    monkeypatch.setattr(execution, 'verify_prepared_child', lambda *a, **k: {'assets': {}, 'plan': frozen})
    monkeypatch.setattr(execution, '_clients_stopped', lambda: True)
    monkeypatch.setattr(execution, 'observe_drain', lambda *a, **k: {'status': 'verified'})
    monkeypatch.setattr(worker, 'run_benchmark_preflight', lambda *a, **k:
        {'schema': 'anvil-serving.benchmark-preflight/v1', 'passed': True, 'observed': {}, 'checks': []})
    class FakeWatchdog:
        def __init__(self):
            reader, writer = os.pipe()
            os.write(writer, b'ready\n'); os.close(writer)
            self.stdout = os.fdopen(reader, 'rb', buffering=0)
        def poll(self): return None
    monkeypatch.setattr(execution.subprocess, 'Popen', lambda *a, **k: FakeWatchdog())
    import threading
    events, entered, resume, exited = [], threading.Event(), threading.Event(), threading.Event()
    def run(plan, **kwargs):
        assert plan == frozen
        events.append('run')
        execution._path(child, root, 'work').mkdir(parents=True, exist_ok=True)
        entered.set()
        if custody == 'cancel_unknown':
            assert resume.wait(5)
        return {'state': 'incomplete' if custody == 'partial' else 'completed', 'summary': {'completed': True}}
    def retain(*args, **kwargs):
        events.append('retention')
        if custody == 'partial':
            from anvil_serving.benchmarking.retention import retain_swe_artifacts
            return {**retain_swe_artifacts(run_path=kwargs['run_path'], work_path=kwargs['work_path']), 'containers': {'status': 'completed'}}
        return {'state': 'retained' if custody == 'completed' else 'incomplete' if custody == 'cancel_unknown' else 'failed',
                'reason': 'swe_container_cleanup_unverified', 'containers': {'status': 'unknown' if custody == 'cancel_unknown' else custody}, 'manifest_reference': {'path': 'fixture'}}
    monkeypatch.setattr(worker, 'run_swe_benchmark', run)
    monkeypatch.setattr(worker, '_retain_stopped_swe', retain)
    real_retention_check = worker._swe_retention_complete
    monkeypatch.setattr(worker, '_swe_retention_complete', lambda value, path: real_retention_check(value, path) if custody == 'partial' else value.get('state') == 'retained' and value.get('containers', {}).get('status') == 'completed')
    def execute():
        try:
            return worker.execute_benchmark_job(store, child['run_id'])
        finally:
            exited.set()
    def stop(*args, **kwargs):
        resume.set()
        assert exited.wait(5)
        return {'stopped': True}
    monkeypatch.setattr(worker, '_stop_owned_worker', stop)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not execution.batch_ready(manifest, cell['batch_id'], run_root=root):
            time.sleep(.01)
        assert execution.batch_ready(manifest, cell['batch_id'], run_root=root)
        execution.release_batch(manifest, cell['batch_id'], run_root=root, allowance_s=140, store=store)
        assert entered.wait(5)
        if custody == 'cancel_unknown':
            cancelled = worker.cancel_campaign_jobs(manifest, store)
            assert cancelled['children'][0]['state'] == 'cancelled'
            assert cancelled['children'][0]['cleanup_deferred'] is True
            assert execution._path(child, root, 'work').is_dir()
            receipt = execution._read(execution._path(child, root, 'campaign-execution.json'))
            closure = execution.batch_closure(manifest, cell['batch_id'], execution._read(directory / 'batches' / (cell['batch_id'] + '.json')), run_root=root)[0]
            assert events[:2] == ['run', 'retention']
            assert closure['status'] == 'blocked' and receipt['clients_stopped'] is False
            assert execution.reconcile_batch(manifest, cell['batch_id'], run_root=root, store=store)['active_seconds'] is None
            return
        while time.monotonic() < deadline:
            receipt = execution._read(execution._path(child, root, 'campaign-execution.json'))
            if receipt['status'] in {'draining', 'blocked'}:
                break
            time.sleep(.01)
        assert events[0:2] == ['run', 'retention']
        closure = execution.coordinate_batch(manifest, cell['batch_id'], store=store, force=True)
        result = future.result(timeout=5)
    assert result['state'] == ('completed' if custody == 'completed' else 'failed')
    assert closure['status'] == ('closed' if custody in {'completed', 'partial'} else 'blocked')
    reconciled = execution.reconcile_batch(manifest, cell['batch_id'], run_root=root, store=store)
    assert reconciled['status'] == closure['status']
    if custody == 'unknown':
        assert reconciled['active_seconds'] is None
        assert not receipt['clients_stopped']


def test_swe_campaign_early_cancellation_records_proven_absence(tmp_path, monkeypatch):
    from anvil_serving.benchmarking.submission import bind_submission_spec
    root, manifest, _ = campaign(tmp_path, monkeypatch)
    cell = manifest['cells'][0]
    base = copy.deepcopy(cell['spec'])
    base.update(suite='swe', profile='pilot-v2', parameters={
        'instance_ids': ['example__project-1'],
        'paired_image_ids': {'example__project-1': 'sha256:' + 'a' * 64},
        'paired_image_repo_digests': {'example__project-1': 'sha256:' + 'b' * 64}})
    base = bind_submission_spec(base)
    cell.update(spec=base, spec_sha256=job_spec_sha256(base))
    manifest['manifest_sha256'] = execution._manifest_digest(manifest)
    directory = execution._directory(manifest, root)
    atomic_write_json(directory / 'manifest.json', manifest)
    atomic_write_json(directory / 'state.json', execution._new_state(manifest))
    child = execution.bind_campaign_child(base, manifest_sha256=manifest['manifest_sha256'],
        campaign_id=manifest['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
    store = BenchmarkJobStore(str(tmp_path / 'jobs.db'), run_root=root)
    store.submit(child)
    monkeypatch.setattr(worker, '_stop_owned_worker', lambda *a, **k: {'stopped': True})
    result = worker.cancel_campaign_jobs(manifest, store)
    assert result['phase'] == 'completed'
    assert result['children'][0]['state'] == 'cancelled'
    assert result['children'][0]['failure']['code'] == 'cancelled'
    assert result['children'][0]['cleanup_deferred'] is False
    artifact = store.artifact(child['run_id'])
    assert artifact['results']['container_custody']['reason'] == 'not_started'
    assert not execution._path(child, root, 'work').exists()
