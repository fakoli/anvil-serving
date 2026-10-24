"""Offline arithmetic over retained cache probes and fixed-length trace controls."""
import hashlib
import argparse
import json
import math
from pathlib import Path
from statistics import median

E = Path(__file__).parent
SOURCES = {}
COUNTERS = {'flare_'+n+'_total' for n in (
    'requests_completed', 'requests_cancelled', 'prefill_tokens', 'generated_tokens',
    'cache_reused_tokens', 'host_cache_reused_tokens', 'host_cache_stored',
    'host_cache_fetched', 'host_cache_store_failed', 'host_cache_fetch_failed',
    'host_cache_lookups', 'host_cache_hits', 'cache_hits')}

def read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('unsafe or oversized evidence input')
    before = path.stat()
    value = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('evidence changed during read')
    SOURCES[str(path.relative_to(E))] = hashlib.sha256(value).hexdigest()
    return value.decode()

def data(path):
    return json.loads(read(path))

def metrics(text):
    result = {}
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        key, value = line.rsplit(' ', 1)
        value = float(value)
        if key in result or not math.isfinite(value) or value < 0:
            raise ValueError('duplicate, nonfinite or negative metric: ' + key)
        result[key] = value
    return result

def deltas(before, after):
    if set(before) != set(after):
        raise ValueError('metric population changed')
    result = {k: after[k]-v for k,v in before.items() if k.split('{')[0].endswith('_total')}
    if any(v < 0 for v in result.values()):
        raise ValueError('counter regression')
    return result

def binding(path):
    read(path)
    return {'path': str(path.relative_to(E)),
            'sha256': SOURCES[str(path.relative_to(E))]}

def require_complete(rows):
    if any(r['status'] != 'complete' for r in rows.values()):
        raise ValueError('final analysis requires all declared cache phases')

def cache(path):
    if not (path / 'summary.json').exists():
        return {'status': 'not_collected'}
    summary, rows, plan = [data(path / n) for n in ['summary.json','results.json','plan.json']]
    count = 12 if 'small' in path.name else 52
    indices = list(range(52-count, 52))
    if 'reverse' in path.name:
        indices.reverse()
    if (summary['error'] or summary['completed'] != count or summary['expected'] != count
            or len(rows) != count or plan['count'] != count or plan['indices'] != indices
            or plan['order'] != ('reverse' if 'reverse' in path.name else 'forward')
            or [r['index'] for r in rows] != indices
            or [r['sequence'] for r in rows] != list(range(count))):
        raise ValueError('incomplete or reordered cache population: ' + path.name)
    values = []
    for suffix in ['before.prom','after.prom','body.json','response.json','events.jsonl']:
        if {p.name for p in path.glob('*-'+suffix)} != {f'{i:03}-{suffix}' for i in range(count)}:
            raise ValueError('cache artifact population mismatch: ' + suffix)
    for i,row in enumerate(rows):
        if set(row['deltas']) != COUNTERS:
            raise ValueError('cache counter schema mismatch')
        before_i = metrics(read(path / f'{i:03}-before.prom'))
        after_i = metrics(read(path / f'{i:03}-after.prom'))
        actual = deltas(before_i, after_i)
        if any(actual[k] != v for k,v in row['deltas'].items()):
            raise ValueError('retained cache delta mismatch')
        body = read(path / f'{i:03}-body.json')
        if hashlib.sha256(body.encode()).hexdigest() != row['body_sha256']:
            raise ValueError('cache body hash mismatch')
        read(path / f'{i:03}-response.json'); read(path / f'{i:03}-events.jsonl')
        values.append(after_i)
    before = metrics(read(path / '000-before.prom'))
    deltas(before, values[-1])
    result = {'status': 'complete',
              'source': binding(path / 'results.json'), 'requests': len(rows),
              'visible': sum(r['visible'] for r in rows),
              'host_restored_requests': sum(r['deltas']['flare_host_cache_fetched_total'] > 0 for r in rows),
              'full_gpu_hits': sum(r['deltas']['flare_cache_reused_tokens_total'] == r['usage']['prompt_tokens'] for r in rows),
              'prompt_tokens_range': [min(r['usage']['prompt_tokens'] for r in rows), max(r['usage']['prompt_tokens'] for r in rows)],
              'completion_tokens_range': [min(r['usage']['completion_tokens'] for r in rows), max(r['usage']['completion_tokens'] for r in rows)],
              'counter_deltas': {k: sum(r['deltas'][k] for r in rows) for k in rows[0]['deltas']},
              'end_host_records': rows[-1]['host_records'], 'end_host_bytes': rows[-1]['host_bytes']}
    names = [k for k in values[-1] if k.startswith('flare_host_cache_memory_')]
    result['pool_metrics'] = {k: {'first': before.get(k), 'last': values[-1][k],
        'post_request_min': min(v[k] for v in values), 'post_request_max': max(v[k] for v in values),
        'delta': values[-1][k] - before[k] if k.endswith('_total') else None} for k in names}
    return result

