"""Offline source-clock, custody, immutable-chain and shared-barrier checks."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from anvil_serving.benchmarking import campaign_telemetry as telemetry, campaign_execution as execution, campaigns
from anvil_serving.benchmarking.artifacts import atomic_write_json
from anvil_serving.benchmarking.jobs import job_spec_sha256
from anvil_serving.control_plane.controller.store import BenchmarkJobStore
from tests.test_benchmark_campaigns import specification


def contract():
    return {'schema': telemetry.SCHEMA, 'cadence_s': 5, 'max_gap_s': 40, 'timeout_s': 2,
        'max_rows': 1000, 'max_bytes': 1024 * 1024,
        'gpu': {'url': 'http://127.0.0.1:9835/metrics', 'timestamp_metric': 'gpu_timestamp',
            'identity_label': 'uuid', 'identity_prefix': 'GPU-', 'success_metric': 'gpu_success',
            'metrics': {'gpu_memory_bytes': 'gauge', 'gpu_watts': 'gauge'}},
        'host': {'url': 'http://127.0.0.1:9100/metrics', 'timestamp_metric': 'node_time_seconds',
            'metrics': {'node_memory_bytes': 'gauge', 'node_oom_total': 'counter'}}}


def setup(tmp_path, monkeypatch, *, count=1, mixed=False):
    tasks = [('agentic', task) for task in ('structured-edit', 'tool-sequence', 'parallel-tools', 'dependent-result')][:count]
    if mixed:
        tasks[2:] = [('aider', lang + '/exercises/practice/task') for lang in ('cpp', 'go')]
    value = specification(concurrency=count, tasks=tasks)
    value['budgets']['drain_reserve_s'] = 120
    value['telemetry_collection'] = contract()
    value['drain_policy'] = {'running_metric': 'running', 'waiting_metric': 'waiting',
        'metric_labels': {'model_name': 'served-model'}, 'timeout_s': 40, 'poll_s': .1}
    manifest = campaigns.plan_campaign(value)
    manifest['manifest_sha256'] = execution._manifest_digest(manifest)
    store = BenchmarkJobStore(str(tmp_path / 'jobs.sqlite'), run_root=str(tmp_path / 'runs'))
    root = execution._directory(manifest, store.run_root)
    root.mkdir(parents=True)
    atomic_write_json(root / 'manifest.json', manifest)
    atomic_write_json(root / 'state.json', campaigns._new_state(manifest))
    activation = {'status': 'verified', 'generation': 'a' * 64, 'container_id': 'b' * 64,
        'started_at': '2026-01-01T00:00:00Z', 'served_model': 'served-model',
        'metrics_url': 'http://127.0.0.1:19100/engine/' + 'a' * 64 + '/metrics'}
    activation['binding_sha256'] = execution._digest({k: v for k, v in activation.items() if k != 'status'})
    monkeypatch.setattr(execution, '_activation', lambda arm, expected=None: activation)
    monkeypatch.setattr(execution, 'observe_drain', lambda *a, **k: {'status': 'verified'})
    clock = [1000.]
    timer = SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000 + clock[0],
                            sleep=time.sleep, perf_counter=time.perf_counter)
    monkeypatch.setattr(telemetry, 'time', timer)
    monkeypatch.setattr(execution, 'time', timer)
    def read(url, *args, **kwargs):
        stamp = str(1000 + clock[0])
        if '/engine/' in url:
            raw = 'running{model_name="served-model"} 0\nwaiting{model_name="served-model"} 0\n' + telemetry.STAMP + '{model="served-model"} ' + stamp
        elif ':9835/' in url:
            raw = 'gpu_timestamp ' + stamp + '\ngpu_success 1\ngpu_memory_bytes{uuid="a",serial="never-retain"} 5\ngpu_watts{uuid="a"} 30'
        else:
            raw = 'node_time_seconds ' + stamp + '\nnode_memory_bytes 500\nnode_oom_total 0'
        return 200, raw.encode()
    monkeypatch.setattr('anvil_serving.router.model_capacity._default_metrics_read', read)
    monkeypatch.setattr(telemetry, '_container', lambda *a: dict.fromkeys(telemetry._fields(contract(), manifest['arms'][0], 'cgroup'), 0))
    bound = []
    for cell in manifest['cells']:
        spec = execution.bind_campaign_child(cell['spec'], manifest_sha256=manifest['manifest_sha256'],
            campaign_id=manifest['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
        store.submit(spec); store.claim(spec['run_id']); bound.append(spec)
        execution._path(spec, store.run_root, 'campaign-execution.json').parent.mkdir(parents=True)
        atomic_write_json(execution._path(spec, store.run_root, 'campaign-execution.json'), {
            'schema': 'anvil-serving.campaign-execution/v1', 'campaign': spec['campaign'],
            'spec_sha256': job_spec_sha256(spec), 'status': 'ready', 'boot_id': execution._boot_id()})
    sampler = telemetry.BatchSampler(manifest, manifest['cells'][0]['batch_id'], run_root=store.run_root)
    release = execution.release_batch(manifest, sampler.batch_id, run_root=store.run_root, allowance_s=210, store=store, sampler=sampler)
    return manifest, store, bound, sampler, release, clock


def close(store, specs, release, clock):
    clock[0] += 2
    for spec in specs:
        path = execution._path(spec, store.run_root, 'campaign-execution.json')
        value = execution._read(path)
        value.update(status='closed', activation=release['activation'], clients_stopped=True,
            drain={'status': 'verified'}, clients_stopped_wall=1000 + clock[0],
            started_monotonic=release['released_monotonic'], closed_monotonic=clock[0])
        atomic_write_json(path, value)
    clock[0] += 1


def test_c4_one_shared_sampler_barrier_precedes_all_immutable_artifacts(tmp_path, monkeypatch):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch, count=4)
    assert len(sampler.rows) == 1  # One pre-release cycle, not four child pollers.
    close(store, specs, release, clock)
    def finish_child(spec):
        return store.transition(spec['run_id'], 'completed', results=execution.campaign_result_reference(store, spec))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(finish_child, spec) for spec in specs]
        time.sleep(.1)
        assert not any(f.done() for f in futures)
        execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler)
        assert all(f.result(3)['state'] == 'completed' for f in futures)
    references = [execution._read(execution._path(s, store.run_root, 'campaign-execution.json'))['telemetry_terminal'] for s in specs]
    assert all(r == references[0] for r in references)
    assert 'never-retain' not in (sampler.root / (sampler.relative + 'samples.jsonl')).read_text()
    assert execution.reconcile_batch(manifest, sampler.batch_id, run_root=store.run_root, store=store)['active_seconds'] == 3
    from anvil_serving.benchmarking.campaign_report import report_campaign
    report = report_campaign(manifest, store)
    assert all(set(c['telemetry_coverage'].values()) == {'measured'} for c in report['cells'])
    assert not any(text in json.dumps(report) for text in ('127.0.0.1', 'GPU-a', 'served-model', 'never-retain'))
    original = execution._path(specs[0], store.run_root, 'campaign-execution.json').read_bytes()
    execution.campaign_result_reference(store, specs[0])
    assert execution._path(specs[0], store.run_root, 'campaign-execution.json').read_bytes() == original


def test_coordinator_loss_and_partial_cancel_cannot_create_complete_coverage(tmp_path, monkeypatch):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch)
    close(store, specs, release, clock)
    clock[0] = release['deadline_monotonic']
    execution.campaign_result_reference(store, specs[0])
    receipt = execution._read(execution._path(specs[0], store.run_root, 'campaign-execution.json'))
    assert receipt['telemetry_terminal']['status'] == 'unavailable'
    assert all(v['status'] == 'unavailable' for v in receipt['telemetry_coverage'].values())
    sampler.finish(partial=True)
    terminal = execution._read(sampler.root / (sampler.relative + 'terminal.json'))
    assert terminal['complete'] is False
    assert not any(v['complete'] for v in terminal['coverage'].values())


@pytest.mark.parametrize('drift', ['container_id', 'started_at', 'image_digest', 'gpu_selection', 'served_identity'])
def test_managed_cgroup_discovery_is_bounded_and_rejects_incarnation_drift(monkeypatch, drift):
    from anvil_serving import serve_recipes, transports
    arm = specification()['arms'][0]
    activation = {'container_id': 'b' * 64, 'started_at': 'start', 'served_model': 'served-model'}
    row = {'container': arm['activation']['container'], 'container_id': activation['container_id'],
        'started_at': activation['started_at'], 'image_digest': arm['identity']['image'],
        'served_identity': activation['served_model'], 'recipe_digest': arm['identity']['recipe_sha256'],
        'gpu_selection': arm['identity']['hardware'], 'running': True,
        'host_memory': {'current_bytes': 1, 'peak_bytes': 2, 'limit_bytes': 5,
                        'ram_plus_swap_limit_bytes': 5, 'events': {'oom': 0, 'oom_kill': 0}, 'oom_killed': False}}
    def bounded(argv, **kwargs):
        assert 0 < kwargs['timeout'] <= 2
        assert kwargs['env'] == {'PATH': __import__('os').defpath}
        assert kwargs['max_output_bytes'] == 4 * telemetry.MAX_READ
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
    def discover(*, _run, strict):
        assert strict is True
        _run(['read-only-fixture'])
        return {'containers': [row]}
    monkeypatch.setattr(transports, '_run_bounded_process', bounded)
    monkeypatch.setattr(serve_recipes, 'discover_recipe_containers', discover)
    assert telemetry._container(arm, activation, 2)['peak_bytes'] == 2
    row[drift] = ['GPU-other'] if drift == 'gpu_selection' else 'other'
    with pytest.raises(ValueError, match='identity_changed'):
        telemetry._container(arm, activation, 2)


def test_closed_terminal_receipt_cannot_be_reclosed_by_late_cancellation(tmp_path, monkeypatch):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch)
    close(store, specs, release, clock)
    execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler)
    spec = specs[0]
    store.transition(spec['run_id'], 'completed', results=execution.campaign_result_reference(store, spec))
    path = execution._path(spec, store.run_root, 'campaign-execution.json')
    original = path.read_bytes()
    execution.close_campaign({'store': store, 'spec': spec, 'path': path}, clients_stopped=False)
    assert path.read_bytes() == original
