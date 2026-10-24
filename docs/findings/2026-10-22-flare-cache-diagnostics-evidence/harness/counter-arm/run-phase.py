"""Validation harness: bounded HTTP child plus managed read-only identity checks."""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import time

E = Path(__file__).parent
W = Path('OPERATOR_PATH_cc25b9dd30c4/anvil-serving')
PY = 'OPERATOR_PATH_9ba32d4067f1/python'
spec = importlib.util.spec_from_file_location('replay', E / 'cache-replay.py')
replay = importlib.util.module_from_spec(spec); spec.loader.exec_module(replay)
IDENTITY = ('container_id', 'image_id', 'started_at', 'restart_count', 'model', 'revision')
POOL = 'flare_host_cache_memory_'
POOL_GAUGES = ('pages', 'free_pages', 'rows', 'free_rows')
POOL_COUNTERS = ('page_pressure_total', 'row_pressure_total', 'evicted_records_total',
                 'compacted_rows_total', 'no_evictable_total', 'oversized_total')

def validate_pool(raw, fresh):
    values = {}
    for line in raw.splitlines():
        if line.startswith(POOL):
            key, value = line.rsplit(' ', 1)
            if key in values:
                raise RuntimeError('duplicate pool metric: ' + key)
            values[key] = float(value)
    for name in (*POOL_GAUGES, *POOL_COUNTERS):
        value = values.get(POOL + name, float('nan'))
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            raise RuntimeError('invalid or missing pool metric: ' + name)
    if values[POOL+'pages'] != 7314 or values[POOL+'rows'] != 52:
        raise RuntimeError('unexpected pool geometry')
    for unit in ('pages', 'rows'):
        if values[POOL+'free_'+unit] > values[POOL+unit]:
            raise RuntimeError('invalid free pool capacity')
        if fresh and values[POOL+'free_'+unit] != values[POOL+unit]:
            raise RuntimeError('fresh pool already occupied')
    if fresh and any(values[POOL+name] != 0 for name in POOL_COUNTERS):
        raise RuntimeError('fresh pool pressure counters nonzero')
    return values

def same_identity(before, after):
    if any(before[k] != after[k] for k in IDENTITY):
        raise RuntimeError('candidate incarnation changed')

def validate_prior(prior, order, count):
    plan = json.loads((prior / 'plan.json').read_text())
    rows = json.loads((prior / 'results.json').read_text())
    forward_indices = [r['index'] for r in replay.corpus('forward', count)]
    if (plan['options'] != replay.OPTIONS or plan['count'] != count
            or plan['order'] != 'forward' or plan['indices'] != forward_indices
            or [r['index'] for r in rows] != forward_indices
            or plan['corpus_sha256'] != replay.digest(replay.CORPUS.read_bytes())
            or len(rows) != count):
        raise RuntimeError('prior corpus/options/completeness mismatch')
    expected = {r['index']: replay.digest(json.dumps(replay.build_body(
        replay.MODEL, r['prompt'], **replay.OPTIONS)).encode()) for r in replay.corpus(order, count)}
    if {r['index']: r['body_sha256'] for r in rows} != expected:
        raise RuntimeError('prior request bodies differ')
    return rows[-1]['uptime_after']

def run(name, order, count, prior, fresh):
    with (E / (name + '-reserved.json')).open('x') as reservation:
        reservation.write(json.dumps({'at': time.time(), 'name': name}) + '\n')
    env = os.environ.copy()
    env['ANVIL_SERVING_HOME'] = 'OPERATOR_PATH_601ba1a688c8/operator-home'
    env['PYTHONPATH'] = str(W)
    frozen = json.loads((E / 'frozen-inputs.json').read_text())
    for path, expected in frozen['files'].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise RuntimeError('frozen file changed: ' + path)
    def status(label):
        argv = [PY, '-m', 'anvil_serving.cli', 'models', 'recipes', 'status',
            'local-inference-lab/GLM-5.3-Flash-NVFP4-MXFP8-CSF-QAD', '--container',
            'glm53-flare-cache-counters', '--registry', str(E / 'flare-instrumented-recipe.toml'), '--json']
        result = subprocess.run(argv, env=env, cwd=W, capture_output=True, text=True, timeout=15)
        (E / (name + '-' + label + '.json')).write_text(result.stdout)
        result.check_returncode()
        envelope = json.loads(result.stdout)
        data = json.loads(envelope['data']) if isinstance(envelope['data'], str) else envelope['data']
        if (not envelope['ok'] or not data['running'] or data['restart_count'] != 0
                or data['containment_error'] or data['host_memory']['oom_killed']
                or data['host_memory']['events'].get('oom', 0)
                or data['image_id'] != 'sha256:aa74f31ae8a97387ecf14af5b334222ba858ca540241d45a9eccdbae0054c0fc'
                or data['revision'] != 'dec48abd33efa73c3bb7c95b74eee10cad34f9be'):
            raise RuntimeError('managed identity/containment gate failed')
        return data
    before = status('before')
    key, _ = replay.read_key(replay.KEY)
    raw, counters = replay.idle(key)
    validate_pool(raw, fresh)
    (E / (name + '-pool-before.prom')).write_text(raw)
    if fresh and any(counters[k] != 0 for k in ('flare_requests_completed_total',
            'flare_host_cache_stored_total', 'flare_host_cache_fetched_total')):
        raise RuntimeError('fresh incarnation already received generation')
    if prior:
        prior = E / prior
        old = json.loads((E / (prior.name + '-after.json')).read_text())['data']
        old = json.loads(old) if isinstance(old, str) else old
        same_identity(old, before)
        if counters['flare_uptime_seconds'] < validate_prior(prior, order, count):
            raise RuntimeError('cross-phase uptime regressed')
    outage = json.loads((E / 'outage-start.json').read_text())['at']
    remaining = int(min(1800, outage + 70 * 60 - time.time()))
    if remaining < 90:
        raise RuntimeError('restoration reserve reached')
    argv = [PY, str(E / 'cache-replay.py'), '--output', str(E / name), '--order', order,
            '--count', str(count), '--seconds', str(remaining)]
    replay.write(E / (name + '-command.json'), {'argv': argv, 'at': time.time(),
        'hard_timeout_seconds': remaining, 'prior': str(prior) if prior else None, 'fresh': fresh})
    try:
        with (E / (name + '.log')).open('x') as log:
            subprocess.run(argv, env=env, cwd=W, stdout=log, stderr=subprocess.STDOUT,
                           timeout=remaining, check=True)
    finally:
        after = status('after')
        same_identity(before, after)
    summary = json.loads((E / name / 'summary.json').read_text())
    if summary['error'] or summary['completed'] != count:
        raise RuntimeError('phase incomplete')
    print(json.dumps({'name': name, **summary}), flush=True)

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('name'); p.add_argument('--order', choices=['forward', 'reverse'], required=True)
    p.add_argument('--count', type=int, choices=[12, 52], default=52)
    p.add_argument('--prior'); p.add_argument('--fresh', action='store_true')
    a = p.parse_args()
    if any(not x.replace('-', '').isalnum() for x in [a.name] + ([a.prior] if a.prior else [])):
        p.error('names must be simple phase identifiers')
    run(a.name, a.order, a.count, a.prior, a.fresh)
