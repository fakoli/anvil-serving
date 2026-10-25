"""Offline mixed-duration barriers, custody and immutable cancellation evidence."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from anvil_serving.benchmarking import campaign_execution as execution, worker, campaign_report
from anvil_serving.benchmarking.artifacts import atomic_write_json, evidence_file_reference
from anvil_serving.benchmarking.jobs import BenchmarkJobError
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


@pytest.mark.parametrize('count', [1, 4])
def test_fast_native_waits_for_slow_aider_and_one_shared_drain(tmp_path, monkeypatch, count):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch, count=count, mixed=count == 4)
    contexts = active(store, specs, release)
    drains = []
    def drain(*args, **kwargs):
        assert all(execution._read(c['path'])['clients_stopped'] for c in contexts)
        drains.append(kwargs['stopped_wall'])
        clock[0] += 35
        return {'status': 'verified'}
    monkeypatch.setattr(execution, 'observe_drain', drain)
    stop(contexts[0]); clock[0] += 2
    if count == 4:
        stop(contexts[1])
        assert execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler) is None
        assert not drains
        assert all(execution._read(c['path'])['status'] == 'draining' for c in contexts[:2])
        assert [s['suite'] for s in specs] == ['agentic', 'agentic', 'aider', 'aider']
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(finalize, store, specs[0], failed=True)]
        time.sleep(.05)
        assert not futures[0].done()
        for context in contexts[2:]:
            clock[0] += 1
            stop(context)
        shared = execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler)
        assert shared['status'] == 'closed'
        futures += [pool.submit(finalize, store, s) for s in specs[1:]]
        assert futures[0].result(3)['failure']['code'] == 'fixture_negative'
        assert all(f.result(3)['state'] == 'completed' for f in futures[1:])
    terminal = sampler.root / (sampler.relative + 'terminal.json')
    original = terminal.read_bytes()
    assert execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler) == shared
    assert terminal.read_bytes() == original and len(drains) == 1
    receipts = [execution._read(c['path']) for c in contexts]
    assert len({r['batch_closure']['sha256'] for r in receipts}) == 1
    assert len({r['telemetry_terminal']['sha256'] for r in receipts}) == 1
    assert execution.reconcile_batch(manifest, sampler.batch_id, run_root=store.run_root, store=store)['active_seconds'] == shared['finalized_monotonic'] - release['released_monotonic']
    assert all(execution._anchored_closure(store, cell, manifest, store.run_root) for cell in manifest['cells'])


@pytest.mark.parametrize('damage', ['missing', 'malformed', 'list', 'oversized', 'symlink', 'nonregular'])
def test_bad_peer_never_drains_or_reclaims_budget(tmp_path, monkeypatch, damage):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch, count=4)
    contexts = active(store, specs, release)
    for c in contexts: stop(c)
    peer = contexts[-1]['path']
    if damage == 'missing': peer.unlink()
    elif damage == 'malformed': peer.write_text('{')
    elif damage == 'list': peer.write_text('[]')
    elif damage == 'oversized': peer.write_bytes(b' ' * (16 * 1024 * 1024 + 1))
    elif damage == 'symlink':
        saved = peer.with_name('saved.json'); peer.rename(saved); peer.symlink_to(saved)
    else:
        import os
        peer.unlink(); os.mkfifo(peer)
    monkeypatch.setattr(execution, 'observe_drain', lambda *a, **k: pytest.fail('global drain before valid stops'))
    assert execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler) is None
    clock[0] = release['deadline_monotonic']
    shared = execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler)
    assert shared['status'] == 'blocked'
    assert execution.reconcile_batch(manifest, sampler.batch_id, run_root=store.run_root, store=store)['active_seconds'] is None


def test_activation_drift_blocks_all_children(tmp_path, monkeypatch):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch, count=4)
    contexts = active(store, specs, release)
    for c in contexts: stop(c)
    calls = []
    def activation(*a, **k):
        calls.append(True)
        if len(calls) == 2:
            raise BenchmarkJobError('campaign_activation_mismatch', 'changed generation')
        return release['activation']
    monkeypatch.setattr(execution, '_activation', activation)
    shared = execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler)
    assert shared['status'] == 'blocked'
    for spec in specs:
        execution.campaign_result_reference(store, spec)
        assert execution._read(execution._path(spec, store.run_root, 'campaign-execution.json'))['status'] == 'blocked'


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


@pytest.mark.parametrize('terminal_state', ['completed', 'cancelled'])
def test_terminalization_during_wait_never_rewrites_anchored_receipt(tmp_path, monkeypatch, terminal_state):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch)
    context = active(store, specs, release)[0]
    stopped = threading.Event()
    original_commit = execution._commit_receipt
    def commit(*a, **k):
        value = original_commit(*a, **k)
        stopped.set()
        return value
    monkeypatch.setattr(execution, '_commit_receipt', commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(execution.close_campaign, context, clients_stopped=True)
        assert stopped.wait(3)
        with execution.child_lock(store, specs[0]):
            original = context['path'].read_bytes()
            reference = evidence_file_reference(str(context['path']), root=str(context['path'].parent))
            store.transition(specs[0]['run_id'], terminal_state, results={'campaign_execution': reference})
        assert waiting.result(3)['status'] == 'draining'
    assert context['path'].read_bytes() == original
    execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler)
    execution.campaign_result_reference(store, specs[0])
    assert context['path'].read_bytes() == original
    assert execution._anchored_closure(store, manifest['cells'][0], manifest, store.run_root)


@pytest.mark.parametrize('damage', ['missing', 'bytes', 'release', 'terminal'])
def test_shared_closure_transitive_hash_required_by_report_and_reconciliation(tmp_path, monkeypatch, damage):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch)
    context = active(store, specs, release)[0]
    stop(context); clock[0] += 2
    execution.coordinate_batch(manifest, sampler.batch_id, store=store, sampler=sampler)
    finalize(store, specs[0])
    receipt = execution._read(context['path'])
    assert set(campaign_report._coverage(receipt, manifest, manifest['cells'][0], store).values()) == {'measured'}
    closure = sampler.root / receipt['batch_closure']['path']
    if damage == 'missing': closure.unlink()
    elif damage == 'bytes': closure.write_text('{}')
    elif damage == 'release':
        path = sampler.root / 'batches' / (sampler.batch_id + '.json')
        value = execution._read(path); value['released_at'] = 'tampered'; atomic_write_json(path, value)
    else:
        (sampler.root / receipt['telemetry_terminal']['path']).write_text('{}')
    assert execution.reconcile_batch(manifest, sampler.batch_id, run_root=store.run_root, store=store)['active_seconds'] is None
    assert set(campaign_report._coverage(receipt, manifest, manifest['cells'][0], store).values()) == {'unknown'}


def test_watchdog_terminal_with_mismatched_anchor_stops_owned_worker_without_writes(tmp_path, monkeypatch):
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch)
    context = active(store, specs, release)[0]
    store.transition(specs[0]['run_id'], 'failed', failure={'class': 'fixture', 'message': 'failed'}, results={})
    original = context['path'].read_bytes()
    monkeypatch.setattr(execution, 'verify_child', lambda *a, **k: (manifest, {}))
    stopped = []
    execution._watchdog(store.path, store.run_root, specs[0]['run_id'],
        cancel_job=lambda *a: pytest.fail('terminal recancelled'),
        stop_worker=lambda *a, **k: stopped.append(True) or {'stopped': True})
    assert stopped == [True] and context['path'].read_bytes() == original


def test_missing_sampler_terminal_is_explicit_and_does_not_rewait_after_blocked_closure(tmp_path, monkeypatch):
    from anvil_serving.benchmarking import campaign_telemetry as telemetry
    manifest, store, specs, sampler, release, clock = setup(tmp_path, monkeypatch)
    context = active(store, specs, release)[0]
    stop(context)
    monkeypatch.setattr(telemetry.BatchSampler, 'recover', lambda *a, **k: None)
    closure = execution.coordinate_batch(manifest, sampler.batch_id, store=store, force=True)
    assert closure['status'] == 'blocked' and 'telemetry_terminal' not in closure
    monkeypatch.setattr(execution.time, 'sleep', lambda n: pytest.fail('published closure cannot wait for missing sampler'))
    result = finalize(store, specs[0], failed=True)
    receipt = execution._read(context['path'])
    assert result['failure']['code'] == 'fixture_negative'
    assert receipt['status'] == 'blocked'
    assert receipt['telemetry_terminal']['status'] == 'unavailable'
    assert store.artifact(specs[0]['run_id'])['results']['campaign_execution']['sha256']


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
