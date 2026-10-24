"""Bounded HTTP-only validation fixture; never starts or stops a service."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--nonce', required=True)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError('explicit loopback HTTP origin required')
    os.close(os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600))
    deadline = time.monotonic() + 600
    evidence = {'configuration': vars(args) | {'output': str(args.output), 'prefill_chunk_size': 4096},
                'requests': [], 'checks': [], 'metrics': {}}

    def save():
        if args.output.is_symlink():
            raise ValueError('output replaced by symlink')
        with tempfile.NamedTemporaryFile(mode='w', dir=args.output.parent, delete=False) as handle:
            handle.write(json.dumps(evidence, indent=2) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, args.output)

    def timeout(limit):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('600-second workload deadline reached')
        return min(limit, remaining)

    def read_bounded(response, limit):
        raw = response.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('HTTP response exceeds evidence size bound')
        return raw

    def check(name, passed, **details):
        evidence['checks'].append({'name': name, 'passed': bool(passed), **details})
        save()

    def metrics(label, wait_idle=True):
        idle_deadline = time.monotonic() + 30
        while True:
            with urllib.request.urlopen(args.url + '/metrics', timeout=timeout(10)) as response:
                text = read_bounded(response, 2 << 20).decode()
            values = {k: float(v) for k, v in re.findall(r'^(flare_[^\s]+) ([0-9.eE+-]+)$', text, re.M)}
            required = ('flare_active_requests', 'flare_pending_requests', 'flare_retiring_request_slots', 'flare_free_snapshots',
                        'flare_cached_checkpoints', 'flare_requests_failed_total',
                        'flare_host_cache_hits_total', 'flare_host_cache_fetched_total',
                        'flare_host_cache_fetch_failed_total', 'flare_snapshot_evictions_total',
                        'flare_host_cache_outbound_in_flight', 'flare_host_cache_inbound_in_flight',
                        'flare_cache_hits_total', 'flare_graph_launches_total{kind="restore"}',
                        'flare_graph_launches_total{kind="init"}')
            if any(key not in values for key in required):
                raise ValueError('required model metrics unavailable')
            busy = sum(values.get(k, 0) for k in (
                'flare_active_requests', 'flare_pending_requests', 'flare_retiring_request_slots',
                'flare_host_cache_outbound_in_flight', 'flare_host_cache_inbound_in_flight'))
            if not busy or not wait_idle:
                if wait_idle:
                    evidence['metrics'][label] = values
                    save()
                return values
            if time.monotonic() >= idle_deadline:
                raise TimeoutError('model/cache transfers did not drain')
            time.sleep(.05)

    def request(label, messages, tokens=8192, seed=1234):
        body = {'model': args.model, 'messages': messages,
                'temperature': 1.0, 'top_p': .95, 'seed': seed, 'max_tokens': tokens}
        started = time.monotonic()
        req = urllib.request.Request(args.url + '/v1/chat/completions',
                                     json.dumps(body).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout(15 if tokens == 1 else 180)) as response:
            reply = json.loads(read_bounded(response, 4 << 20))
        return {'label': label, 'request': body, 'response': reply,
                'started_monotonic': started, 'ended_monotonic': time.monotonic(),
                'elapsed_s': time.monotonic() - started}

    facts = '\n'.join(f'Fact {i}: value {i * 7919 % 1009}.' for i in range(768))
    prefix = f'Reference {args.nonce}.\n{facts}\n'
    messages = [{'role': 'user', 'content': prefix + 'Reply only READY.'}]
    indices = [3, 83, 169, 257, 341, 427, 511, 601, 733]

    def query(selected):
        return messages + [{'role': 'user', 'content': 'Return only the values of facts ' + ', '.join(map(str, selected)) + ', in that order, as comma-separated integers.'}]

    def record(row, selected=None):
        evidence['requests'].append(row)
        save()
        choice = row['response']['choices'][0]
        if selected is not None:
            text = choice['message'].get('content') or ''
            got = [int(n) for n in re.findall(r'\d+', text)]
            expected = [i * 7919 % 1009 for i in selected]
            check(row['label'] + '.facts', got == expected and choice['finish_reason'] == 'stop',
                  expected=expected, observed=got)
        return row['response']

    def evict(label):
        before = metrics(label + '-before')
        capacity = before['flare_free_snapshots'] + before['flare_cached_checkpoints']
        if not isinstance(capacity, (int, float)) or not capacity.is_integer() or not 2 <= capacity <= 128:
            raise ValueError('bounded GPU snapshot capacity unavailable')
        for i in range(int(capacity) + 1):
            record(request(label + '-' + str(i), [{'role': 'user', 'content': f'Filler {args.nonce} {label} {i}. Reply OK.'}], tokens=1))
        after = metrics(label + '-after')
        check(label + '.eviction', after.get('flare_snapshot_evictions_total', 0) > before.get('flare_snapshot_evictions_total', 0))

    try:
        initial = metrics('initial')
        cold = record(request('cold', messages))
        answer = cold['choices'][0]['message'].get('content') or ''
        check('cold.ready', answer.strip() == 'READY' and cold['choices'][0]['finish_reason'] == 'stop')
        messages.append({'role': 'assistant', 'content': answer})
        before = metrics('gpu-before')
        record(request('gpu', query(indices)), indices)
        after = metrics('gpu-after')
        check('gpu.restore', after['flare_cache_hits_total'] - before['flare_cache_hits_total'] == 1
              and after['flare_host_cache_hits_total'] == before['flare_host_cache_hits_total']
              and after['flare_graph_launches_total{kind="init"}'] == before['flare_graph_launches_total{kind="init"}']
              and after['flare_graph_launches_total{kind="restore"}'] - before['flare_graph_launches_total{kind="restore"}'] == 1)
        evict('evict-host')
        before = metrics('host-before')
        restored = record(request('host', query(indices)), indices)
        after = metrics('host-after')
        check('host.fetch', after.get('flare_host_cache_hits_total', 0) - before.get('flare_host_cache_hits_total', 0) == 1,
              reused_prompt_tokens=restored.get('reused_prompt_tokens'), prompt_tokens=cold['usage']['prompt_tokens'])
        check('host.restored-not-recomputed', after['flare_host_cache_fetched_total'] > before['flare_host_cache_fetched_total']
              and after['flare_host_cache_fetch_failed_total'] == before['flare_host_cache_fetch_failed_total']
              and after['flare_graph_launches_total{kind="restore"}'] - before['flare_graph_launches_total{kind="restore"}'] == 1
              and after['flare_graph_launches_total{kind="init"}'] == before['flare_graph_launches_total{kind="init"}'])
        check('host.reused-prefix', restored.get('reused_prompt_tokens', 0) >= ((cold['usage']['prompt_tokens'] - 1) // 4096) * 4096)
        evict('evict-branches')
        before = metrics('branches-before')
        selected = [[i + offset for i in indices] for offset in range(1, 5)]
        peak_active = 0
        samples = {}
        with ThreadPoolExecutor(4) as pool:
            futures = [pool.submit(request, 'branch-' + str(i), query(values), seed=1300 + i) for i, values in enumerate(selected)]
            while not all(f.done() for f in futures):
                observed = metrics('branches-inflight', wait_idle=False)
                samples.setdefault('first', observed)
                samples['last'] = observed
                if observed['flare_active_requests'] > peak_active:
                    samples['peak'] = observed
                peak_active = max(peak_active, observed['flare_active_requests'])
                time.sleep(.02)
            rows = [f.result() for f in futures]
        evidence['branch_samples'] = samples
        for row, values in zip(rows, selected):
            record(row, values)
        check('branches.client-overlap', max(row['started_monotonic'] for row in rows) < min(row['ended_monotonic'] for row in rows))
        check('branches.four-active-observed', peak_active == 4, observed_peak_active=peak_active)
        after = metrics('final')
        check('branches.host-fetch', after.get('flare_host_cache_fetched_total', 0) > before.get('flare_host_cache_fetched_total', 0))
        hits = sum(after[k] - before[k] for k in ('flare_host_cache_hits_total', 'flare_cache_hits_total'))
        minimum_reuse = ((cold['usage']['prompt_tokens'] - 1) // 4096) * 4096
        check('branches.restored-not-recomputed', hits == 4
              and after['flare_graph_launches_total{kind="init"}'] == before['flare_graph_launches_total{kind="init"}']
              and 1 <= after['flare_graph_launches_total{kind="restore"}'] - before['flare_graph_launches_total{kind="restore"}'] <= 4
              and all(row['response'].get('reused_prompt_tokens', 0) >= minimum_reuse for row in rows), cache_hits=hits)
        check('no-request-failures', after.get('flare_requests_failed_total', 0) == initial.get('flare_requests_failed_total', 0))
        check('no-host-fetch-failures', after.get('flare_host_cache_fetch_failed_total', 0) == initial.get('flare_host_cache_fetch_failed_total', 0))
    except Exception as exc:
        evidence['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        save()
        raise
    print(json.dumps({'checks': evidence['checks'], 'requests': len(evidence['requests'])}))
    return 0 if all(c['passed'] for c in evidence['checks']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
