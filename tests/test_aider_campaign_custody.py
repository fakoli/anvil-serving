"""Real campaign release, worker, cleanup, and closure with fake containers/models."""
import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
import time
import threading

import pytest

from anvil_serving.benchmarking import campaign_execution as execution, worker, aider, aider_containment
from anvil_serving.benchmarking.artifacts import atomic_write_json
from anvil_serving.benchmarking.jobs import BenchmarkJobError, job_spec_sha256
from anvil_serving.benchmarking.submission import bind_submission_spec
from anvil_serving.control_plane.controller.store import BenchmarkJobStore
from tests.test_benchmark_campaign_execution import campaign


@pytest.mark.parametrize('mode', ['success', 'success_manifest_after', 'success_record_after', 'success_registry_after', 'success_stop_after', 'unknown', 'timeout', 'malformed_timeout', 'timeout_added_after', 'manifest_drift', 'record_drift', 'cancel_partial', 'cancel_partial_stop_after', 'cancel_unknown', 'cancel_prework', 'cancel_prepared'])
def test_aider_campaign_requires_retention_and_custody_before_closure_and_next_admission(tmp_path, monkeypatch, mode):
    root, manifest, _ = campaign(tmp_path, monkeypatch, count=2, timeout_s=10, drain_reserve_s=70,
        drain={'running_metric': 'running', 'waiting_metric': 'waiting',
               'metric_labels': {'model_name': 'model'}, 'timeout_s': 40, 'poll_s': .01})
    for index, cell in enumerate(manifest['cells']):
        base = copy.deepcopy(cell['spec'])
        base.update(suite='aider', profile='pilot-v2', parameters={'instance_ids': ['python/exercises/practice/phone-number']})
        base = bind_submission_spec(base)
        cell.update(spec=base, spec_sha256=job_spec_sha256(base), prepared={'custody_contract': 'aider-stop/v1'})
    manifest['manifest_sha256'] = execution._manifest_digest(manifest)
    directory = execution._directory(manifest, root)
    atomic_write_json(directory / 'manifest.json', manifest)
    atomic_write_json(directory / 'state.json', execution._new_state(manifest))
    cell = manifest['cells'][0]
    child = execution.bind_campaign_child(cell['spec'], manifest_sha256=manifest['manifest_sha256'],
        campaign_id=manifest['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
    store = BenchmarkJobStore(str(tmp_path / 'jobs.db'), run_root=root)
    store.submit(child)
    if mode == 'cancel_prepared':
        execution._path(child, root, 'aider/containers').mkdir(parents=True, exist_ok=True)
        execution._path(child, root, 'aider/preparation/receipt.json').parent.mkdir(parents=True, exist_ok=True)
        execution._path(child, root, 'aider/preparation/receipt.json').write_text('{}')
    frozen = {'fixture': 'exact-prepared-plan'}
    monkeypatch.setattr(execution, 'verify_prepared_child', lambda *a, **k: {'assets': {}, 'plan': frozen})
    monkeypatch.setattr(execution, '_clients_stopped', lambda: True)
    monkeypatch.setattr(execution, 'observe_drain', lambda *a, **k: {'status': 'verified'})
    class FakeWatchdog:
        def __init__(self):
            reader, writer = os.pipe(); os.write(writer, b'ready\n'); os.close(writer)
            self.stdout = os.fdopen(reader, 'rb', buffering=0)
        def poll(self): return None
    monkeypatch.setattr(execution.subprocess, 'Popen', lambda *a, **k: FakeWatchdog())
    entered, resume, exited = threading.Event(), threading.Event(), threading.Event()
    events = []
    def preflight(*args, **kwargs):
        if mode in {'cancel_prework', 'cancel_prepared'}:
            entered.set(); assert resume.wait(5)
        return {'schema': 'anvil-serving.benchmark-preflight/v1', 'passed': True, 'observed': {}, 'checks': []}
    monkeypatch.setattr(worker, 'run_benchmark_preflight', preflight)
    def run(plan):
        assert plan == frozen
        events.append('suite')
        base = execution._path(child, root, 'aider'); base.mkdir(parents=True)
        task = child['parameters']['instance_ids'][0]
        for name, value in {'plan.json': '{}', 'selection.json': json.dumps({'instance_ids': [task]}),
                            'result.json': '{}', 'process.json': '{}', 'requests.jsonl': '{}', 'adapter-result.json': '{}',
                            'results/' + task + '/.aider.results.json': '{}', 'results/' + task + '/.aider.chat.history.md': 'fixture'}.items():
            if mode in {'timeout', 'malformed_timeout', 'timeout_added_after', 'cancel_partial', 'cancel_partial_stop_after', 'cancel_unknown'} and name not in {'plan.json', 'selection.json'}:
                continue
            path = base / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)
        record = base / 'containers' / ('a' * 32 + '.json'); record.parent.mkdir()
        atomic_write_json(record, {'schema': 'anvil-serving.aider-container/v1', 'ownership_id': child['ownership_id'],
            'run_id': child['run_id'], 'token': 'a' * 32, 'name': 'anvil-aider-' + 'a' * 32, 'state': 'removed',
            'image_id': 'sha256:' + 'b' * 64, 'container_binary': '/usr/bin/docker', 'container_id': None})
        if mode == 'malformed_timeout': record.write_text('[]')
        entered.set()
        if mode in {'cancel_partial', 'cancel_partial_stop_after', 'cancel_unknown'}: assert resume.wait(5)
        if mode in {'timeout', 'malformed_timeout', 'timeout_added_after'}: raise TimeoutError('original suite timeout')
        return {'state': 'completed', 'summary': {'completed': True}}
    monkeypatch.setattr(aider, 'run_aider_benchmark', run)
    def cleanup(*, ownership_id, run_id, **kwargs):
        events.append('cleanup')
        return {'schema': 'anvil-serving.aider-container-cleanup/v1', 'ownership_id': ownership_id,
                'run_id': run_id, 'status': 'incomplete' if mode in {'unknown', 'cancel_unknown'} else 'completed',
                'removed': [], 'errors': ['unknown'] if mode in {'unknown', 'cancel_unknown'} else []}
    monkeypatch.setattr(aider_containment, 'cleanup_aider_containers', cleanup)
    def execute():
        try: return worker.execute_benchmark_job(store, child['run_id'])
        finally: exited.set()
    def stop(*args, **kwargs):
        resume.set(); assert exited.wait(5); return {'stopped': True}
    monkeypatch.setattr(worker, '_stop_owned_worker', stop)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not execution.batch_ready(manifest, cell['batch_id'], run_root=root): time.sleep(.01)
        assert execution.batch_ready(manifest, cell['batch_id'], run_root=root)
        execution.release_batch(manifest, cell['batch_id'], run_root=root, allowance_s=140, store=store)
        assert entered.wait(5)
        if mode.startswith('cancel_'):
            cancelled = worker.cancel_campaign_jobs(manifest, store)
            assert cancelled['children'][0]['state'] == 'cancelled'
            assert cancelled['children'][0]['cleanup_deferred'] is (mode == 'cancel_unknown')
            closure = execution.batch_closure(manifest, cell['batch_id'], execution._read(directory / 'batches' / (cell['batch_id'] + '.json')), run_root=root)[0]
        else:
            while time.monotonic() < deadline:
                receipt = execution._read(execution._path(child, root, 'campaign-execution.json'))
                if receipt['status'] in {'draining', 'blocked'} or future.done(): break
                time.sleep(.01)
            if mode == 'manifest_drift': execution._path(child, root, 'aider/retention.json').unlink()
            if mode == 'record_drift': execution._path(child, root, 'aider/containers/' + 'a' * 32 + '.json').write_text('{}')
            closure = execution.coordinate_batch(manifest, cell['batch_id'], store=store, force=True)
        result = future.result(timeout=5)
    blocked = mode in {'unknown', 'malformed_timeout', 'manifest_drift', 'record_drift', 'cancel_unknown'}
    assert closure['status'] == ('blocked' if blocked else 'closed')
    if mode in {'timeout', 'malformed_timeout', 'timeout_added_after'}: assert result['failure']['code'] == 'TimeoutError'
    if mode == 'unknown': assert result['failure']['code'] == 'aider_container_cleanup_unverified'
    if mode.startswith('success'): assert result['state'] == 'completed'
    if mode not in {'cancel_prework', 'cancel_prepared'}: assert events[:2] == ['suite', 'cleanup']
    if blocked:
        with pytest.raises(BenchmarkJobError, match='prior batch lacks verified closure'):
            execution._remaining_budget(manifest, manifest['cells'][1]['batch_id'], run_root=root, store=store)
    else:
        assert execution._remaining_budget(manifest, manifest['cells'][1]['batch_id'], run_root=root, store=store) > 0
        if mode == 'success_manifest_after': execution._path(child, root, 'aider/retention.json').unlink()
        if mode == 'success_record_after': execution._path(child, root, 'aider/containers/' + 'a' * 32 + '.json').write_text('{}')
        if mode == 'success_registry_after': execution._path(child, root, 'aider/containers/' + 'a' * 32 + '.json').unlink(); execution._path(child, root, 'aider/containers').rmdir()
        if mode == 'timeout_added_after': execution._path(child, root, 'aider/result.json').write_text('{}')
        if mode == 'cancel_partial_stop_after': execution._path(child, root, 'evidence/worker-stop-receipt.json').unlink()
        if mode == 'success_stop_after': execution._path(child, root, 'evidence/0-aider-campaign-stop.json').write_text('{}')
        if mode.startswith('success_') or mode in {'timeout_added_after', 'cancel_partial_stop_after'}:
            assert execution.reconcile_batch(manifest, cell['batch_id'], run_root=root, store=store)['status'] == 'blocked'
            with pytest.raises(BenchmarkJobError, match='prior batch lacks verified closure'):
                execution._remaining_budget(manifest, manifest['cells'][1]['batch_id'], run_root=root, store=store)


@pytest.mark.parametrize('reference_unavailable', [False, True])
def test_aider_timeout_survives_closure_and_reference_errors(tmp_path, monkeypatch, reference_unavailable):
    from tests.test_benchmark_worker import spec
    value = spec()
    value.update(suite='aider', profile='pilot-v2', parameters={'instance_ids': ['python/exercises/practice/phone-number']})
    child = execution.bind_campaign_child(bind_submission_spec(value), manifest_sha256='a' * 64,
        campaign_id='campaign-run', cell_id='cell', arm_id='arm', batch_id='batch')
    store = BenchmarkJobStore(str(tmp_path / 'jobs.db'), run_root=str(tmp_path / 'runs')); store.submit(child)
    context = {'spec': child, 'payload': {'assets': {}}, 'receipt': {'status': 'active'}}
    monkeypatch.setattr(execution, 'enter_campaign', lambda *a, **k: context)
    monkeypatch.setattr(execution, 'verify_child', lambda *a, **k: ({}, {'plan': {}}))
    monkeypatch.setattr(execution, 'require_campaign_suite_window', lambda *a, **k: None)
    monkeypatch.setattr(execution, 'close_campaign', lambda *a, **k: (_ for _ in ()).throw(OSError('receipt unavailable')))
    monkeypatch.setattr(worker, 'campaign_result_reference', lambda *a, **k: (_ for _ in ()).throw(OSError('unavailable')) if reference_unavailable else {})
    monkeypatch.setattr(worker, 'run_benchmark_preflight', lambda *a, **k:
        {'schema': 'anvil-serving.benchmark-preflight/v1', 'passed': True, 'observed': {}, 'checks': []})
    monkeypatch.setattr(worker, '_retain_external_artifacts', lambda *a, **k: {'state': 'failed'})
    monkeypatch.setattr(aider, 'run_aider_benchmark', lambda *a, **k: (_ for _ in ()).throw(TimeoutError('original suite timeout')))
    result = worker.execute_benchmark_job(store, child['run_id'])
    assert result['state'] == 'failed'
    assert result['failure']['code'] == 'TimeoutError' and result['failure']['message'] == 'original suite timeout'


def test_malformed_container_record_does_not_mask_original_aider_timeout(tmp_path, monkeypatch):
    from tests.test_benchmark_worker import spec
    value = spec()
    value.update(suite='aider', profile='pilot-v2', parameters={'instance_ids': ['python/exercises/practice/phone-number']})
    child = execution.bind_campaign_child(bind_submission_spec(value), manifest_sha256='a' * 64,
        campaign_id='campaign-run', cell_id='cell', arm_id='arm', batch_id='batch')
    store = BenchmarkJobStore(str(tmp_path / 'jobs.db'), run_root=str(tmp_path / 'runs')); store.submit(child)
    context = {'spec': child, 'payload': {'assets': {}}, 'receipt': {'status': 'active'}}
    monkeypatch.setattr(execution, 'enter_campaign', lambda *a, **k: context)
    monkeypatch.setattr(execution, 'verify_child', lambda *a, **k: ({}, {'plan': {}}))
    monkeypatch.setattr(execution, 'require_campaign_suite_window', lambda *a, **k: None)
    closed = []
    monkeypatch.setattr(execution, 'close_campaign', lambda *a, **k: closed.append(True))
    monkeypatch.setattr(worker, 'campaign_result_reference', lambda *a, **k: {})
    monkeypatch.setattr(worker, 'run_benchmark_preflight', lambda *a, **k:
        {'schema': 'anvil-serving.benchmark-preflight/v1', 'passed': True, 'observed': {}, 'checks': []})
    def run(plan):
        registry = execution._path(child, store.run_root, 'aider/containers'); registry.mkdir(parents=True)
        (registry / ('a' * 32 + '.json')).write_text('[]')
        raise TimeoutError('original suite timeout')
    monkeypatch.setattr(aider, 'run_aider_benchmark', run)
    monkeypatch.setattr(aider_containment, 'cleanup_aider_containers', lambda **kwargs:
        {'schema': 'anvil-serving.aider-container-cleanup/v1', 'ownership_id': child['ownership_id'],
         'run_id': child['run_id'], 'status': 'completed', 'removed': [], 'errors': []})
    result = worker.execute_benchmark_job(store, child['run_id'])
    assert result['state'] == 'failed'
    assert result['failure']['code'] == 'TimeoutError' and result['failure']['message'] == 'original suite timeout'
    assert not closed


@pytest.mark.parametrize('malformed', [[], None, 3, 'bad', {}, {'schema': 'anvil-serving.aider-container/v1'}])
def test_malformed_aider_registry_is_typed_and_fails_closed_for_all_readers(tmp_path, malformed):
    from tests.test_aider_containment import setup
    run, _, args = setup(tmp_path)
    registry = run / 'aider/containers'; registry.mkdir(parents=True)
    path = registry / ('a' * 32 + '.json'); path.write_text(json.dumps(malformed))
    def no_runtime(*args, **kwargs): pytest.fail('malformed record reached container runtime')
    with pytest.raises(BenchmarkJobError) as raised:
        aider_containment.aider_custody_snapshot(run_path=str(run), ownership_id='owner', run_id='run')
    assert raised.value.code == 'aider_containment_invalid'
    receipt = aider_containment.cleanup_aider_containers(run_path=run, ownership_id='owner', run_id='run', runner=no_runtime)
    assert receipt['status'] == 'incomplete' and receipt['errors']
    with pytest.raises(BenchmarkJobError) as raised:
        aider_containment.run_contained_test('true', runner=no_runtime, **args)
    assert raised.value.code == 'aider_containment_invalid'


@pytest.mark.parametrize('secondary_stage,secondary_type', [
    ('retention', AttributeError), ('retention', TypeError), ('retention', KeyError),
    ('proof', AttributeError), ('proof', TypeError), ('proof', KeyError),
    ('reference', AttributeError), ('reference', TypeError), ('reference', KeyError)])
def test_aider_timeout_preserves_original_and_records_secondary_errors(tmp_path, monkeypatch, secondary_stage, secondary_type):
    from tests.test_benchmark_worker import spec
    value = spec(); value.update(suite='aider', profile='pilot-v2', parameters={'instance_ids': ['python/exercises/practice/phone-number']})
    child = execution.bind_campaign_child(bind_submission_spec(value), manifest_sha256='a' * 64,
        campaign_id='campaign-run', cell_id='cell', arm_id='arm', batch_id='batch')
    store = BenchmarkJobStore(str(tmp_path / 'jobs.db'), run_root=str(tmp_path / 'runs')); store.submit(child)
    context = {'spec': child, 'payload': {'assets': {}}, 'receipt': {'status': 'active'}}
    monkeypatch.setattr(execution, 'enter_campaign', lambda *a, **k: context)
    monkeypatch.setattr(execution, 'verify_child', lambda *a, **k: ({}, {'plan': {}}))
    monkeypatch.setattr(execution, 'require_campaign_suite_window', lambda *a, **k: None)
    closed = []
    monkeypatch.setattr(execution, 'close_campaign', lambda *a, **k: closed.append(k))
    monkeypatch.setattr(worker, 'run_benchmark_preflight', lambda *a, **k:
        {'schema': 'anvil-serving.benchmark-preflight/v1', 'passed': True, 'observed': {}, 'checks': []})
    monkeypatch.setattr(aider, 'run_aider_benchmark', lambda *a, **k: (_ for _ in ()).throw(TimeoutError('original suite timeout')))
    def fail(*args, **kwargs): raise secondary_type('untrusted details must not persist')
    monkeypatch.setattr(worker, '_retain_external_artifacts', fail if secondary_stage == 'retention' else lambda *a, **k: {'state': 'retained'})
    monkeypatch.setattr(worker, '_aider_retention_complete', lambda *a, **k: secondary_stage == 'proof')
    if secondary_stage == 'proof': monkeypatch.setattr(worker, '_aider_campaign_stop_reference', fail)
    monkeypatch.setattr(worker, 'campaign_result_reference', fail if secondary_stage == 'reference' else lambda *a, **k: {})
    result = worker.execute_benchmark_job(store, child['run_id'])
    assert result['state'] == 'failed' and result['failure']['code'] == 'TimeoutError'
    assert result['failure']['message'] == 'original suite timeout'
    secondaries = store.artifact(child['run_id'])['results']['secondary_failures']
    expected = {'retention': 'retention', 'proof': 'campaign_stop_proof_or_close', 'reference': 'campaign_result_reference'}[secondary_stage]
    assert {'stage': expected, 'type': secondary_type.__name__} in secondaries
    assert 'untrusted details' not in json.dumps(secondaries)
    assert not any(item.get('clients_stopped') is not False for item in closed)
