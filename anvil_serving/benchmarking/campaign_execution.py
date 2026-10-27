"""Frozen campaign execution, bounded release and fail-closed drain receipts."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import sqlite3
import stat
import time
import subprocess
import sys

from .artifacts import atomic_write_json, evidence_file_reference
from .jobs import BenchmarkJobError, job_spec_sha256, utc_now
from .campaign_identity import (
    _digest as _digest,
    _manifest_digest as _manifest_digest,
    _read as _read,
    _path as _path,
    _directory as _directory,
    _boot_id as _boot_id,
    bind_campaign_child as bind_campaign_child,
    _cells as _cells,
    _receipt as _receipt,
)


STATE_SCHEMA = "anvil-serving.benchmark-campaign-state/v1"
MAX_CAMPAIGN_BYTES = 16 * 1024 * 1024
CAMPAIGN_LOCK_TIMEOUT_S = 5.0
# Covers post-release asset checks, watchdog startup (30s) and preflight (10s).
CAMPAIGN_ENTRY_RESERVE_S = 60


def source_sha256():
    """Pin executable product bytes, including adapters and independent oracles."""
    root = Path(__file__).resolve().parents[1]
    return _digest({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(root.rglob("*.py"))})


def read_campaign_spec(path):
    try:
        with open(path, "rb", opener=lambda name, flags: os.open(
                name, flags | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("not a regular file")
            raw = handle.read(MAX_CAMPAIGN_BYTES + 1)
        if len(raw) > MAX_CAMPAIGN_BYTES:
            raise ValueError("oversized")
        return json.loads(raw)
    except (OSError, ValueError, RecursionError) as exc:
        raise BenchmarkJobError("bad_campaign_file", "campaign must be bounded JSON") from exc


@contextmanager
def campaign_lock(directory, *, timeout_s=CAMPAIGN_LOCK_TIMEOUT_S):
    """Take a bounded, regular-file OS lock; process death releases it."""
    directory = Path(directory)
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s < 0:
        raise BenchmarkJobError("campaign_lock_invalid", "campaign lock timeout must be a non-negative finite number")
    directory.mkdir(parents=True, exist_ok=True)
    flags = (os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
             | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        descriptor = os.open(directory / ".lock", flags, 0o600)
    except OSError as exc:
        raise BenchmarkJobError("campaign_lock_invalid", "campaign lock must be a readable regular file") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BenchmarkJobError("campaign_lock_invalid", "campaign lock must be a regular file")
        deadline = time.perf_counter() + timeout_s
        acquired = False
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.write(descriptor, b"0")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                if time.perf_counter() >= deadline:
                    raise BenchmarkJobError("campaign_lock_timeout", "campaign lock acquisition exceeded its bounded deadline") from exc
                time.sleep(min(0.05, max(0, deadline - time.perf_counter())))
        try:
            yield
        finally:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _new_state(manifest):
    return {"schema": STATE_SCHEMA, "manifest_sha256": manifest["manifest_sha256"],
            "cancel_requested": False, "batches": {}}


def _state(directory, *, manifest):
    path = directory / "state.json"
    try:
        state = read_campaign_spec(path)
    except BenchmarkJobError as exc:
        raise BenchmarkJobError("campaign_state_invalid", "campaign state receipt is unreadable or malformed") from exc
    if (not isinstance(state, dict)
            or set(state) != {"schema", "manifest_sha256", "cancel_requested", "batches"}
            or state.get("schema") != STATE_SCHEMA
            or state.get("manifest_sha256") != manifest.get("manifest_sha256")
            or type(state.get("cancel_requested")) is not bool
            or not isinstance(state.get("batches"), dict)):
        raise BenchmarkJobError("campaign_state_invalid", "campaign state receipt is not bound to this frozen manifest")
    return state


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
    selection = admission.get('selection', {}).get('instance_ids') if spec['suite'] == 'aider' else None
    assets = prepare_harness_assets(profile, suite=spec['suite'], run_root=run_root,
        ownership_id=spec['ownership_id'], run_id=spec['run_id'], cache_root=cache_root, offline=offline,
        **({'aider_instance_ids': selection} if selection is not None else {}))
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


def _activation(arm, *, expected=None):
    from .campaign_identity import verify_activation
    observed = verify_activation(arm)
    binding = {key: observed.get(key) for key in ('generation', 'metrics_url', 'container_id', 'started_at', 'served_model')}
    value = {'status': 'verified', **binding, 'binding_sha256': _digest(binding)}
    if observed.get('binding_sha256') != value['binding_sha256'] or expected is not None and value != expected:
        raise BenchmarkJobError('campaign_activation_mismatch', 'active serving identity changed during the campaign')
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
    except (OSError, ValueError, KeyError, TypeError, AttributeError, BenchmarkJobError):
        return {**result, 'status': 'blocked'}
    closed = all(r['status'] == 'closed' and r.get('clients_stopped') is True
        and r.get('drain', {}).get('status') == 'verified' and r.get('boot_id') == release['boot_id']
        and _finite(r.get('closed_monotonic')) and _finite(r.get('started_monotonic'))
        and release['released_monotonic'] <= r['started_monotonic'] <= r['closed_monotonic'] <= release['deadline_monotonic'] for r in rows)
    shared = None
    if closed:
        try:
            shared, reference = batch_closure(manifest, batch_id, release, run_root=run_root,
                                              reference=rows[0].get('batch_closure'))
            closed = (shared['status'] == 'closed' and all(r.get('batch_closure') == reference for r in rows)
                      and store is not None and all(_anchored_closure(store, c, manifest, run_root) for c in cells))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, BenchmarkJobError):
            closed = False
    if closed:
        end = shared['finalized_monotonic']
        if 'telemetry_collection' in manifest:
            try:
                from .campaign_telemetry import verify_terminal
                terminal, _, _ = verify_terminal(manifest, batch_id, release, run_root=run_root,
                    reference=rows[0].get('telemetry_terminal'))
                end = max(end, terminal['finalized_monotonic'])
            except (OSError, ValueError, KeyError, TypeError, AttributeError, BenchmarkJobError):
                # Missing finalization cannot reclaim the remaining reservation.
                end = release['deadline_monotonic']
        return {**result, 'status': 'closed', 'active_seconds': end - release['released_monotonic']}
    active = release['boot_id'] == _boot_id() and time.monotonic() < release['deadline_monotonic'] and all(r['status'] in {'active', 'draining', 'ready', 'closed'} for r in rows)
    return {**result, 'status': 'active' if active else 'blocked'}


def _validate_release(release, manifest, batch_id):
    if not isinstance(release, dict):
        raise BenchmarkJobError('campaign_release_invalid', 'release must be an object')
    cells = _cells(manifest, batch_id)
    arm = next(a for a in manifest['arms'] if a['arm_id'] == cells[0]['arm_id'])
    numbers = ('released_monotonic', 'traffic_deadline_monotonic', 'deadline_monotonic', 'allowance_s', 'drain_reserve_s')
    activation = release.get('activation')
    activation_invalid = (not isinstance(activation, dict) or set(activation) != {'status', 'generation', 'metrics_url', 'container_id', 'started_at', 'served_model', 'binding_sha256'}
            or activation.get('status') != 'verified'
            or activation.get('binding_sha256') != _digest({key: activation.get(key) for key in ('generation', 'metrics_url', 'container_id', 'started_at', 'served_model')}))
    if (activation_invalid or release.get('manifest_sha256') != _manifest_digest(manifest)
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
    except (BenchmarkJobError, OSError, KeyError, TypeError, sqlite3.Error):
        return False



TERMINAL = {'completed', 'failed', 'cancelled'}


def child_lock(store, spec):
    # Shared by receipt writers and the worker's immutable terminal transition.
    return campaign_lock(_path(spec, store.run_root, 'campaign-receipt-lock'))


def _commit_receipt(store, spec, value, *, cancelling=False):
    with child_lock(store, spec):
        record = store.status(spec['run_id'])
        path = _path(spec, store.run_root, 'campaign-execution.json')
        if record['state'] in TERMINAL or record['state'] == 'cancelling' and not cancelling:
            return _read(path)
        atomic_write_json(path, value)
        return value


def _stop_fact(row):
    return {k: row.get(k) for k in ('campaign', 'spec_sha256', 'boot_id', 'activation',
            'started_monotonic', 'clients_stopped', 'clients_stopped_wall')}


def _stopped_rows(manifest, batch_id, release, root):
    # Check the lexical receipt before the owned-path resolver follows links.
    if any((_path(c['spec'], root, None) / 'campaign-execution.json').is_symlink()
           for c in _cells(manifest, batch_id)):
        raise BenchmarkJobError('campaign_evidence_invalid', 'batch stop receipt is a symlink')
    rows = [_receipt(c, manifest, root) for c in _cells(manifest, batch_id)]
    if not all(r.get('clients_stopped') is True and r.get('status') in {'draining', 'closed', 'blocked'}
               and r.get('boot_id') == release['boot_id'] and r.get('activation') == release['activation']
               and _finite(r.get('started_monotonic')) and _finite(r.get('clients_stopped_wall'))
               and release['released_monotonic'] <= r['started_monotonic'] <= release['deadline_monotonic']
               for r in rows):
        return None
    return rows


def batch_closure(manifest, batch_id, release, *, run_root, reference=None):
    """Validate the shared closure and its transitive telemetry commitment."""
    root = _directory(manifest, run_root)
    path = root / 'closures' / (batch_id + '.json')
    value = _read(path)
    ref = evidence_file_reference(str(path), root=str(root))
    if (reference is not None and reference != ref or not isinstance(value, dict)
            or value.get('schema') != 'anvil-serving.campaign-batch-closure/v1'
            or value.get('manifest_sha256') != _manifest_digest(manifest)
            or value.get('release_sha256') != _digest(release) or value.get('batch_id') != batch_id
            or value.get('status') not in {'closed', 'blocked'}):
        raise BenchmarkJobError('campaign_closure_invalid', 'shared batch closure binding differs')
    if value['status'] == 'closed':
        rows = _stopped_rows(manifest, batch_id, release, run_root)
        if (not rows or value.get('stops') != [_stop_fact(r) for r in rows]
                or value.get('drain', {}).get('status') != 'verified'
                or not all(_finite(value.get(k)) for k in ('closed_monotonic', 'finalized_monotonic'))
                or not release['released_monotonic'] <= value['closed_monotonic'] <= value['finalized_monotonic'] <= release['deadline_monotonic']):
            raise BenchmarkJobError('campaign_closure_invalid', 'shared batch stop or deadline proof differs')
        if 'telemetry_collection' in manifest:
            from .campaign_telemetry import verify_terminal
            if not isinstance(value.get('telemetry_terminal'), dict):
                raise BenchmarkJobError('campaign_closure_invalid', 'shared telemetry commitment missing')
            terminal, _, _ = verify_terminal(manifest, batch_id, release, run_root=run_root,
                                              reference=value['telemetry_terminal'])
            if terminal['interval_end_monotonic'] != value['closed_monotonic'] or terminal['finalized_monotonic'] > value['finalized_monotonic']:
                raise BenchmarkJobError('campaign_closure_invalid', 'shared telemetry interval differs')
    return value, ref


def coordinate_batch(manifest, batch_id, *, store, sampler=None, force=False):
    """One owner observes global drain only after every bound local client stops."""
    root = _directory(manifest, store.run_root)
    release_path = root / 'batches' / (batch_id + '.json')
    if not release_path.exists():
        return None
    release = _read(release_path)
    _validate_release(release, manifest, batch_id)
    try:
        with campaign_lock(root / 'closure-locks' / batch_id, timeout_s=0):
            closure_path = root / 'closures' / (batch_id + '.json')
            if closure_path.exists():
                return batch_closure(manifest, batch_id, release, run_root=store.run_root)[0]
            terminal_path = root / 'telemetry' / batch_id / 'terminal.json'
            if 'telemetry_collection' in manifest and (terminal_path.exists() or terminal_path.is_symlink()):
                # Finalization survived but its drain/stop closure did not. The
                # terminal alone cannot reconstruct that proof. Never redrain
                # into its old interval or overwrite its immutable bytes.
                value = {'schema': 'anvil-serving.campaign-batch-closure/v1',
                    'manifest_sha256': _manifest_digest(manifest), 'release_sha256': _digest(release),
                    'batch_id': batch_id, 'status': 'blocked', 'stops': [],
                    'drain': {'status': 'unknown', 'reason': 'finalized_telemetry_without_batch_closure'},
                    'finalized_monotonic': time.monotonic()}
                from .campaign_telemetry import verify_terminal
                try:
                    _, _, value['telemetry_terminal'] = verify_terminal(manifest, batch_id, release, run_root=store.run_root)
                except (OSError, ValueError, TypeError, KeyError, AttributeError, BenchmarkJobError):
                    pass
                closure_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(closure_path, value)
                return value
            rows = None
            try:
                rows = _stopped_rows(manifest, batch_id, release, store.run_root)
            except (OSError, ValueError, TypeError, KeyError, AttributeError, BenchmarkJobError):
                pass
            if sampler is None and 'telemetry_collection' in manifest:
                from .campaign_telemetry import BatchSampler
                sampler = BatchSampler.recover(manifest, batch_id, release, run_root=store.run_root)
            if not rows and not force and release['boot_id'] == _boot_id() and time.monotonic() < release['deadline_monotonic']:
                if sampler is not None:
                    sampler.tick()
                return None
            drain = {'status': 'unknown', 'reason': 'batch_clients_not_stopped'}
            if rows and release['boot_id'] == _boot_id() and time.monotonic() < release['deadline_monotonic']:
                arm = next(a for a in manifest['arms'] if a['arm_id'] == _cells(manifest, batch_id)[0]['arm_id'])
                policy = arm.get('drain_policy', manifest.get('drain_policy'))
                try:
                    activation = _activation(arm, expected=release['activation'])
                    if not policy or next(iter(policy['metric_labels'].values())) != activation['served_model']:
                        raise BenchmarkJobError('bad_campaign_drain', 'batch drain model is unverified')
                    resolved = {**policy, 'metrics_url': activation['metrics_url'], 'generation': activation['generation']}
                    drain = observe_drain(resolved, deadline=release['deadline_monotonic'],
                                          stopped_wall=max(r['clients_stopped_wall'] for r in rows),
                                          tick=sampler.tick if sampler is not None else None)
                    if drain['status'] == 'verified':
                        _activation(arm, expected=release['activation'])
                    current = _stopped_rows(manifest, batch_id, release, store.run_root)
                    if not current or [_stop_fact(r) for r in rows] != [_stop_fact(r) for r in current]:
                        drain = {'status': 'unknown', 'reason': 'batch_stop_facts_changed'}
                except (OSError, ValueError, TypeError, KeyError, AttributeError, BenchmarkJobError):
                    drain = {'status': 'unknown', 'reason': 'campaign_activation_unverified'}
            closed = bool(rows and drain['status'] == 'verified' and time.monotonic() <= release['deadline_monotonic'])
            value = {'schema': 'anvil-serving.campaign-batch-closure/v1',
                'manifest_sha256': _manifest_digest(manifest), 'release_sha256': _digest(release),
                'batch_id': batch_id, 'status': 'closed' if closed else 'blocked',
                'stops': [_stop_fact(r) for r in rows] if rows else [], 'drain': drain,
                'closed_monotonic': time.monotonic(), 'closed_at': utc_now()}
            if sampler is not None:
                sampler.finish(partial=not closed, closure=value)
                from .campaign_telemetry import verify_terminal
                try:
                    _, _, value['telemetry_terminal'] = verify_terminal(manifest, batch_id, release, run_root=store.run_root)
                except (OSError, ValueError, TypeError, KeyError, AttributeError, BenchmarkJobError):
                    value['status'] = 'blocked'
            elif 'telemetry_collection' in manifest:
                value['status'] = 'blocked'
            value['finalized_monotonic'] = time.monotonic()
            if value['finalized_monotonic'] > release['deadline_monotonic']:
                value['status'] = 'blocked'
            closure_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(closure_path, value)
            return value
    except BenchmarkJobError as exc:
        if exc.code != 'campaign_lock_timeout':
            raise
        return None  # Another coordinator/cancellation owner is already draining.


def _await_batch(context, *, cancelling=False):
    store, spec, manifest, release = (context[k] for k in ('store', 'spec', 'manifest', 'release'))
    path = context['path']
    while release['boot_id'] == _boot_id() and time.monotonic() < release['deadline_monotonic']:
        if store.status(spec['run_id'])['state'] in TERMINAL | ({'cancelling'} if not cancelling else set()):
            return _read(path)
        try:
            shared, reference = batch_closure(manifest, spec['campaign']['batch_id'], release, run_root=store.run_root)
            value = _read(path)
            value.update(status=shared['status'], drain=shared['drain'], batch_closure=reference)
            if shared['status'] == 'closed':
                value.update(closed_at=shared['closed_at'], closed_monotonic=shared['closed_monotonic'],
                             active_seconds=shared['finalized_monotonic'] - value['started_monotonic'])
            if 'telemetry_terminal' in shared:
                from .campaign_telemetry import attach_terminal
                value = attach_terminal(store, spec, value, manifest, release)
            elif 'telemetry_collection' in manifest:
                # A published blocked closure is final: no owner will produce a
                # missing telemetry terminal later. Do not wait a second time.
                value['telemetry_terminal'] = {'status': 'unavailable', 'reason': 'coordinator_terminal_unavailable'}
                value['telemetry_coverage'] = {k: {'status': 'unavailable'} for k in ('engine', 'gpu', 'host', 'cgroup')}
            return _commit_receipt(store, spec, value, cancelling=cancelling)
        except FileNotFoundError:
            time.sleep(min(.05, max(0, release['deadline_monotonic'] - time.monotonic())))
        except (OSError, ValueError, KeyError, TypeError, AttributeError, BenchmarkJobError):
            break
    value = _read(path)
    value.update(status='blocked', drain={'status': 'unknown', 'reason': 'batch_closure_unavailable'})
    return _commit_receipt(store, spec, value, cancelling=cancelling)

def campaign_result_reference(store, spec, *, cancelling=False):
    if 'campaign' not in spec:
        return {}
    path = _path(spec, store.run_root, 'campaign-execution.json')
    if not path.exists():
        return {}
    # All success/failure/cancellation artifacts pass this seam. Never rewrite
    # receipts already referenced by an immutable terminal artifact.
    record = store.status(spec['run_id'])
    if record and record['state'] not in {'completed', 'failed', 'cancelled'}:
        manifest_path = _path({'ownership_id': spec['ownership_id'], 'run_id': spec['campaign']['campaign_id']}, store.run_root, 'campaign/manifest.json')
        manifest = _read(manifest_path)
        release_path = _directory(manifest, store.run_root) / 'batches' / (spec['campaign']['batch_id'] + '.json')
        value = _read(path)
        if value.get('status') in {'draining', 'closed'} and 'batch_closure' not in value:
            release = _read(release_path)
            _validate_release(release, manifest, spec['campaign']['batch_id'])
            _await_batch({'store': store, 'spec': spec, 'manifest': manifest, 'release': release, 'path': path}, cancelling=cancelling)
        if 'telemetry_collection' in manifest:
            from .campaign_telemetry import attach_terminal
            cell = next(c for c in manifest['cells'] if c['cell_id'] == spec['campaign']['cell_id'])
            value = _receipt(cell, manifest, store.run_root)
            try:
                release = _read(release_path)
                _validate_release(release, manifest, spec['campaign']['batch_id'])
                if release.get('boot_id') == _boot_id() and release['released_monotonic'] > time.monotonic():
                    raise BenchmarkJobError('campaign_release_invalid', 'release begins in the future')
            except (OSError, ValueError, KeyError, TypeError, AttributeError, BenchmarkJobError):
                # A failed entry with no trustworthy release has no terminal to
                # await. Retain unknown coverage, never a fabricated closure.
                if value.get('status') != 'blocked' or 'entry_failure' not in value:
                    raise
                value['telemetry_terminal'] = {'status': 'unavailable', 'reason': 'campaign_release_unverified'}
                value['telemetry_coverage'] = {key: {'status': 'unavailable'} for key in ('engine', 'gpu', 'host', 'cgroup')}
            else:
                value = attach_terminal(store, spec, value, manifest, release)
            _commit_receipt(store, spec, value, cancelling=cancelling)
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


def required_batch_allowance(manifest, batch_id):
    # Child timeout already includes contained tests. The validated drain reserve
    # covers client cleanup, engine drain and final telemetry/receipt collection.
    return (CAMPAIGN_ENTRY_RESERVE_S + max(cell['spec']['timeout_s'] for cell in _cells(manifest, batch_id))
            + manifest['budgets']['drain_reserve_s'])


def require_batch_allowance(manifest, batch_id, *, run_root, store, allowance_s=None):
    required = required_batch_allowance(manifest, batch_id)
    remaining = _remaining_budget(manifest, batch_id, run_root=run_root, store=store)
    requested = required if allowance_s is None else allowance_s
    if not _finite(requested) or requested < required or requested > remaining:
        raise BenchmarkJobError('campaign_budget_exceeded',
            f'full batch requires {required:g}s; requested {requested!r}s; remaining {remaining:g}s')
    return requested


def require_campaign_suite_window(context):
    release, spec = context['release'], context['spec']
    remaining = release['traffic_deadline_monotonic'] - time.monotonic()
    if release['boot_id'] != _boot_id() or not _finite(remaining) or remaining < spec['timeout_s']:
        raise BenchmarkJobError('campaign_budget_exceeded',
            f'full suite requires {spec["timeout_s"]:g}s after entry; remaining {remaining:g}s')


def release_batch(manifest, batch_id, *, run_root, allowance_s, store=None, sampler=None):
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
    allowance_s = require_batch_allowance(manifest, batch_id, run_root=run_root, store=store,
                                         allowance_s=allowance_s)
    arm = next(arm for arm in manifest['arms'] if arm['arm_id'] == cells[0]['arm_id'])
    validate_drain_policy(arm.get('drain_policy', manifest.get('drain_policy', {})), reserve_s=reserve)
    activation = _activation(arm)
    if sampler is not None:
        sampler.start(activation)
        _activation(arm, expected=activation)
    elif 'telemetry_collection' in manifest:
        raise BenchmarkJobError('campaign_telemetry_unavailable', 'frozen collection requires the coordinator sampler')
    now = time.monotonic()
    value = {'schema': 'anvil-serving.campaign-release/v1', 'manifest_sha256': _manifest_digest(manifest),
        'batch_id': batch_id, 'arm_id': cells[0]['arm_id'], 'run_ids': [c['spec']['run_id'] for c in cells],
        'boot_id': _boot_id(), 'released_at': utc_now(), 'released_monotonic': now,
        'allowance_s': allowance_s, 'drain_reserve_s': reserve,
        'traffic_deadline_monotonic': now + allowance_s - reserve, 'deadline_monotonic': now + allowance_s}
    value['activation'] = activation
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(str(path), value)
    if sampler is not None:
        sampler.release = value
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


def validate_drain_policy(policy, *, reserve_s=None, resolved=False):
    if policy == {}:
        return policy
    keys = {'running_metric', 'waiting_metric', 'timeout_s', 'poll_s', 'metric_labels'}
    if resolved:
        keys |= {'metrics_url', 'generation'}
    if not isinstance(policy, dict) or set(policy) - keys:
        raise BenchmarkJobError('bad_campaign_drain', 'invalid drain collector fields')
    timeout, poll = policy.get('timeout_s'), policy.get('poll_s')
    labels = policy.get('metric_labels')
    if (not isinstance(labels, dict) or set(labels) not in ({'model'}, {'model_name'})
            or not isinstance(next(iter(labels.values()), None), str) or not next(iter(labels.values()), None) or len(next(iter(labels.values()))) > 512
            or any(not isinstance(policy.get(k), str) or not re.fullmatch(r'[a-zA-Z_:][a-zA-Z0-9_:]*', policy[k])
                   for k in ('running_metric', 'waiting_metric'))
            or not _finite(timeout) or not 0 < timeout <= 3600
            or not _finite(poll) or not .001 <= poll <= 10):
        raise BenchmarkJobError('bad_campaign_drain', 'drain requires bounded gauges, model labels, and an explicit endpoint')
    minimum = 35 + poll + 1
    if resolved:
        from .campaign_identity import _url
        try:
            if not re.fullmatch('[0-9a-f]{64}', policy.get('generation', '')):
                raise ValueError()
            _url(policy['metrics_url'], local=True, suffix='/engine/' + policy['generation'] + '/metrics')
        except (ValueError, TypeError, BenchmarkJobError) as exc:
            raise BenchmarkJobError('bad_campaign_drain', 'resolved observer URL is invalid') from exc
    if timeout <= minimum or reserve_s is not None and reserve_s < timeout + 22:
        raise BenchmarkJobError('bad_campaign_drain', 'drain timeout and reserve must include freshness, samples, activation rechecks and client termination')
    return policy


def observe_drain(policy, *, deadline, stopped_wall=None, tick=None):
    """Require one post-expiry observer sample with all selected gauges zero."""
    try:
        validate_drain_policy(policy, resolved=True)
        if policy:
            from .campaign_identity import _local_address
            _local_address(policy['metrics_url'])
    except BenchmarkJobError:
        return {'status': 'unknown', 'reason': 'engine_drain_collector_unavailable'}
    if not policy:
        return {'status': 'unknown', 'reason': 'engine_drain_collector_unavailable'}
    stopped_at = time.monotonic()
    stopped_wall = time.time() if stopped_wall is None else stopped_wall
    deadline = min(deadline, stopped_at + policy['timeout_s'])
    freshness_wait = 35
    if deadline - stopped_at <= freshness_wait + policy['poll_s']:
        return {'status': 'unknown', 'reason': 'insufficient_fresh_drain_window'}
    if tick is None:
        time.sleep(freshness_wait)
    else:
        fresh_at = stopped_at + freshness_wait
        while time.monotonic() < fresh_at:
            tick()
            time.sleep(min(1, max(0, fresh_at - time.monotonic())))
    while time.monotonic() < deadline:
        if tick is not None:
            tick()
        try:
            from ..router.model_capacity import _default_metrics_read
            status, raw = _default_metrics_read(policy['metrics_url'], {},
                timeout=min(1, max(.01, deadline-time.monotonic())), max_bytes=1024 * 1024)
            if status != 200 or len(raw) > 1024 * 1024:
                raise ValueError('oversized metrics')
            from .campaign_telemetry import engine_sample
            stamp, values = engine_sample(raw.decode('utf-8'), policy,
                next(iter(policy['metric_labels'].values())), time.time(), stopped_wall=stopped_wall)
            sample = {'observed_monotonic': time.monotonic(), 'source_timestamp_seconds': stamp, **values}
            if sample['running_metric'] == sample['waiting_metric'] == 0:
                return {'status': 'verified', 'sample': sample, 'identity_contract': 'generation-bound-observer/v1',
                        'collector_sha256': _digest(policy), 'freshness_wait_s': freshness_wait}
        except (OSError, ValueError):
            pass
        time.sleep(min(policy.get('poll_s', .1), max(0, deadline-time.monotonic())))
    return {'status': 'unknown', 'reason': 'engine_drain_not_verified'}


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
    _commit_receipt(store, spec, value)
    context = {'manifest': manifest, 'payload': payload, 'receipt': value,
               'path': path, 'store': store, 'spec': spec}
    supervisor = None
    try:
        release_path = _directory(manifest, store.run_root) / 'batches' / (binding['batch_id'] + '.json')
        wait_until = time.monotonic() + 60
        while not release_path.exists():
            if time.monotonic() >= wait_until or store.status(spec['run_id'])['state'] != 'running':
                raise BenchmarkJobError('campaign_release_missing', 'campaign release was not received before the bounded readiness deadline')
            time.sleep(.05)
        release = _read(release_path)
        _validate_release(release, manifest, binding['batch_id'])
        with campaign_lock(_directory(manifest, store.run_root)):
            if _state(_directory(manifest, store.run_root), manifest=manifest).get('cancel_requested') is True:
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
        context['release'] = release
        # Recheck after the barrier: setup-time verification alone leaves a drift window.
        verify_child(spec, run_root=store.run_root)
        arm = next(arm for arm in manifest['arms'] if arm['arm_id'] == binding['arm_id'])
        _activation(arm, expected=release['activation'])
        value.update(status='active', started_at=utc_now(), started_monotonic=time.monotonic())
        value['activation'] = release['activation']
        _commit_receipt(store, spec, value)
        # Full frozen-asset verification may take seconds. Give custody startup
        # thirty seconds, always charged to the remaining traffic allowance.
        supervisor = subprocess.Popen([sys.executable, '-m', 'anvil_serving.benchmarking.worker',
            '--campaign-watchdog', '--db', os.path.realpath(store.path), '--run-root', store.run_root, '--run-id', spec['run_id']],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
        startup_deadline = min(time.monotonic() + 30, release['traffic_deadline_monotonic'])
        acknowledgement = b''
        while time.monotonic() < startup_deadline:
            readable, _, _ = select.select([supervisor.stdout], [], [], max(0, startup_deadline - time.monotonic()))
            if not readable:
                break
            chunk = os.read(supervisor.stdout.fileno(), 6 - len(acknowledgement))
            acknowledgement += chunk
            if not chunk or not b'ready\n'.startswith(acknowledgement) or acknowledgement == b'ready\n':
                break
        if acknowledgement != b'ready\n' or time.monotonic() >= startup_deadline or supervisor.poll() is not None:
            raise BenchmarkJobError('campaign_watchdog_unavailable', 'bounded supervisor did not acknowledge custody')
        supervisor.stdout.close()
        return context
    except Exception as exc:
        # The caller has not received this context yet. Publish closure before
        # its failure artifact waits on the coordinator's shared terminal.
        try:
            _abort_campaign_entry(context, supervisor, exc)
        except Exception:
            # Inaccessible storage/custody must not replace the original error.
            # Without a writable unanchored receipt, closure remains unknown.
            pass
        raise


def _abort_campaign_entry(context, supervisor, error):
    stopped = supervisor is None
    if supervisor is not None:
        try:
            if supervisor.poll() is None:
                supervisor.kill()
            supervisor.wait(timeout=2)
            stopped = True
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            try:
                supervisor.stdout.close()
            except OSError:
                pass
    store, spec = context['store'], context['spec']
    if store.status(spec['run_id'])['state'] in {'completed', 'failed', 'cancelled'}:
        return  # Never change an immutable artifact's anchored receipt.
    value = context['receipt']
    value['entry_failure'] = {'code': error.code if isinstance(error, BenchmarkJobError) else type(error).__name__}
    if stopped and value['status'] == 'active' and 'release' in context:
        try:
            close_campaign(context, wait=False)
            return
        except Exception:
            pass
    clients = stopped and _clients_stopped()
    value.update(status='blocked', clients_stopped=clients,
                 drain={'status': 'unknown', 'reason': 'campaign_entry_aborted'})
    if clients:
        value['clients_stopped_wall'] = time.time()
    _commit_receipt(store, spec, value)


def close_campaign(context, *, clients_stopped=None, wait=True, cancelling=False):
    """Publish only this worker's stop fact; global drain belongs to the batch."""
    store, spec = context['store'], context['spec']
    if store.status(spec['run_id'])['state'] in TERMINAL:
        return _read(context['path'])
    value = dict(context['receipt'])
    clients = _clients_stopped() if clients_stopped is None else clients_stopped
    value.update(status='draining' if clients else 'blocked', clients_stopped=clients,
                 drain={'status': 'pending' if clients else 'unknown', 'reason': 'awaiting_batch_clients'})
    if clients:
        value.setdefault('clients_stopped_wall', time.time())
    context['receipt'] = _commit_receipt(store, spec, value, cancelling=cancelling)
    if wait and clients:
        context['receipt'] = _await_batch(context, cancelling=cancelling)
    return context['receipt']


