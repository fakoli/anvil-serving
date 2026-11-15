"""Offline rejection checks against deliberately damaged copies of real evidence."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

E = Path(__file__).parent
spec = importlib.util.spec_from_file_location('analysis', E / 'analyze-wave.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def rejected(call):
    try:
        call()
    except (ValueError, KeyError, FileNotFoundError):
        return
    raise RuntimeError('damaged evidence accepted')

def main():
    for raw in ['x 1\nx 2', 'x NaN', 'x inf', 'x -1']:
        rejected(lambda: m.metrics(raw))
    rejected(lambda: m.deltas({'x_total': 2}, {'x_total': 1}))
    m.require_complete({'c': {'status': 'complete'}})
    rejected(lambda: m.require_complete({'c': {'status': 'not_collected'}}))
    with tempfile.TemporaryDirectory(prefix='flare-analysis-check-') as directory:
        m.E = Path(directory)
        cache = m.E / 'a-prime'
        shutil.copytree(E / 'a-prime', cache)
        m.cache(cache)
        p = cache / 'results.json'; raw = p.read_text(); rows = json.loads(raw)
        p.write_text(json.dumps(rows[:-1])); rejected(lambda: m.cache(cache)); p.write_text(raw)
        rows[1]['index'] = rows[0]['index']
        p.write_text(json.dumps(rows)); rejected(lambda: m.cache(cache)); p.write_text(raw)
        rows = json.loads(raw)
        for row in rows:
            del row['deltas']['flare_host_cache_store_failed_total']
        p.write_text(json.dumps(rows)); rejected(lambda: m.cache(cache)); p.write_text(raw)
        (cache / '051-after.prom').unlink(); rejected(lambda: m.cache(cache))
        decode = m.E / 'decode-c4-r1-off'
        shutil.copytree(E / decode.name, decode)
        cell = {'name': decode.name, 'concurrency': 4}
        m.decode(cell)
        s = decode / 'summary.json'; original = s.read_text(); summary = json.loads(original)
        summary['cases'][0]['requests'][0]['start_epoch_ns'] += 1
        s.write_text(json.dumps(summary)); rejected(lambda: m.decode(cell)); s.write_text(original)
        t = decode / 'r0-c4-i0.json'; original = t.read_text(); bad = json.loads(original)
        bad['core_ns'][0] = False
        t.write_text(json.dumps(bad)); rejected(lambda: m.decode(cell)); t.write_text(original)
        p = decode / 'r0-c4-i3.json'; row = json.loads(p.read_text()); row['index'] = 0
        p.write_text(json.dumps(row)); rejected(lambda: m.decode(cell))
        p.unlink(); rejected(lambda: m.decode(cell))
    print(json.dumps({'passed': 17, 'valid_controls': 3, 'damaged_cases_rejected': 14}))

if __name__ == '__main__':
    main()
