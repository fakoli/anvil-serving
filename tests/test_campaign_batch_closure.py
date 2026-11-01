"""Offline mixed-duration barriers, custody and immutable cancellation evidence."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from anvil_serving.benchmarking import campaign_execution as execution, worker
from anvil_serving.benchmarking.artifacts import atomic_write_json
from tests.test_campaign_telemetry import setup


def active(store, specs, release):
    contexts = []
    manifest = execution._read(execution._path({'ownership_id': specs[0]['ownership_id'],
        'run_id': specs[0]['campaign']['campaign_id']}, store.run_root, 'campaign/manifest.json'))
    for spec in specs:
        path = execution._path(spec, store.run_root, 'campaign-execution.json')
        value = execution._read(path)
        value.update(status='active', activation=release['activation'],
                     started_monotonic=release['released_monotonic'], clients_stopped=False)
        atomic_write_json(path, value)
        contexts.append(dict(store=store, spec=spec, path=path, receipt=value, manifest=manifest, release=release))
    return contexts


def stop(context):
    return execution.close_campaign(context, clients_stopped=True, wait=False)


def finalize(store, spec, *, failed=False):
    return worker._finish_job(store, spec, 'failed' if failed else 'completed',
        results={'deterministic_task_outcome': not failed},
        failure={'class': 'model_behavior', 'code': 'fixture_negative', 'message': 'negative oracle'} if failed else None)


@pytest.mark.parametrize('unverified', [False, True])
def test_concurrent_cancel_stops_every_sibling_before_drain_and_anchors_once(tmp_path, monkeypatch, unverified):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch, count=4, mixed=True)
    contexts = active(store, specs, release)
    stopped, drains = [], []
    entered, resume = threading.Event(), threading.Event()
    def terminate(path, run_id, **kwargs):
        stopped.append(run_id)
        if len(stopped) == 1:
            entered.set()
            assert resume.wait(3)
        return {'stopped': not (unverified and run_id == specs[-1]['run_id']), 'status': 'fixture'}
    monkeypatch.setattr(worker, '_stop_owned_worker', terminate)
    monkeypatch.setattr(worker, '_retain_external_artifacts', lambda *a, **k: {'state': 'incomplete', 'files': []})
    monkeypatch.setattr(execution, 'verify_child', lambda *a, **k: (manifest, {}))
    def drain(*a, **k):
        assert len(stopped) == 4
        drains.append(True)
        clock[0] += 35
        return {'status': 'verified'}
    monkeypatch.setattr(execution, 'observe_drain', drain)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(worker.cancel_campaign_jobs, manifest, store)
        assert entered.wait(3)
        concurrent = pool.submit(worker.cancel_campaign_jobs, manifest, store).result(3)
        assert concurrent['phase'] == 'in_progress'
        resume.set()
        result = owner.result(5)
    assert len(stopped) == 4 and len(drains) == (0 if unverified else 1)
    assert result['phase'] == ('stopping_unverified' if unverified else 'completed')
    if unverified:
        assert store.status(specs[-1]['run_id'])['state'] == 'cancelling'
        assert store.artifact(specs[-1]['run_id']) is None
    paths = [c['path'] for c in contexts if store.status(c['spec']['run_id'])['state'] == 'cancelled']
    paths += list((execution._path(specs[0], store.run_root, 'campaign-execution.json').parents[1]).glob('*/evidence/cancellation-*.json'))
    originals = {p: p.read_bytes() for p in paths}
    if not unverified:
        worker.cancel_campaign_jobs(manifest, store)
        assert len(stopped) == 4
    for c in contexts:
        if store.status(c['spec']['run_id'])['state'] == 'cancelled':
            execution.close_campaign(c, clients_stopped=False)
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    for cell in manifest['cells'][:-1] if unverified else manifest['cells']:
        assert execution._anchored_closure(store, cell, manifest, store.run_root)


@pytest.mark.parametrize('damage', [None, 'invalid_terminal'])
def test_closure_write_loss_after_immutable_terminal_retries_blocked_without_redrain(tmp_path, monkeypatch, damage):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch)
    context = active(store, specs, release)[0]
    stop(context)
    drains = []
    monkeypatch.setattr(execution, 'observe_drain', lambda *a, **k: drains.append(True) or {'status': 'verified'})
    original_write = execution.atomic_write_json
    def fail_closure(path, value):
        if path.parent.name == 'closures':
            raise OSError('lost closure publication after immutable terminal')
        return original_write(path, value)
    monkeypatch.setattr(execution, 'atomic_write_json', fail_closure)
    with pytest.raises(OSError, match='lost closure publication'):
        execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler)
    terminal_path = sampler.root / sampler.relative / 'terminal.json'
    closure_path = sampler.root / 'closures' / (sampler.batch_id + '.json')
    assert terminal_path.exists() and not closure_path.exists()
    if damage:
        terminal_path.write_text('{}')
    original = terminal_path.read_bytes()
    monkeypatch.setattr(execution, 'atomic_write_json', original_write)
    clock[0] += 1
    # A fresh owner has no in-memory sampler or drain proof. It cannot redrain
    # and attach the new result to the older immutable terminal's interval.
    recovered = execution.coordinate_batch(manifest, sampler.batch_id, store=store, force=True)
    assert recovered['status'] == 'blocked'
    assert recovered['drain']['reason'] == 'finalized_telemetry_without_batch_closure'
    assert 'closed_monotonic' not in recovered
    assert ('telemetry_terminal' in recovered) == (damage is None)
    assert len(drains) == 1 and terminal_path.read_bytes() == original
    assert execution.batch_closure(manifest, sampler.batch_id, release, run_root=store.run_root)[0] == recovered
    result = finalize(store, specs[0], failed=True)
    assert result['state'] == 'failed' and result['failure']['code'] == 'fixture_negative'
    assert execution._anchored_closure(store, manifest['cells'][0], manifest, store.run_root)
    assert execution.reconcile_batch(manifest, sampler.batch_id, run_root=store.run_root, store=store)['active_seconds'] is None
    assert execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler) == recovered
    assert len(drains) == 1 and terminal_path.read_bytes() == original
