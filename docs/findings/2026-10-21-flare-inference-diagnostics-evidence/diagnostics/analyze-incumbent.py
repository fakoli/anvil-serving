"""Offline request/token reconciliation; retain contamination rather than hide it."""
import hashlib
import json
import math
from pathlib import Path

E = Path(__file__).parent


def total(raw, name):
    values = [float(line.rsplit(' ', 1)[1]) for line in raw.splitlines()
              if line.startswith(name + '{') or line.startswith(name + ' ')]
    if not values or not all(math.isfinite(v) and v >= 0 for v in values):
        raise ValueError('absent or invalid counter: ' + name)
    return sum(values)


def snapshot(path):
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('source changed during read: ' + path.name)
    return raw.decode(), {'path': path.name, 'sha256': hashlib.sha256(raw).hexdigest()}


if __name__ == '__main__':
    assert total('x{a="0"} 2\nx{a="1"} 3\nx_other 99', 'x') == 5
    rows = []
    for arm in ('incumbent-before', 'incumbent-after'):
        for c in (1, 4):
            for rep in ('prime', 'r1', 'r2', 'r3'):
                name = f'{arm}-c{c}-{rep}'
                files = [E / (name + suffix) for suffix in ('.json', '-before-native.prom', '-after-native.prom')]
                snapshots = [snapshot(p) for p in files]
                run = json.loads(snapshots[0][0])
                before, after = (item[0] for item in snapshots[1:])
                if run['failed'] != len(run['failures']) or run['requests'] != run['completed'] + run['failed']:
                    raise ValueError('invalid request population: ' + name)
                failed_usage = [failure['response_metadata']['usage'] for failure in run['failures']]
                expected = {'vllm:request_success_total': run['requests'],
                            'vllm:prompt_tokens_total': run['metrics']['prompt_tokens'] + sum(u['prompt_tokens'] for u in failed_usage),
                            'vllm:generation_tokens_total': run['metrics']['output_tokens'] + sum(u['completion_tokens'] for u in failed_usage)}
                deltas = {k: total(after, k) - total(before, k) for k in expected}
                if any(v < 0 for v in deltas.values()):
                    raise ValueError('counter reset: ' + name)
                prefix_hits = total(after, 'vllm:prefix_cache_hits_total') - total(before, 'vllm:prefix_cache_hits_total')
                if prefix_hits < 0:
                    raise ValueError('prefix counter reset: ' + name)
                rows.append({'name': name, 'expected': expected, 'native_deltas': deltas,
                             'reconciled': expected == deltas, 'failed_requests': run['failed'],
                             'prefix_hits': prefix_hits,
                             'sources': [item[1] for item in snapshots]})
    result = {'rows': rows, 'all_reconciled': all(row['reconciled'] for row in rows),
              'limits': ['Reconciliation checks request/token counts, not semantic quality or absence of unrelated host work',
                         'Failed request usage remains in the attempted population; no survivor-only ranking'],
              'analyzer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with (E / 'incumbent-counter-reconciliation.json').open('x') as out:
        out.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'blocks': len(rows), 'all_reconciled': result['all_reconciled']}))