def close_cancelled_campaign(store, spec, *, clients_stopped):
    path = _path(spec, store.run_root, 'campaign-execution.json')
    if not path.exists():
        return None
    manifest, _ = verify_child(spec, run_root=store.run_root)
    release_path = _directory(manifest, store.run_root) / 'batches' / (spec['campaign']['batch_id'] + '.json')
    if not release_path.exists():
        return None
    release = _read(release_path)
    _validate_release(release, manifest, spec['campaign']['batch_id'])
    return close_campaign({'receipt': _read(path), 'path': path, 'manifest': manifest,
        'release': release, 'store': store, 'spec': spec}, clients_stopped=clients_stopped,
        wait=False, cancelling=True)


def _watchdog(database, run_root, run_id, *, cancel_job, stop_worker):
    """Bounded supervisor algorithm; worker supplies its custody operations."""
    from ..control_plane.controller.store import BenchmarkJobStore
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
            if store.status(run_id)['state'] in TERMINAL:
                stop_worker(str(_path(spec, run_root, 'worker.json')), run_id,
                            database=os.path.realpath(store.path), run_root=store.run_root)
                return
            receipt = _receipt(cell, manifest, run_root)
        except (BenchmarkJobError, OSError, ValueError, KeyError, TypeError):
            break
        if (receipt['status'] in {'closed', 'blocked'} and receipt.get('clients_stopped') is True
                and _anchored_closure(store, cell, manifest, run_root)):
            return
        if (receipt.get('status') in {'draining', 'closed', 'blocked'} and receipt.get('clients_stopped') is True
                and receipt.get('activation') == release.get('activation')):
            deadline = release['deadline_monotonic']
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    try:
        terminal = store.status(run_id)
    except (BenchmarkJobError, OSError, ValueError, sqlite3.Error):
        # The worker receipt was verified before this supervisor acknowledged
        # custody. A damaged database must not turn the finite deadline into an
        # unbounded run.
        stop_worker(
            str(_path(spec, run_root, 'worker.json')), run_id,
            database=os.path.realpath(store.path), run_root=store.run_root,
        )
        # Unreadable durable state cannot authorize a receipt rewrite.
        return

    stopped = False
    if terminal is None or terminal['state'] not in {'completed', 'failed', 'cancelled'}:
        try:
            cancelled = cancel_job(store, run_id)
            stopped = cancelled.get('worker_terminated') is True
        except (BenchmarkJobError, OSError, ValueError, sqlite3.Error):
            pass
    if not stopped:
        stop_worker(str(_path(spec, run_root, 'worker.json')), run_id,
                           database=os.path.realpath(store.path), run_root=store.run_root)
