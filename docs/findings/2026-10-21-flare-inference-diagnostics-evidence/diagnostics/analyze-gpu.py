"""Descriptive GPU samples restricted to measured request-block wall-clock windows."""
import json
import hashlib
import math
from pathlib import Path

E = Path(__file__).parent
NAMES = ('power_limit_watts', 'power_draw_watts', 'temperature_gpu', 'utilization_gpu_ratio',
         'memory_used_bytes', 'clocks_current_sm_clock_hz', 'clocks_current_memory_clock_hz',
         'clocks_event_reasons_hw_power_brake_slowdown', 'clocks_event_reasons_hw_thermal_slowdown',
         'clocks_event_reasons_sw_power_cap', 'clocks_event_reasons_sw_thermal_slowdown')
rows = []
sources = []
missing = []
seen = set()
for arm in ('incumbent-before', 'flare-control', 'flare-instrumented', 'incumbent-after'):
    path = E / (arm + '-prometheus-gpu-range.json')
    raw = path.read_bytes()
    payload = json.loads(raw)
    response = payload.get('response', payload)
    sources.append({'path': path.name, 'sha256': hashlib.sha256(raw).hexdigest()})
    windows = []
    for c in (1, 4):
        for rep in ('r1', 'r2', 'r3'):
            source = E / f'{arm}-c{c}-{rep}.json'
            data = source.read_bytes()
            run = json.loads(data)
            windows.append((source.stem, run['started_at_unix_ns'] / 1e9, run['finished_at_unix_ns'] / 1e9))
            sources.append({'path': source.name, 'sha256': hashlib.sha256(data).hexdigest()})
    for series in response['data']['result']:
        metric = series['metric']
        name = metric['__name__'].removeprefix('nvidia_smi_')
        if metric.get('host') != 'primary-node' or name not in NAMES:
            continue
        key = (arm, metric['gpu_uuid'], name)
        if key in seen:
            raise ValueError('duplicate GPU metric series')
        seen.add(key)
        coverage = [{'window': window, 'returned_samples': sum(a <= t <= b for t, _ in series['values'])}
                    for window, a, b in windows]
        missing.extend({'arm': arm, 'gpu_uuid': key[1], 'metric': name, 'window': item['window']}
                       for item in coverage if not item['returned_samples'])
        values = [float(v) for t, v in series['values'] if any(a <= t <= b for _, a, b in windows)]
        if not values or not all(math.isfinite(v) for v in values):
            raise ValueError('missing/nonfinite samples: ' + name)
        rows.append({'arm': arm, 'gpu_uuid': metric['gpu_uuid'], 'metric': name,
                     'returned_samples': len(values), 'coverage': coverage, 'min': min(values), 'max': max(values)})
    selected = [row for row in rows if row['arm'] == arm]
    gpus = {row['gpu_uuid'] for row in selected}
    if len(gpus) != 2:
        raise ValueError('expected two GPUs: ' + arm)
    for gpu in sorted(gpus):
        observed = {row['metric'] for row in selected if row['gpu_uuid'] == gpu}
        missing.extend({'arm': arm, 'gpu_uuid': gpu, 'metric': name, 'window': window}
                       for name in NAMES if name not in observed for window, _, _ in windows)
result = {'rows': rows, 'sources': sources, 'missing_windows': missing,
          'analyzer_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          'limits': ['Descriptive returned Prometheus samples within measured block UTC windows, including failed populations',
                     'Queries can repeat collector samples; counts are not independent measurements',
                     'No energy integration, kernel attribution, hardware saturation or causal power conclusion',
                     'Temperature is exporter GPU temperature; sampled extrema are not guaranteed physical extrema']}
with (E / 'gpu-observations.json').open('x') as out:
    out.write(json.dumps(result, indent=2) + '\n')
print(json.dumps({'series': len(rows)}))
