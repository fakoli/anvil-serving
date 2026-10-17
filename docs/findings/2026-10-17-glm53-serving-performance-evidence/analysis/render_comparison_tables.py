#!/usr/bin/env python3
"""Render the closed comparison's native-bound rows as readable Markdown."""
import hashlib
import json
import sys
from pathlib import Path

root, summary_path, output = map(Path, sys.argv[1:])
s = json.loads(summary_path.read_text())
assert s['complete_strata']['natural_and_edge'] is True
rows = sorted((r for r in s['rows'] if r['disposition'] == 'accepted'),
              key=lambda r: (r['plan']['context_tokens'], r['plan']['concurrency'], r['plan']['arm']))
assert len(rows) == 24 and s['statistics']['matched_latency_pairs'] == 12
projections = {}
for r in rows:
    for name in ('native', 'projection'):
        ref = r[name]; raw = (root / ref['path']).read_bytes()
        assert len(raw) == ref['bytes'] and hashlib.sha256(raw).hexdigest() == ref['sha256']
        if name == 'projection': projections[r['cell_id']] = json.loads(raw)

def pair(row, name, scale=1):
    d = row['timing'][name]
    assert d['samples'] == row['plan']['requests']
    return f"{d['p50']/scale:,.2f} / {d['p95']/scale:,.2f}"

def label(row):
    arm = 'EXL3' if row['plan']['arm'] == 'baseline' else 'FP4'
    return [arm, f"{row['actual_prompt_tokens']['p50']:,.0f}", str(row['plan']['concurrency'])]

lines = ['# Matched serving measurements', '',
         'All distribution pairs below are p50 / p95 from n=8 per natural cell or n=4 per capacity-edge cell. They are descriptive small-sample statistics.', '']
def table(title, header, values):
    lines.extend(['## '+title, '', '| '+' | '.join(header)+' |', '| '+' | '.join('---' for _ in header)+' |'])
    lines.extend('| '+' | '.join(value)+' |' for value in values)
    lines.append('')
latency=[]; generation=[]; resources=[]
for r in rows:
    p=projections[r['cell_id']]; h=p['supplementals']['engine_phase_histograms']['value']; m=r['memory']; c=r['concurrency']; a=p['metrics']['admission_status']['observation']
    mean=lambda name: h[name]['sum']/h[name]['count']
    latency.append(label(r)+[pair(r,'time_to_first_output_ms',1000),pair(r,'ttft_ms',1000),pair(r,'effective_prefill_tok_s'),f"{mean('request_prefill'):.3f}",f"{mean('request_queue'):.3f}"])
    generation.append(label(r)+[pair(r,'decode_tok_s'),pair(r,'tpot_ms'),pair(r,'e2e_ms',1000),f"{r['aggregate_output_throughput_tok_s']:.2f}",pair(r,'completion_tokens')])
    resources.append(label(r)+[f"{c['client_observed_max']} / {c['engine_active_max']} / {c['engine_waiting_max']}",f"{100*p['engine_sampling']['kv_usage_fraction_max']:.2f}",f"{m['gpu_peak_used_bytes_worst_device']/2**30:.2f} / {m['gpu_min_free_bytes_worst_device']/2**30:.2f}",f"{m['host_available_bytes']/2**30:.2f}",f"{m['cgroup_sampled_peak_bytes']/2**30:.2f}",f"{a['ooms']} / {a['preemptions']} / {a['refused_requests']}"])
table('Prefill and first output',['Arm','Actual input p50','C','TTFO s','Visible TTFT s','Effective prefill tok/s','Engine prefill mean s','Engine queue mean s'],latency)
table('Generation and request completion',['Arm','Actual input p50','C','Decode tok/s','TPOT ms','E2E s','Aggregate output tok/s','Completion tokens'],generation)
table('Observed resource use',['Arm','Actual input p50','C','Client / active / queued max','KV use peak %','GPU peak used / min free GiB','Host min available GiB','Cgroup sampled peak GiB','OOM / preempt / refusal'],resources)
lines.extend(['Timing and resource definitions, configuration, exact native references, and limitations are in the finding and summary-table.json. GPU figures are worst single-device samples; maxima are sampled lower bounds. Host available memory is whole-host memory; cgroup memory includes page cache.', ''])
with output.open('x') as handle: handle.write('\n'.join(lines))
print(json.dumps({'rows':len(rows),'tables':3,'output':str(output)}))
