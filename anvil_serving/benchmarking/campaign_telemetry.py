"""Bounded coordinator-owned telemetry; retained samples, never inferred coverage."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from .artifacts import atomic_write_json
from .jobs import BenchmarkJobError, canonical_json_bytes

SCHEMA = 'anvil-serving.campaign-telemetry/v1'
PLANES = ('engine', 'gpu', 'host', 'cgroup')
STAMP = 'anvil_observer_metrics_sample_timestamp_seconds'
NAME = re.compile(r'[a-zA-Z_:][a-zA-Z0-9_:]{0,127}\Z')
MAX_READ = 1024 * 1024


def digest(value):
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def validate_collection(value, *, arms, reserve_s):
    keys = {'schema', 'cadence_s', 'max_gap_s', 'timeout_s', 'max_rows', 'max_bytes', 'gpu', 'host'}
    if not isinstance(value, dict) or set(value) != keys or value['schema'] != SCHEMA:
        raise BenchmarkJobError('bad_campaign_telemetry', 'invalid telemetry collection contract')
    if (not all(number(value[k]) for k in ('cadence_s', 'max_gap_s', 'timeout_s'))
            or not 1 <= value['cadence_s'] <= 30 or not .1 <= value['timeout_s'] <= 3
            or not value['cadence_s'] + 4 * value['timeout_s'] <= value['max_gap_s'] <= 60
            or type(value['max_rows']) is not int or not 2 <= value['max_rows'] <= 4096
            or type(value['max_bytes']) is not int or not 65536 <= value['max_bytes'] <= 8 * 1024 * 1024):
        raise BenchmarkJobError('bad_campaign_telemetry', 'telemetry cadence, gaps and ceilings must be bounded')
    from .campaign_identity import _url
    for plane in ('gpu', 'host'):
        source = value[plane]
        fields = {'url', 'timestamp_metric', 'metrics'}
        if plane == 'gpu':
            fields |= {'identity_label', 'identity_prefix', 'success_metric'}
        if not isinstance(source, dict) or set(source) != fields:
            raise BenchmarkJobError('bad_campaign_telemetry', 'invalid telemetry source contract')
        try:
            _url(source['url'], local=True, suffix='/metrics')
        except (ValueError, TypeError, BenchmarkJobError) as exc:
            raise BenchmarkJobError('bad_campaign_telemetry', 'telemetry requires literal local HTTP URLs without credentials or queries') from exc
        metrics = source['metrics']
        if (not isinstance(metrics, dict) or not 1 <= len(metrics) <= 16
                or any(not isinstance(k, str) or not NAME.fullmatch(k) or v not in ('gauge', 'counter') for k, v in metrics.items())
                or not isinstance(source['timestamp_metric'], str) or not NAME.fullmatch(source['timestamp_metric'])
                or source['timestamp_metric'] in metrics):
            raise BenchmarkJobError('bad_campaign_telemetry', 'telemetry metric selection is invalid')
        if plane == 'gpu' and (source['identity_label'] != 'uuid' or source['identity_prefix'] not in ('', 'GPU-')
                or not isinstance(source['success_metric'], str) or not NAME.fullmatch(source['success_metric'])):
            raise BenchmarkJobError('bad_campaign_telemetry', 'GPU source requires UUID and collection success bindings')
    for arm in arms:
        policy = arm.get('drain_policy', {})
        if not policy or reserve_s < policy['timeout_s'] + 23 + value['max_gap_s'] + 4 * value['timeout_s']:
            raise BenchmarkJobError('bad_campaign_telemetry', 'reserve must include drain, final fresh telemetry and receipt binding')
        if len(arm['identity']['hardware']) != len(set(arm['identity']['hardware'])) or len(arm['identity']['hardware']) > 16:
            raise BenchmarkJobError('bad_campaign_telemetry', 'telemetry requires unique bounded GPU identities')
    return value


def metric(raw, name, labels=None):
    """One explicitly selected series; ambiguous or nonfinite values fail closed."""
    from ..router.model_capacity import _label_value
    pattern = re.compile(r'^' + re.escape(name) + r'(?:\{(?P<labels>[^\n]*)\})?\s+(?P<value>[^\s]+)(?:\s+[^\s]+)?$', re.M)
    model = (labels or {}).get('model', (labels or {}).get('model_name'))
    found = [float(m.group('value')) for m in pattern.finditer(raw)
             if all(_label_value(m.group('labels') or '', key) == value for key, value in (labels or {}).items())
             and (model is None or all(_label_value(m.group('labels') or '', key) in (None, model) for key in ('model', 'model_name')))]
    if len(found) != 1 or not number(found[0]):
        raise ValueError('missing_or_ambiguous_series')
    return found[0]


def engine_sample(raw, policy, model, wall, *, stopped_wall=None):
    stamp = metric(raw, STAMP, {'model': model})
    if not 0 <= wall - stamp <= 35 or stopped_wall is not None and stamp <= stopped_wall:
        raise ValueError('stale_engine_sample')
    values = {key: metric(raw, policy[key], policy['metric_labels']) for key in ('running_metric', 'waiting_metric')}
    return stamp, values


def _container(arm, activation, timeout):
    from .. import serve_recipes
    from ..transports import _run_bounded_process
    deadline = time.monotonic() + timeout
    def run(argv, **kwargs):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        result = _run_bounded_process(argv, timeout=remaining, max_output_bytes=4 * MAX_READ, env={'PATH': os.defpath})
        return subprocess.CompletedProcess(argv, result.returncode, result.stdout.decode('utf-8'), result.stderr.decode('utf-8'))
    row = serve_recipes.select_recipe_container(serve_recipes.discover_recipe_containers(_run=run, strict=True),
                                               container=arm['activation']['container'])
    expected = {'container_id': activation['container_id'], 'started_at': activation['started_at'],
                'image_digest': arm['identity']['image'], 'served_identity': activation['served_model'],
                'recipe_digest': arm['identity']['recipe_sha256']}
    if (not row.get('running') or any(row.get(k) != v for k, v in expected.items())
            or sorted(row.get('gpu_selection') or []) != sorted(arm['identity']['hardware'])):
        raise ValueError('container_identity_changed')
    memory = row['host_memory']
    values = {k: memory.get(k) for k in ('current_bytes', 'peak_bytes', 'limit_bytes', 'ram_plus_swap_limit_bytes')}
    values.update({k: (memory.get('events') or {}).get(k) for k in ('oom', 'oom_kill')})
    if not all(number(v) for v in values.values()) or type(memory.get('oom_killed')) is not bool:
        raise ValueError('cgroup_sample_unavailable')
    values['oom_killed'] = int(memory['oom_killed'])
    return values


def _read(root, relative, limit, reference=None):
    if not isinstance(relative, str):
        raise ValueError('invalid_telemetry_path')
    rel = Path(relative)
    path = root / rel
    if (not isinstance(relative, str) or not relative or '\\' in relative or rel.is_absolute() or '..' in rel.parts
            or any(p.is_symlink() for p in (path, *path.parents)) or not path.resolve().is_relative_to(root.resolve())):
        raise ValueError('invalid_telemetry_path')
    with open(path, 'rb', opener=lambda p, f: os.open(p, f | os.O_NONBLOCK | os.O_NOFOLLOW)) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('nonregular_telemetry')
        raw = stream.read(limit + 1)
    if len(raw) > limit or reference is not None and (reference.get('sha256') != hashlib.sha256(raw).hexdigest() or reference.get('bytes') != len(raw)):
        raise ValueError('invalid_telemetry_bytes')
    return raw


def _reference(root, relative, limit):
    # Same evidence reference contract, read once with the telemetry byte bound.
    raw = _read(root, relative, limit)
    return {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _fields(contract, arm, plane):
    if plane == 'engine':
        return {'running_metric': 'gauge', 'waiting_metric': 'gauge'}
    if plane == 'cgroup':
        return {k: 'counter' if k in ('oom', 'oom_kill') else 'gauge' for k in
                ('current_bytes', 'peak_bytes', 'limit_bytes', 'ram_plus_swap_limit_bytes', 'oom', 'oom_kill', 'oom_killed')}
    return {name + ('/' + gpu if plane == 'gpu' else ''): kind
            for name, kind in contract[plane]['metrics'].items()
            for gpu in (arm['identity']['hardware'] if plane == 'gpu' else [''])}


def _units(fields):
    result = {}
    for key, kind in fields.items():
        name = key.split('/')[0]
        result[key] = ('requests' if name in ('running_metric', 'waiting_metric') else
            'bytes' if name.endswith('_bytes') else 'watts' if name.endswith('_watts') else
            'ratio' if name.endswith('_ratio') else 'seconds' if name.endswith(('_seconds', '_seconds_total')) else
            'boolean' if name == 'oom_killed' or '_reasons_' in name else
            'count' if kind == 'counter' or name == 'node_vmstat_oom_kill' else 'native')
    return result


def coverage(rows, contract, arm, start, end, *, complete):
    result = {}
    for plane in PLANES:
        samples, failures, resets, previous, seen = [], 0, 0, None, {}
        fields = _fields(contract, arm, plane)
        for row in rows:
            item = row['planes'][plane]
            if not isinstance(item, dict):
                raise ValueError('invalid_sample')
            if item.get('status') not in ('measured', 'unavailable'):
                raise ValueError('invalid_sample_status')
            if item['status'] != 'measured':
                failures += 1
                continue
            stamp, values = item['source_wall'], item['values']
            if set(values) != set(fields) or not all(number(v) for v in values.values()) or item.get('units') != _units(fields):
                raise ValueError('invalid_telemetry_series')
            if (not all(number(item.get(k)) for k in ('wall', 'monotonic'))
                    or not number(stamp) or not 0 <= item['wall'] - stamp <= (35 if plane == 'engine' else contract['max_gap_s'])
                    or not number(item['source_monotonic'])
                    or not math.isclose(item['source_monotonic'], item['monotonic'] - (item['wall'] - stamp), abs_tol=.001)):
                raise ValueError('invalid_telemetry_timestamp')
            if stamp in seen:
                # Regressions and changed cache contents are retained failures,
                # never extra measured timestamps or interpolated continuity.
                failures += int(values != seen[stamp])
                resets += int(previous is not None and stamp < previous['source_wall'])
                continue
            seen[stamp] = values
            reset = previous is not None and (stamp < previous['source_wall'] or item['source_monotonic'] < previous['source_monotonic'] or any(
                kind == 'counter' and values[key] < previous['values'][key] for key, kind in fields.items()))
            resets += int(reset)
            samples.append(item['source_monotonic'])
            previous = item
        gaps = [b-a for a, b in zip(samples, samples[1:])]
        maximum = max(gaps, default=0)
        full = (complete and number(start) and number(end) and start <= end and len(samples) >= 2
                and not failures and not resets and samples[0] <= start <= end <= samples[-1]
                and maximum <= contract['max_gap_s'])
        result[plane] = {'status': 'measured' if samples else 'unavailable', 'complete': bool(full),
            'sample_count': len(samples), 'started_monotonic': samples[0] if samples else None,
            'closed_monotonic': samples[-1] if samples else None, 'max_gap_s': maximum,
            'failures': failures, 'reset_segments': resets}
    return result


class BatchSampler:
    """One synchronous bounded cycle in the existing foreground coordinator."""
    def __init__(self, manifest, batch_id, *, run_root):
        from .campaign_identity import _cells, _directory, _boot_id
        self.manifest, self.batch_id, self.run_root = manifest, batch_id, run_root
        self.cells = _cells(manifest, batch_id)
        self.arm = next(a for a in manifest['arms'] if a['arm_id'] == self.cells[0]['arm_id'])
        self.contract = manifest['telemetry_collection']
        self.root = _directory(manifest, run_root)
        self.relative = 'telemetry/' + batch_id + '/'
        self.rows, self.byte_count, self.next_tick = [], 0, 0
        self.release = self.activation = None
        self.finished = False
        self.truncated = False
        self.boot = _boot_id()

    @classmethod
    def recover(cls, manifest, batch_id, release, *, run_root):
        """Resume retained sampling only while holding the batch closure lock."""
        sampler = cls(manifest, batch_id, run_root=run_root)
        sampler.activation, sampler.release, sampler.boot = release['activation'], release, release['boot_id']
        try:
            raw = _read(sampler.root, sampler.relative + 'samples.jsonl', sampler.contract['max_bytes'])
            sampler.rows = [json.loads(line) for line in raw.splitlines()]
            if not 1 <= len(sampler.rows) <= sampler.contract['max_rows']:
                return None
            for row in sampler.rows:
                if (row['manifest_sha256'] != manifest['manifest_sha256']
                        or row['activation_sha256'] != release['activation']['binding_sha256']
                        or row['batch_id'] != batch_id or row['boot_id'] != release['boot_id']):
                    return None
            coverage(sampler.rows, sampler.contract, sampler.arm, release['released_monotonic'],
                     time.monotonic(), complete=False)
            sampler.byte_count = len(raw)
            sampler.next_tick = sampler.rows[-1]['monotonic'] + sampler.contract['cadence_s']
            if (sampler.root / (sampler.relative + 'terminal.json')).exists():
                verify_terminal(manifest, batch_id, release, run_root=run_root)
                sampler.finished = True
            return sampler
        except (OSError, ValueError, KeyError, TypeError, BenchmarkJobError):
            return None

    def start(self, activation):
        self.activation = activation
        directory = self.root / self.relative
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise BenchmarkJobError('campaign_telemetry_path', 'telemetry must remain in its owned directory')
        directory.mkdir(parents=True, exist_ok=False)
        with open(directory / 'samples.jsonl', 'xb', opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)):
            pass
        self.tick(force=True)

    def tick(self, *, force=False):
        if self.finished or self.truncated or not self.activation or not force and time.monotonic() < self.next_tick:
            return
        deadline = self.release['deadline_monotonic'] - 1 if self.release else time.monotonic() + 4 * self.contract['timeout_s']
        if time.monotonic() >= deadline:
            return
        row = {'schema': SCHEMA, 'manifest_sha256': self.manifest['manifest_sha256'],
            'contract_sha256': digest(self.contract), 'activation_sha256': self.activation['binding_sha256'],
            'batch_id': self.batch_id, 'arm_id': self.arm['arm_id'], 'boot_id': self.boot,
            'wall': time.time(), 'monotonic': time.monotonic(), 'planes': {}}
        row['utc'] = datetime.fromtimestamp(row['wall'], timezone.utc).isoformat()
        from ..router.model_capacity import _default_metrics_read
        for plane in PLANES:
            stage = 'source_read_failed'
            try:
                timeout = min(self.contract['timeout_s'], deadline - time.monotonic())
                if timeout <= 0:
                    raise TimeoutError()
                if plane == 'cgroup':
                    stage = 'cgroup_identity_or_memory_unavailable'
                    values = _container(self.arm, self.activation, timeout)
                    stamp = time.time()
                else:
                    source = self.contract.get(plane, {})
                    url = self.activation['metrics_url'] if plane == 'engine' else source['url']
                    from .campaign_identity import _local_address
                    _local_address(url)
                    status, raw = _default_metrics_read(url, {}, timeout=timeout, max_bytes=MAX_READ)
                    if status != 200 or len(raw) > MAX_READ:
                        raise ValueError('metrics_read_failed')
                    stage = 'source_parse_or_identity_failed'
                    raw = raw.decode('utf-8')
                    if plane == 'engine':
                        policy = self.arm.get('drain_policy', self.manifest.get('drain_policy', {}))
                        stamp, values = engine_sample(raw, policy, self.activation['served_model'], time.time())
                    else:
                        stamp = metric(raw, source['timestamp_metric'])
                        if plane == 'gpu' and metric(raw, source['success_metric']) != 1:
                            raise ValueError('gpu_collection_failed')
                        values = {}
                        for name in source['metrics']:
                            for gpu in (self.arm['identity']['hardware'] if plane == 'gpu' else ['']):
                                labels = {source['identity_label']: gpu.removeprefix(source['identity_prefix'])} if plane == 'gpu' else {}
                                values[name + ('/' + gpu if gpu else '')] = metric(raw, name, labels)
                stage = 'source_timestamp_stale_or_invalid'
                wall, mono = time.time(), time.monotonic()
                if not number(stamp) or not 0 <= wall - stamp <= (35 if plane == 'engine' else self.contract['max_gap_s']):
                    raise ValueError('stale_source')
                # Each plane records its own read completion, avoiding serial-read clock skew.
                row['planes'][plane] = {'status': 'measured', 'source_wall': stamp,
                    'source_monotonic': mono - (wall - stamp), 'values': values, 'units': _units(_fields(self.contract, self.arm, plane)), 'wall': wall, 'monotonic': mono}
            except (OSError, ValueError, KeyError, TypeError, BenchmarkJobError, subprocess.SubprocessError, http.client.HTTPException):
                row['planes'][plane] = {'status': 'unavailable', 'reason': stage}
        raw = canonical_json_bytes(row) + b'\n'
        if len(self.rows) >= self.contract['max_rows'] or self.byte_count + len(raw) > self.contract['max_bytes']:
            self.truncated = True
            return
        path = self.root / (self.relative + 'samples.jsonl')
        with open(path, 'ab', opener=lambda p, f: os.open(p, f | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('nonregular_telemetry')
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self.rows.append(row)
        self.byte_count += len(raw)
        self.next_tick = time.monotonic() + self.contract['cadence_s']

    def finish(self, *, partial=False, closure=None):
        if self.finished or not self.activation:
            return
        if (self.root / (self.relative + 'terminal.json')).exists():
            self.finished = True
            return  # A different serialized owner already finalized this batch.
        from .campaign_identity import _receipt
        receipts = []
        try:
            if closure is None:
                receipts = [_receipt(c, self.manifest, self.run_root) for c in self.cells]
        except (OSError, ValueError, KeyError, TypeError, AttributeError, BenchmarkJobError):
            partial = True
        closed = receipts and all(r.get('status') == 'closed' and r.get('clients_stopped') is True and r.get('drain', {}).get('status') == 'verified' for r in receipts)
        if closure is not None:
            closed = closure['status'] == 'closed'
        complete = bool(closed and not partial and self.release)
        end = closure['closed_monotonic'] if closure is not None else max((r.get('closed_monotonic', 0) for r in receipts), default=0)
        final_deadline = min(time.monotonic() + self.contract['max_gap_s'], self.release['deadline_monotonic'] - 1) if self.release else time.monotonic()
        if not complete:
            self.tick(force=True)
        while complete and not self.truncated and time.monotonic() < final_deadline:
            self.tick(force=True)
            measured = coverage(self.rows, self.contract, self.arm, self.release['released_monotonic'], end, complete=True)
            if all(number(v['closed_monotonic']) and v['closed_monotonic'] >= end for v in measured.values()):
                break
            time.sleep(min(self.contract['cadence_s'], max(0, final_deadline-time.monotonic())))
        end_time = time.monotonic()
        complete = complete and not self.truncated and end_time <= self.release['deadline_monotonic']
        summary = coverage(self.rows, self.contract, self.arm,
            self.release['released_monotonic'] if self.release else None, end, complete=complete)
        value = {'schema': SCHEMA, 'manifest_sha256': self.manifest['manifest_sha256'],
            'contract_sha256': digest(self.contract), 'activation': self.activation, 'boot_id': self.boot,
            'batch_id': self.batch_id, 'arm_id': self.arm['arm_id'], 'complete': complete,
            'truncated': self.truncated, 'finalized_monotonic': end_time, 'interval_end_monotonic': end,
            'row_count': len(self.rows), 'coverage': summary,
            'samples': _reference(self.root, self.relative + 'samples.jsonl', self.contract['max_bytes'])}
        atomic_write_json(self.root / (self.relative + 'terminal.json'), value)
        self.finished = True


def verify_terminal(manifest, batch_id, release, *, run_root, reference=None):
    from .campaign_identity import _directory, _cells
    contract = manifest['telemetry_collection']
    validate_collection(contract, arms=[{**a, 'drain_policy': a.get('drain_policy', manifest.get('drain_policy', {}))} for a in manifest['arms']], reserve_s=manifest['budgets']['drain_reserve_s'])
    arm = next(a for a in manifest['arms'] if a['arm_id'] == _cells(manifest, batch_id)[0]['arm_id'])
    root = _directory(manifest, run_root)
    relative = 'telemetry/' + batch_id + '/terminal.json'
    if reference is not None and (not isinstance(reference, dict) or reference.get('path') != relative):
        raise ValueError('invalid_terminal_path')
    terminal = json.loads(_read(root, relative, MAX_READ, reference))
    if not isinstance(terminal, dict):
        raise ValueError('invalid_terminal')
    expected = {'schema': SCHEMA, 'manifest_sha256': manifest['manifest_sha256'], 'contract_sha256': digest(contract),
        'activation': release['activation'], 'boot_id': release['boot_id'], 'batch_id': batch_id, 'arm_id': arm['arm_id']}
    if any(terminal.get(k) != v for k, v in expected.items()) or type(terminal.get('complete')) is not bool or type(terminal.get('truncated')) is not bool:
        raise ValueError('terminal_identity_mismatch')
    if (not number(terminal.get('interval_end_monotonic')) or not number(terminal.get('finalized_monotonic'))
            or terminal['interval_end_monotonic'] > terminal['finalized_monotonic'] or not release['released_monotonic'] <= terminal['finalized_monotonic'] <= release['deadline_monotonic']):
        raise ValueError('terminal_outside_release')
    ref = terminal['samples']
    if not isinstance(ref, dict) or ref.get('path') != 'telemetry/' + batch_id + '/samples.jsonl':
        raise ValueError('invalid_samples_path')
    raw = _read(root, ref['path'], contract['max_bytes'], ref)
    lines = raw.splitlines()
    if type(terminal['row_count']) is not int or len(lines) != terminal['row_count'] or not 1 <= len(lines) <= contract['max_rows']:
        raise ValueError('invalid_row_count')
    rows = [json.loads(line) for line in lines]
    previous = -1
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('planes'), dict):
            raise ValueError('invalid_sample')
        expected_row = {k: v for k, v in expected.items() if k != 'activation'}
        expected_row['activation_sha256'] = release['activation']['binding_sha256']
        if (any(row.get(k) != v for k, v in expected_row.items()) or set(row.get('planes', {})) != set(PLANES)
                or not number(row.get('wall')) or not number(row.get('monotonic'))
                or not previous <= row['monotonic'] <= terminal['finalized_monotonic']):
            raise ValueError('sample_identity_mismatch')
        previous = row['monotonic']
        for item in row['planes'].values():
            if not isinstance(item, dict):
                raise ValueError('invalid_plane')
            if item.get('status') == 'measured' and (not number(item.get('monotonic')) or not row['monotonic'] <= item['monotonic'] <= terminal['finalized_monotonic']):
                raise ValueError('plane_outside_interval')
    summary = coverage(rows, contract, arm, release['released_monotonic'], terminal['interval_end_monotonic'], complete=terminal['complete'])
    if terminal['coverage'] != summary or terminal.get('truncated') is not False and terminal['complete']:
        raise ValueError('forged_coverage')
    return terminal, rows, _reference(root, relative, MAX_READ)


def attach_terminal(store, spec, receipt, manifest, release):
    """Called before every immutable child artifact; never repair an anchored receipt."""
    from .campaign_identity import _boot_id
    if 'telemetry_collection' not in manifest or 'telemetry_terminal' in receipt:
        return receipt
    if release['boot_id'] == _boot_id():
        while time.monotonic() < release['deadline_monotonic']:
            try:
                terminal, rows, reference = verify_terminal(manifest, spec['campaign']['batch_id'], release, run_root=store.run_root)
                arm = next(a for a in manifest['arms'] if a['arm_id'] == spec['campaign']['arm_id'])
                receipt['telemetry_coverage'] = coverage(rows, manifest['telemetry_collection'], arm,
                    receipt.get('started_monotonic'), receipt.get('closed_monotonic'), complete=terminal['complete'])
                receipt['telemetry_terminal'] = reference
                return receipt
            except FileNotFoundError:
                time.sleep(min(.05, max(0, release['deadline_monotonic']-time.monotonic())))
            except (OSError, ValueError, KeyError, TypeError, RecursionError, BenchmarkJobError):
                break
    receipt['telemetry_terminal'] = {'status': 'unavailable', 'reason': 'coordinator_terminal_unavailable'}
    receipt['telemetry_coverage'] = {key: {'status': 'unavailable'} for key in PLANES}
    return receipt