def decode(cell):
    path = E / cell['name']
    source = path / 'summary.json'
    summary = data(source)
    case, = summary['cases']
    c = cell['concurrency']
    files = sorted(path.glob('r*-i*.json'))
    if {p.name for p in files} != {f'r0-c{c}-i{i}.json' for i in range(c)}:
        raise ValueError('decode artifact population mismatch')
    rows = [data(p) for p in files]
    bodies = data(path / 'requests.json')
    if (summary.get('error') or case['concurrency'] != c or case['repeat'] != 0
            or len(rows) != c or len(bodies) != 4
            or [r['index'] for r in rows] != list(range(c))
            or sorted(r['index'] for r in case['requests']) != list(range(c))
            or not all(r['valid'] and r['done'] and r['finishes'] == ['length']
                and r['usage']['prompt_tokens'] == 7037 and r['usage']['completion_tokens'] == 1024
                and len(r['token_ids']) == 1024 and len(r['core_ns']) == 1024
                and all(b >= a for a,b in zip(r['core_ns'], r['core_ns'][1:]))
                and r['core_ns'][-1] > r['core_ns'][0] for r in rows)):
        raise ValueError('incomplete fixed-length fixture')
    for row, body in zip(rows,bodies):
        clocks = row['core_ns'] + [row['performance'][k] for k in ['ingress_ns','enqueue_ns']]
        clocks += [row[k] for k in ['start_ns','response_end_ns','end_ns','start_epoch_ns','end_epoch_ns']]
        if (not all(type(t) is int and t >= 0 for t in clocks)
                or not all(type(t) is int and t >= 0 for t in row['token_ids'])
                or not row['performance']['ingress_ns'] <= row['performance']['enqueue_ns'] <= row['core_ns'][0]
                or not row['start_ns'] <= row['response_end_ns'] <= row['end_ns']
                or row['end_epoch_ns'] < row['start_epoch_ns']
                or row['core_ns'][-1] - row['performance']['ingress_ns'] > row['response_end_ns'] - row['start_ns']):
            raise ValueError('invalid process/client timestamp domain')
        if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != row['body_sha256']:
            raise ValueError('decode body hash mismatch')
        summary_row = next(r for r in case['requests'] if r['index'] == row['index'])
        if summary_row != {k:v for k,v in row.items() if k not in {'events','token_ids','core_ns'}}:
            raise ValueError('decode summary/raw provenance mismatch')
    before, after = metrics(case['metrics_before']), metrics(case['metrics_after'])
    change = deltas(before, after)
    return {'source': binding(source), 'requests': len(rows),
            'all_prompt_cached': all(r['usage']['prompt_tokens_details']['cached_tokens'] == 7037 for r in rows),
            'body_hashes': [r['body_sha256'] for r in rows],
            'token_hashes': [hashlib.sha256(json.dumps(r['token_ids']).encode()).hexdigest() for r in rows],
            'core_decode_seconds': [(r['core_ns'][-1] - r['core_ns'][0]) / 1e9 for r in rows],
            'median_core_decode_seconds': median((r['core_ns'][-1] - r['core_ns'][0]) / 1e9 for r in rows),
            'speculative_deltas': {k:v for k,v in change.items()
                if k.startswith(('flare_speculative_rounds_total', 'flare_speculative_drafts_total')) and v}}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--final', action='store_true')
    args = parser.parse_args()
    if metrics('# HELP x x\nx 2\nx{a="b"} 3\n') != {'x': 2, 'x{a="b"}': 3}:
        raise ValueError('parser check failed')
    read(Path(__file__))
    names = ['a-prime','a-reverse','b-prime','b-forward','b-small-prime','b-small-repeat']
    names += ['counter-arm/'+n for n in ['c-prime','c-reverse','d-prime','d-forward']]
    result = {'schema': 'flare-cache-diagnostic-summary/v1', 'performance_eligible': False,
              'semantic_quality': 'not_evaluated', 'promoted': False,
              'cache': {n: cache(E / n) for n in names}, 'decode': {}, 'pairs': []}
    if args.final:
        require_complete(result['cache'])
    cells = data(E / 'decode-order-plan.json')['cells']
    if len(cells) != 14 or len({c['name'] for c in cells}) != 14:
        raise ValueError('decode plan population mismatch')
    for cell in cells:
        result['decode'][cell['name']] = decode(cell)
    for c in [1, 4]:
        for repeat in [1, 2, 3]:
            off, on = [result['decode'][f'decode-c{c}-r{repeat}-{mode}'] for mode in ['off','on']]
            if off['body_hashes'] != on['body_hashes'] or not off['all_prompt_cached'] or not on['all_prompt_cached']:
                raise ValueError('paired workload mismatch')
            result['pairs'].append({'concurrency': c, 'repeat': repeat,
                'identical_token_sequences': off['token_hashes'] == on['token_hashes'],
                'identical_request_token_sequences': sum(a == b for a,b in zip(off['token_hashes'], on['token_hashes'])),
                'paired_requests': len(off['token_hashes']),
                'on_vs_off_median_core_time_percent': 100*(on['median_core_decode_seconds']/off['median_core_decode_seconds']-1),
                'causal_overhead_eligible': False})
    result['limits'] = ['Cache phases are functional diagnostics, not throughput or semantic-quality gates.',
        'One fresh pair per order per image; no repeated policy-performance experiment.',
        'Trace pairs match prompt, output length and body but differ in tokens and speculative acceptance.',
        'One-second rank intervals overlap and are not kernel critical-path attribution.']
    result['consumed_artifact_sha256'] = dict(sorted(SOURCES.items()))
    for relative, expected in SOURCES.items():
        if hashlib.sha256((E / relative).read_bytes()).hexdigest() != expected:
            raise ValueError('evidence changed during analysis')
    (E / 'summary.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'cache_requests': sum(r.get('requests',0) for r in result['cache'].values()),
        'decode_requests': sum(r['requests'] for r in result['decode'].values()), 'pairs': result['pairs']}))
