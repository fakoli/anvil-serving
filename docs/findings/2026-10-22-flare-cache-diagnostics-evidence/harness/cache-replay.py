"""Bounded HTTP-only diagnostic fixture. Lifecycle stays in Anvil Serving."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request

from anvil_serving.benchmarking.requests import build_body, stream_chat
from anvil_serving.recipe_secrets import read_key

BASE = 'http://127.0.0.1:39130'
MODEL = 'glm53-flare-47581cca-tp2-c4-mtp3'
CORPUS = Path('OPERATOR_PATH_a11e61aa1b2c/corpus-c1.json')
KEY = 'OPERATOR_PATH_6f3ccf38bed5/flare.key'
OPTIONS = dict(max_tokens=2048, reasoning_effort='max', temperature=1, top_p=.95)
COUNTERS = ('flare_requests_completed_total', 'flare_requests_cancelled_total',
            'flare_prefill_tokens_total', 'flare_generated_tokens_total',
            'flare_cache_reused_tokens_total', 'flare_host_cache_reused_tokens_total',
            'flare_host_cache_stored_total', 'flare_host_cache_fetched_total',
            'flare_host_cache_store_failed_total', 'flare_host_cache_fetch_failed_total')
COUNTERS += ('flare_host_cache_lookups_total', 'flare_host_cache_hits_total', 'flare_cache_hits_total')
IDLE = ('flare_active_requests', 'flare_pending_requests', 'flare_retiring_request_slots',
        'flare_host_cache_outbound_in_flight', 'flare_host_cache_inbound_in_flight',
        'flare_host_cache_inbound_waiting')
IDLE_TIMEOUT = 10

def digest(data):
    return hashlib.sha256(data).hexdigest()

def write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')

def protect(value, key):
    if isinstance(value, str) and key in value:
        raise ValueError('protected content suppressed')
    if isinstance(value, dict):
        for name, item in value.items():
            protect(name, key); protect(item, key)
    if isinstance(value, list):
        for item in value:
            protect(item, key)

def metrics(key):
    with urllib.request.urlopen(BASE + '/metrics', timeout=5) as response:
        raw = response.read(4 * 1024 * 1024 + 1).decode()
    protect(raw, key)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('metrics exceed bound')
    values = {line.split()[0]: float(line.split()[-1]) for line in raw.splitlines()
              if line and not line.startswith('#') and '{' not in line.split()[0]}
    for name in (*COUNTERS, *IDLE, 'flare_uptime_seconds'):
        if name not in values:
            raise ValueError('missing metric ' + name)
    return raw, values

def idle(key):
    end = time.monotonic() + IDLE_TIMEOUT
    previous = None
    while True:
        raw, values = metrics(key)
        current = tuple(values[name] for name in COUNTERS)
        if all(values[name] == 0 for name in IDLE) and current == previous:
            return raw, values
        if time.monotonic() >= end:
            raise RuntimeError('endpoint/cache did not drain')
        previous = current if all(values[name] == 0 for name in IDLE) else None
        time.sleep(.2)

def corpus(order, count):
    rows = json.loads(CORPUS.read_text())
    if len(rows) != 52 or [r['index'] for r in rows] != list(range(52)):
        raise ValueError('unexpected corpus identity')
    for row in rows:
        if digest(row['prompt'].encode()) != row['sha256']:
            raise ValueError('corpus prompt hash mismatch')
    rows = rows[-count:]
    return rows[::-1] if order == 'reverse' else rows

def run(output, order, count, seconds):
    rows = corpus(order, count)
    output.mkdir(mode=0o700)  # Existing evidence must never be overwritten.
    key, key_identity = read_key(KEY)
    request = urllib.request.Request(BASE + '/v1/models', headers={'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(request, timeout=5) as response:
        identity = json.loads(response.read(1048576))
    protect(identity, key)
    if [x['id'] for x in identity['data']] != [MODEL]:
        raise RuntimeError('served model identity mismatch')
    write(output / 'identity.json', identity)
    write(output / 'plan.json', {'order': order, 'count': count, 'seconds': seconds,
          'corpus_sha256': digest(CORPUS.read_bytes()), 'options': OPTIONS,
          'indices': [r['index'] for r in rows], 'at': time.time()})
    results, failure = [], None
    deadline = time.monotonic() + seconds
    try:
        for sequence, row in enumerate(rows):
            if deadline - time.monotonic() < 75:
                raise TimeoutError('phase completion reserve reached')
            if read_key(KEY)[1] != key_identity:
                raise RuntimeError('key file identity changed')
            raw, before = idle(key)
            (output / f'{sequence:03}-before.prom').write_text(raw)
            if results and before['flare_uptime_seconds'] < results[-1]['uptime_after']:
                raise RuntimeError('process uptime regressed')
            body = build_body(MODEL, row['prompt'], **OPTIONS)
            body_bytes = json.dumps(body).encode()
            (output / f'{sequence:03}-body.json').write_bytes(body_bytes)
            events, captured = [], 0
            def observe(kind, value):
                nonlocal captured
                protect(value, key)
                if kind in {'malformed', 'server_error'}:
                    raise ValueError('invalid upstream stream: ' + kind)
                encoded = json.dumps({'kind': kind, 'value': value, 'at': time.time()})
                captured += len(encoded)
                if captured > 512 * 1024:
                    raise ValueError('response capture limit exceeded')
                if key in encoded:
                    raise ValueError('response contains protected content')
                events.append(encoded)
            started = time.time()
            try:
                result = stream_chat(BASE + '/v1', MODEL, row['prompt'], key,
                    timeout=10, deadline_seconds=60, observer=observe, **OPTIONS)
            finally:
                (output / f'{sequence:03}-events.jsonl').write_text('\n'.join(events) + '\n')
            protect(result, key)
            write(output / f'{sequence:03}-response.json', result)
            if (not result['stream_terminal_observed'] or not result.get('usage')
                    or result['output_token_source'] != 'usage'
                    or not result['finish_reasons']
                    or set(result['finish_reasons']) - {'stop', 'length'}):
                raise ValueError('terminal/usage contract failed')
            raw, after = idle(key)
            (output / f'{sequence:03}-after.prom').write_text(raw)
            delta = {k: after[k] - before[k] for k in COUNTERS}
            if (after['flare_uptime_seconds'] < before['flare_uptime_seconds']
                    or any(v < 0 for v in delta.values())
                    or delta['flare_requests_completed_total'] != 1
                    or delta['flare_requests_cancelled_total'] != 0
                    or delta['flare_host_cache_store_failed_total'] != 0
                    or delta['flare_host_cache_fetch_failed_total'] != 0):
                raise RuntimeError('counter reset, request interference or cache failure')
            prompt_accounted = sum(delta[k] for k in ('flare_prefill_tokens_total',
                'flare_cache_reused_tokens_total', 'flare_host_cache_reused_tokens_total'))
            if prompt_accounted != result['usage']['prompt_tokens']:
                raise RuntimeError('prompt denominator mismatch')
            results.append({'sequence': sequence, 'index': row['index'], 'started': started,
                'ended': time.time(), 'body_sha256': digest(body_bytes),
                'prompt_sha256': row['sha256'], 'deltas': delta, 'usage': result['usage'],
                'visible': result['ttft'] is not None, 'finish_reasons': result['finish_reasons'],
                'ttfo': result['time_to_first_output'], 'ttft': result['ttft'],
                'e2e': result['e2e'], 'uptime_after': after['flare_uptime_seconds'],
                'host_records': after.get('flare_host_cache_memory_records'),
                'host_bytes': after.get('flare_host_cache_memory_bytes')})
            write(output / 'results.json', results)
            print(json.dumps({'sequence': sequence, 'index': row['index'],
                  'host_reuse': delta['flare_host_cache_reused_tokens_total'],
                  'gpu_reuse': delta['flare_cache_reused_tokens_total'],
                  'visible': result['ttft'] is not None}), flush=True)
    except Exception as error:
        failure = type(error).__name__ + ': ' + str(error)
        if key in failure:
            failure = 'protected error omitted'
        raise
    finally:
        write(output / 'summary.json', {'completed': len(results), 'expected': count,
              'visible': sum(r['visible'] for r in results), 'error': failure,
              'performance_eligible': False, 'semantic_quality': 'not_evaluated'})

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--order', choices=['forward', 'reverse'], required=True)
    parser.add_argument('--count', type=int, choices=[12, 52], default=52)
    parser.add_argument('--seconds', type=int, default=1500)
    args = parser.parse_args()
    if not 75 <= args.seconds <= 1800:
        parser.error('seconds must be 75..1800')
    run(args.output, args.order, args.count, args.seconds)
