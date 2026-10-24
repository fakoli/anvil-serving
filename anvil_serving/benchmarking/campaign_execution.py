"""Frozen campaign execution, bounded release and fail-closed drain receipts."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import stat
import time
from urllib.parse import urlsplit
import subprocess
import sys

from .artifacts import atomic_write_json, evidence_file_reference
from .jobs import BenchmarkJobError, canonical_json_bytes, job_spec_sha256, resolve_owned_run_path, utc_now, validate_job_spec


def _digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _manifest_digest(value):
    return _digest({k: v for k, v in value.items() if k != 'manifest_sha256'})


def _read(path):
    with open(path, 'rb', opener=lambda p, f: os.open(p, f | os.O_NONBLOCK | os.O_NOFOLLOW)) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise BenchmarkJobError('campaign_evidence_invalid', 'campaign evidence must be regular files')
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise BenchmarkJobError('campaign_evidence_invalid', 'campaign evidence exceeds size limit')
    return json.loads(raw)


def _path(spec, root, relative):
    return Path(resolve_owned_run_path(root, ownership_id=spec['ownership_id'], run_id=spec['run_id'], relative=relative))


def _directory(manifest, root):
    return _path({'ownership_id': manifest['ownership_id'], 'run_id': manifest['campaign_id']}, root, 'campaign')


def _boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def bind_campaign_child(spec, *, manifest_sha256, campaign_id, cell_id, arm_id, batch_id):
    return validate_job_spec({**spec, 'campaign': dict(campaign_id=campaign_id, cell_id=cell_id,
        arm_id=arm_id, batch_id=batch_id, manifest_sha256=manifest_sha256)})


def _source_hashes():
    # Pin the actual oracle/request/runner code, including imported native fixtures.
    directory = Path(__file__).parent
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.glob('*.py'))}


def prepare_child(spec, *, run_root, cache_root=None, offline=False):
    from .harnesses import prepare_harness_assets
    from .profiles import load_profile
    from .submission import validate_submission_spec
    profile = load_profile(spec['profile'])
    admission = validate_submission_spec(spec, profile=profile, require_profile_binding=True)
    cache_root = cache_root or os.environ.get('ANVIL_BENCHMARK_CACHE_ROOT', os.path.expanduser('~/.anvil-serving/benchmark-harness-cache'))
    assets = prepare_harness_assets(profile, suite=spec['suite'], run_root=run_root,
        ownership_id=spec['ownership_id'], run_id=spec['run_id'], cache_root=cache_root, offline=offline)
    common = dict(endpoint=spec['endpoint'], run_root=run_root, cache_root=cache_root,
                  ownership_id=spec['ownership_id'], run_id=spec['run_id'], timeout_s=spec['timeout_s'])
    params = spec.get('parameters', {})
    if spec['suite'] == 'aider':
        from .aider import build_aider_run_plan
        plan = build_aider_run_plan(profile, assets, **common, instance_ids=params.get('instance_ids'),
            request_controls={k: params[k] for k in ('thinking_mode', 'reasoning_effort', 'temperature', 'top_p') if k in params})
    elif spec['suite'] == 'performance':
        from .performance import build_performance_run_plan
        from .submission import _SUITE_PARAMETERS
        plan = build_performance_run_plan(profile, assets, **common,
            parameters={k: v for k, v in params.items() if k in _SUITE_PARAMETERS['performance']})
    elif spec['suite'] in {'context', 'agentic'}:
        plan = {'schema': 'anvil-serving.native-campaign-plan/v1', 'profile': profile, 'admission': admission}
    else:
        raise BenchmarkJobError('unsupported_campaign_suite', 'campaign supports native, Aider and performance children')
    payload = {'plan': plan, 'assets': assets, 'source_hashes': _source_hashes()}
    path = _path(spec, run_root, 'campaign-prepared/plan.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(path), payload)
    return {'path': 'campaign-prepared/plan.json', 'sha256': _digest(payload),
            'plan_sha256': _digest(plan), 'source_sha256': _digest(payload['source_hashes']),
            'matching': {'workload_sha256': _digest(plan.get('selection', admission)),
                         'oracle_sha256': _digest(payload['source_hashes'])}}


def verify_prepared_child(spec, prepared, *, run_root):
    if set(prepared) != {'path', 'sha256', 'plan_sha256', 'source_sha256', 'matching'} or prepared['path'] != 'campaign-prepared/plan.json':
        raise BenchmarkJobError('campaign_prepared_mismatch', 'prepared child binding is invalid')
    payload = _read(_path(spec, run_root, prepared['path']))
    if (_digest(payload) != prepared['sha256'] or _digest(payload['plan']) != prepared['plan_sha256']
            or _digest(_source_hashes()) != prepared['source_sha256'] or payload['source_hashes'] != _source_hashes()):
        raise BenchmarkJobError('campaign_prepared_mismatch', 'prepared input, oracle, or runner bytes changed')
    plan = payload['plan']
    if spec['suite'] == 'aider':
        from .aider import verify_plan
        verify_plan(plan)
    elif spec['suite'] == 'performance':
        from .performance import verify_plan
        verify_plan(plan)
    else:
        from .profiles import load_profile
        from .submission import validate_submission_spec
        profile = load_profile(spec['profile'])
        if plan != {'schema': 'anvil-serving.native-campaign-plan/v1', 'profile': profile,
                    'admission': validate_submission_spec(spec, profile=profile, require_profile_binding=True)}:
            raise BenchmarkJobError('campaign_prepared_mismatch', 'native prepared profile or selection changed')
    return payload


def verify_child(spec, *, run_root):
    binding = spec['campaign']
    manifest_path = _path({'ownership_id': spec['ownership_id'], 'run_id': binding['campaign_id']}, run_root, 'campaign/manifest.json')
    manifest = _read(manifest_path)
    from .campaigns import source_sha256
    if manifest.get('source_sha256') != source_sha256():
        raise BenchmarkJobError('campaign_source_mismatch', 'frozen executable source changed')
    if _manifest_digest(manifest) != binding['manifest_sha256']:
        raise BenchmarkJobError('campaign_manifest_mismatch', 'frozen campaign manifest changed')
    matches = [cell for cell in manifest['cells'] if cell['cell_id'] == binding['cell_id']]
    base = {k: v for k, v in spec.items() if k != 'campaign'}
    if (len(matches) != 1 or matches[0]['spec'] != base or matches[0]['spec_sha256'] != job_spec_sha256(base)
            or any(matches[0][key] != binding[key] for key in ('arm_id', 'batch_id'))):
        raise BenchmarkJobError('campaign_child_mismatch', 'child differs from its frozen campaign cell')
    return manifest, verify_prepared_child(base, matches[0]['prepared'], run_root=run_root)


def _cells(manifest, batch_id):
    cells = [c for c in manifest['cells'] if c['batch_id'] == batch_id]
    if not cells or len(cells) > 4 or len({c['arm_id'] for c in cells}) != 1:
        raise BenchmarkJobError('campaign_batch_invalid', 'batch must bind one through four children on one arm')
    return cells


def _receipt(cell, manifest, root):
    value = _read(_path(cell['spec'], root, 'campaign-execution.json'))
    expected = bind_campaign_child(cell['spec'], manifest_sha256=_manifest_digest(manifest),
        campaign_id=manifest['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
    if value.get('campaign') != expected['campaign'] or value.get('spec_sha256') != job_spec_sha256(expected):
        raise BenchmarkJobError('campaign_receipt_mismatch', 'child receipt binding differs from manifest')
    return value


def batch_ready(manifest, batch_id, *, run_root):
    try:
        return all(_receipt(c, manifest, run_root)['status'] == 'ready' for c in _cells(manifest, batch_id))
    except FileNotFoundError:
        return False


def reconcile_batch(manifest, batch_id, *, run_root, store=None):
    cells = _cells(manifest, batch_id)
    path = _directory(manifest, run_root) / 'batches' / (batch_id + '.json')
    result = {'batch_id': batch_id, 'arm_id': cells[0]['arm_id'], 'active_seconds': None, 'reserved_seconds': 0}
    if not path.exists():
        return {**result, 'status': 'ready' if batch_ready(manifest, batch_id, run_root=run_root) else 'pending'}
    release = _read(path)
    _validate_release(release, manifest, batch_id)
    result['reserved_seconds'] = release['allowance_s']
    rows = []
    try:
        rows = [_receipt(c, manifest, run_root) for c in cells]
    except FileNotFoundError:
        return {**result, 'status': 'blocked'}
    closed = all(r['status'] == 'closed' and r.get('clients_stopped') is True
        and r.get('drain', {}).get('status') == 'verified' and r.get('boot_id') == release['boot_id']
        and _finite(r.get('closed_monotonic')) and _finite(r.get('started_monotonic'))
        and release['released_monotonic'] <= r['started_monotonic'] <= r['closed_monotonic'] <= release['deadline_monotonic'] for r in rows)
    if closed:
        closed = store is not None and all(_anchored_closure(store, c, manifest, run_root) for c in cells)
    if closed:
        return {**result, 'status': 'closed', 'active_seconds': max(r['closed_monotonic'] for r in rows) - release['released_monotonic']}
    active = release['boot_id'] == _boot_id() and time.monotonic() < release['deadline_monotonic'] and all(r['status'] in {'active', 'ready', 'closed'} for r in rows)
    return {**result, 'status': 'active' if active else 'blocked'}


def _validate_release(release, manifest, batch_id):
    cells = _cells(manifest, batch_id)
    arm = next(a for a in manifest['arms'] if a['arm_id'] == cells[0]['arm_id'])
    numbers = ('released_monotonic', 'traffic_deadline_monotonic', 'deadline_monotonic', 'allowance_s', 'drain_reserve_s')
    if (release.get('manifest_sha256') != _manifest_digest(manifest)
            or release.get('schema') != 'anvil-serving.campaign-release/v1'
            or release.get('batch_id') != batch_id or release.get('arm_id') != cells[0]['arm_id']
            or release.get('run_ids') != [c['spec']['run_id'] for c in cells]
            or any(not _finite(release.get(key)) for key in numbers)
            or not 10 <= release['drain_reserve_s'] < release['allowance_s'] <= min(3600, manifest['budgets']['per_arm_s'], arm['budget_s'])
            or release['drain_reserve_s'] != manifest['budgets']['drain_reserve_s']
            or not math.isclose(release['deadline_monotonic'] - release['released_monotonic'], release['allowance_s'], rel_tol=0, abs_tol=.000001)
            or not math.isclose(release['deadline_monotonic'] - release['traffic_deadline_monotonic'], release['drain_reserve_s'], rel_tol=0, abs_tol=.000001)):
        raise BenchmarkJobError('campaign_release_invalid', 'release binding, allowance or deadlines are invalid')


def _anchored_closure(store, cell, manifest, root):
    """Mutable execution receipts alone never release a reservation."""
    try:
        expected = bind_campaign_child(cell['spec'], manifest_sha256=_manifest_digest(manifest),
            campaign_id=manifest['campaign_id'], cell_id=cell['cell_id'], arm_id=cell['arm_id'], batch_id=cell['batch_id'])
        record = store.status(expected['run_id'])
        if record['spec'] != expected or record['state'] not in {'completed', 'failed', 'cancelled'}:
            return False
        artifact = store.artifact(expected['run_id'])
        root_path = _path(expected, root, 'campaign-execution.json').parent
        return artifact['results'].get('campaign_execution') == evidence_file_reference(str(root_path / 'campaign-execution.json'), root=str(root_path))
    except (BenchmarkJobError, OSError, KeyError, TypeError):
        return False


def campaign_result_reference(store, spec):
    if 'campaign' not in spec:
        return {}
    path = _path(spec, store.run_root, 'campaign-execution.json')
    if not path.exists():
        return {}
    return {'campaign_execution': evidence_file_reference(str(path), root=str(path.parent))}


def _finite(value):
    return type(value) in {float, int} and math.isfinite(value)


def _remaining_budget(manifest, batch_id, *, run_root, store):
    budgets = manifest['budgets']
    used = total = 0.0
    for previous in dict.fromkeys(c['batch_id'] for c in manifest['cells']):
        if previous == batch_id:
            continue
        status = reconcile_batch(manifest, previous, run_root=run_root, store=store)
        if status['reserved_seconds']:
            if status['status'] != 'closed':
                raise BenchmarkJobError('campaign_closure_unknown', 'prior batch lacks verified closure')
            total += status['active_seconds']
            if status['arm_id'] == _cells(manifest, batch_id)[0]['arm_id']:
                used += status['active_seconds']
    arm = next(a for a in manifest['arms'] if a['arm_id'] == _cells(manifest, batch_id)[0]['arm_id'])
    return min(min(3600, budgets['per_arm_s'], arm['budget_s']) - used, min(7200, budgets['total_s']) - total)


def release_batch(manifest, batch_id, *, run_root, allowance_s, store=None):
    """Caller holds campaign scheduling lock; every earlier batch must be closed."""
    cells = _cells(manifest, batch_id)
    directory = _directory(manifest, run_root)
    path = directory / 'batches' / (batch_id + '.json')
    if path.exists():
        return _read(path)
    if not batch_ready(manifest, batch_id, run_root=run_root):
        raise BenchmarkJobError('batch_not_ready', 'all frozen children must be ready before release')
    budgets = manifest['budgets']
    reserve = budgets['drain_reserve_s']
    if not _finite(allowance_s) or not _finite(reserve) or not 10 <= reserve < allowance_s:
        raise BenchmarkJobError('campaign_budget_exceeded', 'allowance must include at least ten seconds of drain reserve')
    allowance_s = min(allowance_s, _remaining_budget(manifest, batch_id, run_root=run_root, store=store))
    if allowance_s <= reserve:
        raise BenchmarkJobError('campaign_budget_exceeded', 'active campaign budget exhausted')
    now = time.monotonic()
    value = {'schema': 'anvil-serving.campaign-release/v1', 'manifest_sha256': _manifest_digest(manifest),
        'batch_id': batch_id, 'arm_id': cells[0]['arm_id'], 'run_ids': [c['spec']['run_id'] for c in cells],
        'boot_id': _boot_id(), 'released_at': utc_now(), 'released_monotonic': now,
        'allowance_s': allowance_s, 'drain_reserve_s': reserve,
        'traffic_deadline_monotonic': now + allowance_s - reserve, 'deadline_monotonic': now + allowance_s}
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(path), value)
    return value


def _clients_stopped():
    """Own synchronous caller has returned; exclude this worker, inspect its group."""
    pid = os.getpid()
    if os.getpgrp() != pid:
        return False
    try:
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = path.read_text().rsplit(')', 1)[1].split()
                if int(path.parent.name) != pid and int(fields[2]) == pid and fields[0] != 'Z':
                    return False
            except FileNotFoundError:
                continue
        return True
    except (OSError, ValueError, IndexError):
        return False



def validate_drain_policy(policy, *, reserve_s=None):
    if policy == {}:
        return policy
    keys = {'metrics_url', 'running_metric', 'waiting_metric', 'timeout_s', 'poll_s',
            'auth_env', 'metric_labels', 'identity_contract', 'generation'}
    if not isinstance(policy, dict) or set(policy) - keys:
        raise BenchmarkJobError('bad_campaign_drain', 'invalid drain collector fields')
    labels = policy.get('metric_labels')
    timeout, poll = policy.get('timeout_s'), policy.get('poll_s')
    if not isinstance(policy.get('metrics_url'), str):
        raise BenchmarkJobError('bad_campaign_drain', 'drain metrics URL must be a string')
    try:
        url = urlsplit(policy['metrics_url'])
        url.port
    except ValueError as exc:
        raise BenchmarkJobError('bad_campaign_drain', 'invalid drain metrics URL') from exc
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.hostname == 'localhost'
            or not isinstance(labels, dict) or not labels or len(labels) > 32
            or any(not isinstance(k, str) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', k)
                   or not isinstance(v, str) or not v or len(v) > 512 for k, v in labels.items())
            or not (labels.get('model') or labels.get('model_name'))
            or any(not isinstance(policy.get(k), str) or not re.fullmatch(r'[a-zA-Z_:][a-zA-Z0-9_:]*', policy[k])
                   for k in ('running_metric', 'waiting_metric'))
            or not _finite(timeout) or not 0 < timeout <= 3600
            or not _finite(poll) or not .001 <= poll <= 10
            or ('auth_env' in policy and (not isinstance(policy['auth_env'], str)
                or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', policy['auth_env'])))):
        raise BenchmarkJobError('bad_campaign_drain', 'drain requires bounded gauges, model labels, and an explicit endpoint')
    contract = policy.get('identity_contract')
    if contract == 'generation-bound-observer/v1':
        generation = policy.get('generation')
        if not isinstance(generation, str) or not re.fullmatch('[0-9a-f]{64}', generation) or url.path != '/engine/' + generation + '/metrics':
            raise BenchmarkJobError('bad_campaign_drain', 'observer drain requires its exact generation-bound URL')
        minimum = 35 + 2 * poll + 2  # expire cached sample, two bounded one-second reads
    elif contract == 'generation-label/v1' and labels.get('generation'):
        minimum = 2 * poll
    else:
        raise BenchmarkJobError('bad_campaign_drain', 'drain requires an explicit generation identity contract')
    if timeout <= minimum or reserve_s is not None and reserve_s < timeout + 6:
        raise BenchmarkJobError('bad_campaign_drain', 'drain timeout and reserve must include freshness, samples and six-second client termination')
    return policy


def observe_drain(policy, *, deadline):
    """Require two independent bounded engine samples with all selected gauges zero."""
    try:
        validate_drain_policy(policy)
    except BenchmarkJobError:
        return {'status': 'unknown', 'reason': 'engine_drain_collector_unavailable'}
    if not policy:
        return {'status': 'unknown', 'reason': 'engine_drain_collector_unavailable'}
    samples = []
    stopped_at = time.monotonic()
    deadline = min(deadline, stopped_at + policy['timeout_s'])
    freshness_wait = 35 if policy['identity_contract'] == 'generation-bound-observer/v1' else 0
    if deadline - stopped_at <= freshness_wait + policy['poll_s']:
        return {'status': 'unknown', 'reason': 'insufficient_fresh_drain_window'}
    if freshness_wait:
        time.sleep(freshness_wait)
    while time.monotonic() < deadline:
        try:
            from ..router.model_capacity import _default_metrics_read, _label_value
            headers = {}
            if policy.get('auth_env'):
                token = os.environ.get(policy['auth_env'])
                if not token:
                    raise ValueError('metrics authorization unavailable')
                headers['Authorization'] = 'Bearer ' + token
            status, raw = _default_metrics_read(policy['metrics_url'], headers,
                timeout=min(1, max(.01, deadline-time.monotonic())), max_bytes=1024 * 1024)
            if status != 200 or len(raw) > 1024 * 1024:
                raise ValueError('oversized metrics')
            values = {}
            for key in ('running_metric', 'waiting_metric'):
                pattern = re.compile(r'^' + re.escape(policy[key]) + r'(?:\{(?P<labels>[^\n]*)\})?\s+(?P<value>[^\s]+)(?:\s+[^\s]+)?$', re.M)
                found = [float(m.group('value')) for m in pattern.finditer(raw.decode())
                         if all(_label_value(m.group('labels') or '', label) == expected
                                for label, expected in policy['metric_labels'].items())]
                if not found or any(not math.isfinite(v) or v < 0 for v in found):
                    raise ValueError('missing or invalid engine gauge')
                values[key] = sum(found)
            samples.append({'observed_monotonic': time.monotonic(), **values})
            if len(samples) >= 2 and all(s['running_metric'] == s['waiting_metric'] == 0 for s in samples[-2:]):
                return {'status': 'verified', 'samples': samples[-2:], 'identity_contract': policy['identity_contract'],
                        'collector_sha256': _digest(policy), 'freshness_wait_s': freshness_wait}
        except (OSError, ValueError):
            samples = []
        time.sleep(min(policy.get('poll_s', .1), max(0, deadline-time.monotonic())))
    return {'status': 'unknown', 'reason': 'engine_drain_not_verified', 'samples': samples[-2:]}


def enter_campaign(store, spec):
    """Prepare then wait for a finite shared release before any traffic-capable stage."""
    if sys.platform != 'linux':
        raise BenchmarkJobError('unsupported_campaign_platform', 'campaign execution requires verified Linux worker custody')
    manifest, payload = verify_child(spec, run_root=store.run_root)
    binding = spec['campaign']
    path = _path(spec, store.run_root, 'campaign-execution.json')
    value = {'schema': 'anvil-serving.campaign-execution/v1', 'campaign': binding,
        'spec_sha256': job_spec_sha256(spec), 'status': 'ready', 'boot_id': _boot_id(),
        'ready_at': utc_now(), 'clients_stopped': False,
        'telemetry_coverage': {k: {'status': 'unavailable'} for k in ('engine', 'gpu', 'host', 'cgroup')}}
    atomic_write_json(str(path), value)
    release_path = _directory(manifest, store.run_root) / 'batches' / (binding['batch_id'] + '.json')
    wait_until = time.monotonic() + 60
    while not release_path.exists():
        if time.monotonic() >= wait_until or store.status(spec['run_id'])['state'] != 'running':
            raise BenchmarkJobError('campaign_release_missing', 'campaign release was not received before the bounded readiness deadline')
        time.sleep(.05)
    release = _read(release_path)
    _validate_release(release, manifest, binding['batch_id'])
    from .campaigns import campaign_lock
    with campaign_lock(_directory(manifest, store.run_root)):
        state_path = _directory(manifest, store.run_root) / 'state.json'
        if state_path.exists() and _read(state_path).get('cancel_requested') is True:
            raise BenchmarkJobError('campaign_cancelled', 'campaign cancellation was requested before traffic')
        if release['allowance_s'] > _remaining_budget(manifest, binding['batch_id'], run_root=store.run_root, store=store):
            raise BenchmarkJobError('campaign_budget_exceeded', 'release exceeds remaining verified campaign budget')
    if (release.get('manifest_sha256') != binding['manifest_sha256'] or release.get('batch_id') != binding['batch_id']
            or release.get('boot_id') != _boot_id() or spec['run_id'] not in release.get('run_ids', [])
            or not _finite(release.get('traffic_deadline_monotonic'))
            or not _finite(release.get('deadline_monotonic')) or not _finite(release.get('released_monotonic'))
            or not 0 < release['deadline_monotonic'] - release['released_monotonic'] <= 3600
            or not release['released_monotonic'] <= time.monotonic() < release['traffic_deadline_monotonic'] < release['deadline_monotonic']):
        raise BenchmarkJobError('campaign_release_invalid', 'release is expired or does not bind this worker')
    # Recheck after the barrier: setup-time verification alone leaves a drift window.
    verify_child(spec, run_root=store.run_root)
    value.update(status='active', started_at=utc_now(), started_monotonic=time.monotonic())
    atomic_write_json(str(path), value)
    # A finite supervisor survives both coordinator loss and a worker crash. It uses
    # the existing custody-checked cancellation and partial-retention path.
    supervisor = subprocess.Popen([sys.executable, '-m', 'anvil_serving.benchmarking.campaign_execution',
        '--watchdog', os.path.realpath(store.path), store.run_root, spec['run_id']],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True)
    ready, _, _ = select.select([supervisor.stdout], [], [], min(2, max(0, release['traffic_deadline_monotonic'] - time.monotonic())))
    if not ready or supervisor.stdout.readline(32) != b'ready\n':
        supervisor.kill()
        supervisor.wait(timeout=2)
        supervisor.stdout.close()
        raise BenchmarkJobError('campaign_watchdog_unavailable', 'bounded supervisor did not acknowledge custody')
    supervisor.stdout.close()
    return {'manifest': manifest, 'payload': payload, 'release': release, 'receipt': value,
            'path': path}


def close_campaign(context, *, clients_stopped=None):
    value = context['receipt']
    clients = _clients_stopped() if clients_stopped is None else clients_stopped
    arm = next(a for a in context['manifest']['arms'] if a['arm_id'] == value['campaign']['arm_id'])
    policy = arm.get('drain_policy', context['manifest'].get('drain_policy'))
    drain = observe_drain(policy, deadline=context['release']['deadline_monotonic']) if clients else {'status': 'unknown', 'reason': 'owned_clients_not_stopped'}
    closed = clients and drain['status'] == 'verified' and time.monotonic() <= context['release']['deadline_monotonic']
    value.update(status='closed' if closed else 'blocked', clients_stopped=clients, drain=drain)
    if closed:
        value.update(closed_at=utc_now(), closed_monotonic=time.monotonic())
        value['active_seconds'] = value['closed_monotonic'] - value['started_monotonic']
    atomic_write_json(str(context['path']), value)
    return value


def close_cancelled_campaign(store, spec, *, clients_stopped):
    path = _path(spec, store.run_root, 'campaign-execution.json')
    if not path.exists():
        return None
    manifest, _ = verify_child(spec, run_root=store.run_root)
    release_path = _directory(manifest, store.run_root) / 'batches' / (spec['campaign']['batch_id'] + '.json')
    if not release_path.exists():
        return None
    return close_campaign({'receipt': _read(path), 'path': path, 'manifest': manifest,
        'release': _read(release_path)}, clients_stopped=clients_stopped)


def _watchdog(database, run_root, run_id):
    from ..control_plane.controller.store import BenchmarkJobStore
    from .worker import cancel_benchmark_job
    store = BenchmarkJobStore(database, run_root=run_root)
    record = store.status(run_id)
    spec = record['spec']
    manifest, _ = verify_child(spec, run_root=run_root)
    release = _read(_directory(manifest, run_root) / 'batches' / (spec['campaign']['batch_id'] + '.json'))
    _validate_release(release, manifest, spec['campaign']['batch_id'])
    deadline = release['traffic_deadline_monotonic']
    if release['boot_id'] != _boot_id() or not _finite(deadline):
        return
    cell = next(c for c in manifest['cells'] if c['cell_id'] == spec['campaign']['cell_id'])
    print('ready', flush=True)
    while time.monotonic() < deadline:
        try:
            receipt = _receipt(cell, manifest, run_root)
        except (BenchmarkJobError, OSError, ValueError, KeyError):
            break
        if (receipt['status'] in {'closed', 'blocked'} and receipt.get('clients_stopped') is True
                and _anchored_closure(store, cell, manifest, run_root)):
            return
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    if store.status(run_id)['state'] in {'completed', 'failed', 'cancelled'}:
        from .worker import _stop_owned_worker
        _stop_owned_worker(str(_path(spec, run_root, 'worker.json')), run_id,
                           database=os.path.realpath(store.path), run_root=store.run_root)
    else:
        cancel_benchmark_job(store, run_id)


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--watchdog':
        _watchdog(*sys.argv[2:])
