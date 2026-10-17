#!/usr/bin/env python3
"""Private terminal-view assembler; no requests, lifecycle, or publication."""
from __future__ import annotations
import argparse,json,hashlib,re
import statistics
from pathlib import Path
from anvil_serving.benchmarking.artifacts import atomic_write_json,evidence_file_reference,validate_cross_suite_evidence

def load(p):
 v=json.loads(p.read_text());
 if not isinstance(v,dict): raise ValueError(f'object required: {p}')
 return v
def ref(p,root): return evidence_file_reference(str(p),root=str(root))
def dist(m,k):
 suffix='_ms' if k.endswith('_ms') else ''; stem=k[:-3] if suffix else k
 return {'samples':m.get(stem+'_samples'),'p50':m.get(stem+'_p50'+suffix),'p95':m.get(stem+'_p95'+suffix)}
def verified_prompt_pairs(root, plan):
 review_path=root/'plans/prompt-reconstruction-review.json'
 if hashlib.sha256(review_path.read_bytes()).hexdigest()!='502e07a8d0dd860ecccec8e4cb827f614b8993f58f5fdeae905dbdc871a8a08c': raise ValueError('independently reviewed prompt reconstruction changed')
 review=load(review_path); inputs=review['inputs']; run=load(root/'plans/run-plan.json')
 for key,path in [('comparison_plan','plans/comparison-plan.json'),('run_plan','plans/run-plan.json'),('frozen_request_builder','plans/source/anvil_serving/benchmarking/requests.py')]:
  if inputs[key]!=ref(root/path,root): raise ValueError('prompt review input binding changed: '+key)
 executed=inputs['executed_request_builder']; frozen=inputs['frozen_request_builder']
 if executed != {'byte_equal_to_frozen_source':True,'repository_relative_path':'anvil_serving/benchmarking/requests.py','sha256':frozen['sha256']}: raise ValueError('request builder identity mismatch')
 rows={r['id']:r for r in run['matrix']}; pairs={}
 for pair in review['pairs']:
  pair_id=(pair['baseline_cell'],pair['candidate_cell'])
  if pair_id in pairs or pair.get('arms_match') is not True: raise ValueError('duplicate or unmatched reconstructed pair')
  for cid in pair_id:
   row=rows[cid];argv=row['argv'];opt=lambda name:argv[argv.index(name)+1]
   controls={'margin':1024,'max_model_len':int(opt('--max-model-len')),'max_tokens':int(opt('--max-tokens')),'planned_context_tokens':row['planned_context_tokens'],'prompt_cache_mode':opt('--prompt-cache-mode'),'request_canaries':'--request-canaries' in argv,'requests':row['requests'],'response_words':int(opt('--response-words')),'seed':row['seed'],'unique_prefix_namespace':'capacity-v1:seed-request-index'}
   if pair['controls']!=controls or pair['excluded_warmup']!=row['excluded_warmup']: raise ValueError('prompt reconstruction controls differ from frozen row')
  hashes=pair['request_prompt_sha256']
  if not isinstance(hashes,list) or len(hashes)!=controls['requests'] or any(not isinstance(h,str) or re.fullmatch('[0-9a-f]{64}',h) is None for h in hashes): raise ValueError('invalid reconstructed prompt vector')
  if pair['vector_sha256']!=hashlib.sha256(''.join(hashes).encode('ascii')).hexdigest(): raise ValueError('reconstructed prompt vector hash mismatch')
  pairs[pair_id]=pair
 return pairs,{'evidence':ref(review_path,root),'scope':review['method']['scope_boundary']}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--root',type=Path,required=True);ap.add_argument('--coverage',default='analysis/partial-serving-comparison-evidence.json');ap.add_argument('--out',default='analysis/final-draft');a=ap.parse_args();root=a.root.resolve();out=(root/a.out).resolve();graph_path=root/'final-graph-manifest.json'
 if not out.is_relative_to(root) or out==root: raise ValueError('output must be a child of campaign root')
 if out.exists() and any(out.iterdir()): raise ValueError('use a fresh output snapshot directory')
 if graph_path.exists(): raise ValueError('refusing stale root graph manifest; choose a new snapshot after review')
 out.mkdir(parents=True,exist_ok=True)
 plan=load(root/'plans/comparison-plan.json');coverage=validate_cross_suite_evidence(load(root/a.coverage),artifact_root=str(root),verify_files=True);adj_path=root/'plans/controlled-diagnostic-adjudication.json';adj=load(adj_path);review_pairs,prompt_binding=verified_prompt_pairs(root,plan);accepted=coverage['metrics_coverage']['cells'];supp={};rows=[]
 if coverage['metrics_coverage']['profile']!='serving-comparison/v1' or coverage['metrics_coverage']['frozen_plan']!=ref(root/'plans/comparison-plan.json',root): raise ValueError('coverage is not bound to current frozen plan')
 for cid,c in plan['required_cells'].items():
  native=root/'native'/c['arm']/(cid+'.json'); proj=root/'analysis/projections'/(cid+'.json')
  if cid in accepted:
   n,p=load(native),load(proj);m=n['metrics']; es=p.get('engine_sampling',{}); mem=p.get('memory_sampling',{}); ps=[x.get('prompt_tokens') for x in n.get('request_timings',[]) if isinstance(x,dict) and isinstance(x.get('prompt_tokens'),int)]
   required_supp={'runtime_model_load_memory','engine_phase_histograms','queue_waiting_by_reason','request_identity'}; got=p.get('supplementals',{})
   if set(got)!=required_supp: raise ValueError('projection supplemental record incomplete')
   supp[cid]=got
   actual={'ordered':ps,'min':min(ps) if ps else None,'max':max(ps) if ps else None,'p50':statistics.median(ps) if ps else None,'p95':sorted(ps)[max(0,((len(ps)*95+99)//100)-1)] if ps else None}
   row={'cell_id':cid,'disposition':'accepted','plan':c,'native':ref(native,root),'projection':ref(proj,root),'configured_context_distribution':n.get('context_distribution'),'actual_prompt_tokens':actual,'timing':{k:dist(m,k) for k in ('effective_prefill_tok_s','time_to_first_output_ms','ttft_ms','decode_tok_s','tpot_ms','e2e_ms','prompt_tokens','completion_tokens')},'aggregate_output_throughput_tok_s':m.get('throughput_tok_s'),'concurrency':{'requested':c['concurrency'],'client_observed_max':n.get('client_concurrency_observed_max'),'engine_active_max':es.get('active_max'),'engine_waiting_max':es.get('queued_max'),'engine_occupancy_max':es.get('occupancy_max'),'waiting_by_reason_max':es.get('waiting_by_reason_max',es.get('waiting_by_reason_unavailable'))},'kv':{'physical_bytes':p.get('kv_startup_observation',{}).get('physical_total_bytes'),'allocated_tokens':p['metrics'].get('kv_cache_tokens',{}).get('value')},'memory':{'gpu_memory_accounting':'worst single sampled device; not a two-GPU sum','gpu_peak_used_bytes_worst_device':mem.get('gpu',{}).get('peak_used_bytes'),'gpu_min_free_bytes_worst_device':mem.get('gpu',{}).get('min_free_bytes'),'host_available_bytes':mem.get('host',{}).get('host_available_bytes'),'cgroup_sampled_peak_bytes':mem.get('host',{}).get('cgroup_sampled_peak_bytes'),'cgroup_lifetime_peak_bytes':mem.get('host',{}).get('cgroup_lifetime_peak_bytes')}}
  elif c['workload']=='synthetic-repeated-code':
   # Preserve the one executed original controlled failure separately.
   row={'cell_id':cid,'plan':c,'disposition':'failed' if cid=='baseline-controlled-ctx10240-c1' else 'blocked','reason':'retained original controlled deadline/failure' if cid=='baseline-controlled-ctx10240-c1' else adj['original_controlled_remaining']}
  else: row={'cell_id':cid,'plan':c,'disposition':'pending','reason':'no accepted fresh complete frozen-plan projection'}
  rows.append(row)
 pairs=[]
 for b in rows:
  if b['plan']['arm']!='baseline' or b['disposition']!='accepted':continue
  mate=next((x for x in rows if x['plan']['arm']=='candidate' and x['disposition']=='accepted' and all(x['plan'][k]==b['plan'][k] for k in ('workload','context_tokens','concurrency','requests'))),None)
  if mate:
   left,right=supp[b['cell_id']]['request_identity'],supp[mate['cell_id']]['request_identity']; review=review_pairs.get((b['cell_id'],mate['cell_id']))
   keys=('namespace','context_seed','request_indices','prompt_tokens_ordered','request_canaries')
   if review and review.get('arms_match') is True and left.get('status')=='measured' and right.get('status')=='measured' and all(left['value'][k]==right['value'][k] for k in keys): pairs.append((b,mate))
 stats={'required_cells':42,'accepted_cells':sum(r['disposition']=='accepted' for r in rows),'accepted_expected_terminal':24,'failed_original_controlled_cells':sum(r['disposition']=='failed' for r in rows),'blocked_controlled_cells':sum(r['disposition']=='blocked' for r in rows),'pending_cells':sum(r['disposition']=='pending' for r in rows),'matched_latency_pairs':len(pairs),'original_matrix_status':'partial'}
 noncontrolled=[r for r in rows if r['plan']['workload']!='synthetic-repeated-code']
 all_supplementals=all(all(v.get('status')=='measured' for v in supp[r['cell_id']].values()) for r in noncontrolled if r['disposition']=='accepted')
 natural_edge_complete=(len(noncontrolled)==24 and all(r['disposition']=='accepted' for r in noncontrolled) and all_supplementals and len(pairs)==12)
 result={'schema':'private-serving-final-draft/v2','coverage':ref(root/a.coverage,root),'prompt_reconstruction':prompt_binding,'statistics':stats,'rows':rows,'complete_strata':{'natural_and_edge':natural_edge_complete,'original_matrix':False},'promotion':{'promoted':False},'chart_rendering':{'status':'ready' if natural_edge_complete else 'pending','reason':'requires identity-matched accepted baseline/candidate pairs'}}
 atomic_write_json(str(out/'summary-table.json'),result)
 if natural_edge_complete:
  charts=[]
  for metric,title in [('effective_prefill_tok_s','Effective prefill p50 (tok/s)'),('time_to_first_output_ms','TTFO p50 (ms)'),('ttft_ms','TTFT p50 (ms)'),('decode_tok_s','Decode p50 (tok/s)'),('tpot_ms','TPOT p50 (ms)'),('e2e_ms','E2E p50 (ms)'),('throughput_tok_s','Aggregate output throughput (tok/s)')]:
   series=[]
   for arm in ('baseline','candidate'):
    pts=[]
    for b,c in pairs:
     row=b if arm=='baseline' else c; artifact=row['native']['path']; valuepath='metrics.throughput_tok_s' if metric=='throughput_tok_s' else 'metrics.'+metric.replace('_ms','')+'_p50'+('_ms' if metric.endswith('_ms') else '')
     pts.append({'artifact':artifact,'x':f"{row['plan']['workload']} {row['plan']['context_tokens']} c{row['plan']['concurrency']}"})
    series.append({'label':arm,'points':pts})
   charts.append({'title':title,'metric':valuepath,'x_label':'matched cell','y_label':title,'series':series})
  atomic_write_json(str(graph_path),{'schema':'anvil-serving.benchmark-graph-manifest/v1','title':'Private serving comparison','subtitle':'Matched accepted native cells only','charts':charts})
 else: atomic_write_json(str(out/'graph-rendering-pending.json'),result['chart_rendering'])
 lines=['# Private serving comparison draft','','Original 42-cell matrix remains **partial**.','', '| Cell | Disposition | Actual context | E2E p50/p95 |','| --- | --- | --- | --- |']
 for r in rows:
  t=r.get('timing',{}).get('e2e_ms',{});lines.append(f"| {r['cell_id']} | {r['disposition']} | {r.get('actual_prompt_tokens',{}).get('p50','')} | {t.get('p50','')}/{t.get('p95','')} |")
 (out/'summary.md').write_text('\n'.join(lines)+'\n');print(json.dumps(stats,sort_keys=True))
if __name__=='__main__':main()
