"""HTTP-only concurrent prefill/decode diagnostic against one loaded model."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import urllib.request
from urllib.parse import urlsplit


def valid_prefill(reply, lines):
    if not isinstance(reply, dict) or 'error' in reply:
        return False
    choices, usage = reply.get('choices'), reply.get('usage')
    return (isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
            and choices[0].get('finish_reason') in ('stop', 'length')
            and isinstance(choices[0].get('message'), dict) and isinstance(usage, dict)
            and type(usage.get('prompt_tokens')) is int and lines * 10 <= usage['prompt_tokens'] <= lines * 30
            and usage.get('completion_tokens') == 1 and reply.get('reused_prompt_tokens', 0) <= 64)


def overlap_observation(events, start, end):
    arrivals = []
    tokens = 0
    for row in events:
        count = sum(len(c.get('token_ids') or []) for c in row['event'].get('choices', []))
        if count and start < row['received_ns'] < end:
            arrivals.append(row['received_ns'])
            tokens += count
    edges = [start, *arrivals, end]
    return {'token_events_inside_prefill': len(arrivals), 'tokens_delivered_inside_prefill': tokens,
            'maximum_client_receive_gap_ms': max(b - a for a, b in zip(edges, edges[1:])) / 1e6,
            'timing_scope': 'client receipt window; native timestamp origin is not client monotonic origin'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--nonce', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError('explicit loopback HTTP origin required')
    os.close(os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
    results = {'configuration': vars(args) | {'output': str(args.output)}, 'cases': []}
    deadline = time.monotonic() + 600

    def timeout(limit):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('workload deadline')
        return min(limit, remaining)

    def save():
        if args.output.is_symlink():
            raise ValueError('output became a symlink')
        with tempfile.NamedTemporaryFile(mode='w', dir=args.output.parent, delete=False) as h:
            h.write(json.dumps(results, indent=2) + '\n')
            h.flush()
            os.fsync(h.fileno())
        os.replace(h.name, args.output)

    def get(path):
        with urllib.request.urlopen(args.url + path, timeout=timeout(10)) as response:
            raw = response.read((2 << 20) + 1)
        if len(raw) > 2 << 20:
            raise ValueError('metrics response too large')
        return json.loads(raw) if '?' in path else raw.decode()

    def body(prompt, maximum, stream=False):
        return {'model': args.model, 'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 1.0, 'top_p': .95, 'seed': 1234, 'max_tokens': maximum,
                'stream': stream, 'return_token_ids': True, 'return_token_timestamps': True,
                **({'stream_options': {'include_usage': True}} if stream else {})}

    def stream(prompt, progress, row):
        conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout(180))
        row.update(start_ns=time.monotonic_ns(), events=[], content='', token_ids=[], native_timestamps_ns=[])
        total = 0
        try:
            conn.request('POST', '/v1/chat/completions', json.dumps(body(prompt, 4096, True)), {'Content-Type': 'application/json'})
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError('stream HTTP ' + str(response.status))
            while True:
                timeout(180)
                line = response.readline((1 << 20) + 1)
                total += len(line)
                if len(line) > 1 << 20 or total > 8 << 20:
                    raise ValueError('SSE evidence size bound')
                if not line:
                    break
                if not line.startswith(b'data:'):
                    continue
                data = line[5:].strip()
                if data == b'[DONE]':
                    row['done'] = True
                    break
                event = json.loads(data)
                row['events'].append({'received_ns': time.monotonic_ns(), 'event': event})
                if 'error' in event:
                    raise ValueError('model SSE error')
                for choice in event.get('choices', []):
                    row['content'] += (choice.get('delta') or {}).get('content') or ''
                    row['token_ids'].extend(choice.get('token_ids') or [])
                    row['native_timestamps_ns'].extend(choice.get('token_timestamps_ns') or [])
                    if choice.get('finish_reason'):
                        row['finish_reason'] = choice['finish_reason']
                if event.get('usage'):
                    row['usage'] = event['usage']
                if len(row['token_ids']) >= 64:
                    progress.set()
        finally:
            conn.close()
            row['end_ns'] = time.monotonic_ns()

    try:
        for lines in (0, 2048, 6144):
            before = get('/metrics?format=json')
            if before['active'] or before['pending']:
                raise ValueError('model not idle before controlled case')
            case = {'prefill_lines': lines, 'before': before, 'prom_before': get('/metrics'), 'decode': {}}
            results['cases'].append(case)
            save()
            progress = threading.Event()
            prompt = f'Reference {args.nonce}-{lines}. Print every integer from 1 through 5000 in order, separated by commas. No commentary or summary. Do not stop early.'
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(stream, prompt, progress, case['decode'])
                if not progress.wait(timeout=timeout(30)) or future.done():
                    raise ValueError('did not establish live decode before probe')
                case['overlap_start'] = get('/metrics?format=json')
                if lines:
                    if case['overlap_start']['active'] < 1:
                        raise ValueError('decode no longer active before fresh prefill')
                    prefix = f'Fresh reference {args.nonce}-{lines}.\n' + '\n'.join(
                        f'Record {i}: red green blue silver black gold white amber teal violet.' for i in range(lines))
                    request = body(prefix + '\nReply READY.', 1)
                    case['prefill'] = {'request': request, 'send_start_ns': time.monotonic_ns()}
                    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout(180))
                    try:
                        conn.request('POST', '/v1/chat/completions', json.dumps(request).encode(), {'Content-Type': 'application/json'})
                        case['prefill']['start_ns'] = time.monotonic_ns()
                        response = conn.getresponse()
                        if response.status != 200:
                            raise ValueError('prefill HTTP ' + str(response.status))
                        raw = response.read((4 << 20) + 1)
                    finally:
                        conn.close()
                    if len(raw) > 4 << 20:
                        raise ValueError('prefill response too large')
                    reply = json.loads(raw)
                    case['prefill'].update(response=reply, end_ns=time.monotonic_ns())
                    if not valid_prefill(reply, lines):
                        raise ValueError('prefill response/usage/cold treatment invalid')
                future.result(timeout=timeout(180))
            row = case['decode']
            stamps = row['native_timestamps_ns']
            visible = row['content'].rsplit(',', 1)[0] if row.get('finish_reason') == 'length' else row['content']
            numbers = [int(n) for n in re.findall(r'\d+', visible)]
            case['checks'] = {
                'stream_completed': row.get('done') is True,
                'token_timestamps': len(stamps) == len(row['token_ids']) >= 128 and stamps == sorted(stamps),
                'usage_matches_ids': row.get('usage', {}).get('completion_tokens') == len(row['token_ids']),
                'complete_visible_numbers_in_order': len(numbers) >= 100 and numbers == list(range(1, len(numbers) + 1)),
            }
            if lines:
                prefill = case['prefill']
                case['overlap'] = overlap_observation(row['events'], prefill['start_ns'], prefill['end_ns'])
                case['checks']['decode_progress_during_prefill'] = case['overlap']['tokens_delivered_inside_prefill'] > 0
                case['checks']['decode_client_spans_prefill'] = row['end_ns'] >= prefill['end_ns']
                earlier = [c['prefill']['response']['usage']['prompt_tokens'] for c in results['cases'][:-1] if 'prefill' in c]
                case['checks']['prefill_sizes_ordered'] = not earlier or prefill['response']['usage']['prompt_tokens'] > max(earlier)
            case['after'] = get('/metrics?format=json')
            case['prom_after'] = get('/metrics')
            save()
    except Exception as exc:
        results['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        save()
        raise
    print(json.dumps([{'prefill_lines': c['prefill_lines'], 'checks': c['checks']} for c in results['cases']]))
    return 0 if all(all(c['checks'].values()) for c in results['cases']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
