"""Bounded HTTP-only C1/C4 decode capture fixture; no lifecycle actions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from anvil_serving.recipe_secrets import read_key
from pathlib import Path
import time

MODEL = 'glm53-flare-47581cca-tp2-c4-mtp3'

def body(index):
    return {'model': MODEL, 'messages': [{'role': 'user', 'content':
        f'Reference {index}. Write a detailed numbered list of at least 400 programming facts. Continue without a conclusion. Background notes:' + ' apple' * 7000}],
        'temperature': 1.0, 'top_p': .95, 'top_k': 20, 'seed': 42,
        'max_tokens': 1024, 'stream': True, 'return_token_ids': True,
        'return_token_timestamps': True, 'stream_options': {'include_usage': True}}

def contains_key(value, key):
    if isinstance(value, str):
        return key in value
    if isinstance(value, dict):
        return any(contains_key(k, key) or contains_key(v, key) for k, v in value.items())
    return isinstance(value, list) and any(contains_key(v, key) for v in value)

def validate(row):
    ids, stamps = row['token_ids'], row['core_ns']
    perf = row.get('performance') or {}
    ingress, enqueue = perf.get('ingress_ns'), perf.get('enqueue_ns')
    elapsed = row.get('response_end_ns', 0) - row.get('start_ns', 0)
    return (type(ingress) is int and type(enqueue) is int
        and len(stamps) == 1024 and 0 <= ingress <= enqueue <= stamps[0] <= stamps[-1]
        and 0 <= stamps[-1] - ingress <= elapsed
        and row.get('done') is True and len(ids) == len(stamps) == 1024
        and all(type(i) is int and i >= 0 for i in ids)
        and all(type(t) is int and t > 0 for t in stamps)
        and stamps == sorted(stamps) and row['finishes'] == ['length']
        and row.get('usage', {}).get('completion_tokens') == 1024
        and row.get('usage', {}).get('prompt_tokens') == 7037)

def remaining(deadline, limit=10):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError('phase deadline')
    return min(limit, value)

def request(index, deadline, output, key_file):
    key, identity = read_key(key_file)
    data = json.dumps(body(index), sort_keys=True).encode()
    row = {'index': index, 'body_sha256': hashlib.sha256(data).hexdigest(),
           'start_epoch_ns': time.time_ns(), 'start_ns': time.monotonic_ns(),
           'events': [], 'token_ids': [], 'core_ns': [], 'finishes': []}
    conn = http.client.HTTPConnection('127.0.0.1', 39130, timeout=10)
    try:
        conn.timeout = remaining(deadline)
        conn.request('POST', '/v1/chat/completions', data, {'Content-Type': 'application/json', 'Authorization': 'Bearer '+key})
        if conn.sock:
            conn.sock.settimeout(remaining(deadline))
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f'HTTP {response.status}')
        size = 0
        while True:
            wait = remaining(deadline)
            if conn.sock:
                conn.sock.settimeout(wait)
            line = response.readline((1 << 20) + 1)
            size += len(line)
            if len(line) > 1 << 20 or size > 8 << 20:
                raise ValueError('SSE size bound')
            if not line:
                break
            if not line.startswith(b'data:'):
                continue
            raw = line[5:].strip()
            if raw == b'[DONE]':
                row['done'] = True
                break
            event = json.loads(raw)
            row['events'].append({'client_ns': time.monotonic_ns(), 'event': event})
            if 'error' in event:
                raise ValueError('SSE error')
            if event.get('performance'):
                row['performance'] = event['performance']
            if event.get('usage'):
                row['usage'] = event['usage']
            for choice in event.get('choices', []):
                row['token_ids'].extend(choice.get('token_ids') or [])
                row['core_ns'].extend(choice.get('token_timestamps_ns') or [])
                if choice.get('finish_reason'):
                    row['finishes'].append(choice['finish_reason'])
        row['response_end_ns'] = time.monotonic_ns()
        if read_key(key_file)[1] != identity or contains_key(row, key):
            row = {'index': index, 'error': 'credential changed or appeared in response'}
        row['valid'] = validate(row) if 'token_ids' in row else False
    except Exception as exc:
        row['error'] = type(exc).__name__
        row['valid'] = False
    finally:
        conn.close()
        row.update(end_ns=time.monotonic_ns(), end_epoch_ns=time.time_ns())
        if contains_key(row, key):
            row = {'index': index, 'valid': False, 'error': 'credential in response rejected'}
        output.write_text(json.dumps(row) + '\n')
    return {k: v for k, v in row.items() if k not in {'events', 'token_ids', 'core_ns'}}

def metrics(deadline):
    conn = http.client.HTTPConnection('127.0.0.1', 39130, timeout=remaining(deadline, 5))
    try:
        conn.request('GET', '/metrics')
        if conn.sock:
            conn.sock.settimeout(remaining(deadline, 5))
        response = conn.getresponse()
        raw = response.read((2 << 20) + 1)
        if response.status != 200 or len(raw) > 2 << 20:
            raise ValueError('metrics unavailable')
        return raw.decode()
    finally:
        conn.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-key-file', required=True)
    parser.add_argument('--concurrency', type=int, choices=[1,4])
    parser.add_argument('--phase', choices=['warmup', 'unprofiled', 'profiled'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=55)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 90:
        raise ValueError('phase limit must be 1..90 seconds')
    args.output.mkdir(mode=0o700)
    (args.output / 'requests.json').write_text(json.dumps([body(i) for i in range(4)]))
    result = {'phase': args.phase, 'effort': 'omitted; source default Max', 'cases': [], 'start_epoch_ns': time.time_ns()}
    deadline = time.monotonic() + args.seconds
    try:
        for repeat in range(1):
            for concurrency in ((args.concurrency,) if args.concurrency else (1, 4)):
                remaining(deadline)
                case = {'repeat': repeat, 'concurrency': concurrency, 'start_epoch_ns': time.time_ns(), 'metrics_before': metrics(deadline)}
                with ThreadPoolExecutor(concurrency) as pool:
                    futures = [pool.submit(request, i, deadline, args.output / f'r{repeat}-c{concurrency}-i{i}.json', args.api_key_file) for i in range(concurrency)]
                    case['requests'] = [f.result() for f in futures]
                case.update(end_epoch_ns=time.time_ns(), metrics_after=metrics(deadline))
                result['cases'].append(case)
                if not all(r['valid'] for r in case['requests']):
                    raise ValueError('request or workload validation failed')
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['end_epoch_ns'] = time.time_ns()
        (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'phase': args.phase, 'cases': len(result['cases']), 'error': result.get('error')}), flush=True)
    return int('error' in result)

if __name__ == '__main__':
    raise SystemExit(main())
