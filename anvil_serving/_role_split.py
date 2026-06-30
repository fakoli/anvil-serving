#!/usr/bin/env python3
"""role_split.py - split Claude Code per-call context/generation by ROLE.

Splits assistant calls by isSidechain (subagent vs main orchestrator) and by model,
so we can size the LOCAL specialist context ceiling from the subagent distribution
rather than the orchestrator's long-context tail. Stdlib only.
"""
import json
import os
import glob
import sys
from collections import defaultdict

PROJECTS = os.environ.get("ANVIL_CLAUDE_LOGS") or os.path.expanduser("~/.claude/projects")

def pct(v, q):
    if not v: return 0
    if q<=0: return v[0]
    if q>=100: return v[-1]
    i=(len(v)-1)*q/100.0; lo=int(i); hi=min(lo+1,len(v)-1); f=i-lo
    return int(round(v[lo]*(1-f)+v[hi]*f))

def summarize(ctx, out):
    ctx=sorted(ctx); out=sorted(out)
    return dict(n=len(ctx),
        ctx=dict(p50=pct(ctx,50),p90=pct(ctx,90),p95=pct(ctx,95),p99=pct(ctx,99),max=ctx[-1] if ctx else 0,
                 mean=int(sum(ctx)/len(ctx)) if ctx else 0),
        gen=dict(p50=pct(out,50),p95=pct(out,95),max=out[-1] if out else 0))

def main():
    out_path = sys.argv[sys.argv.index("--out")+1] if "--out" in sys.argv else None
    files = glob.glob(os.path.join(PROJECTS,"**","*.jsonl"), recursive=True)
    groups = defaultdict(lambda: ([],[]))   # key -> (ctx_list, out_list)
    by_model = defaultdict(lambda: ([],[]))
    seen_sidechain_field = 0; total = 0
    # buckets to show what share of SUBAGENT calls fit a given local context ceiling
    ceilings = [16384, 32768, 65536, 131072, 262144]
    sub_ctx_all = []
    for f in files:
        try: fh=open(f, encoding="utf-8", errors="replace")
        except Exception: continue
        with fh:
            for line in fh:
                line=line.strip()
                if not line: continue
                try: d=json.loads(line)
                except Exception: continue
                if d.get("type")!="assistant": continue
                m=d.get("message")
                if not isinstance(m,dict): continue
                u=m.get("usage") or {}
                ctx=(u.get("input_tokens",0) or 0)+(u.get("cache_creation_input_tokens",0) or 0)+(u.get("cache_read_input_tokens",0) or 0)
                ot=u.get("output_tokens",0) or 0
                total+=1
                sc = d.get("isSidechain")
                if sc is not None: seen_sidechain_field+=1
                role = "subagent" if sc else "main"
                groups[role][0].append(ctx); groups[role][1].append(ot)
                model=m.get("model") or "unknown"
                by_model[model][0].append(ctx); by_model[model][1].append(ot)
                if role=="subagent":
                    sub_ctx_all.append(ctx)
    res = dict(
        total_assistant_calls=total,
        records_with_isSidechain_field=seen_sidechain_field,
        by_role={k:summarize(*v) for k,v in groups.items()},
        by_model={k:summarize(*v) for k,v in sorted(by_model.items(), key=lambda x:-len(x[1][0]))},
    )
    # what context ceiling covers what fraction of SUBAGENT calls
    sub_ctx_all.sort(); n=len(sub_ctx_all)
    cov={}
    for c in ceilings:
        cov[c]= round(100.0*sum(1 for x in sub_ctx_all if x<=c)/n,1) if n else 0
    res["subagent_context_coverage_pct"]=cov
    txt=json.dumps(res, indent=1)
    if out_path: open(out_path,"w").write(txt); print("WROTE",out_path)
    print("=== ROLE SPLIT ===")
    print("total assistant calls:",total,"| with isSidechain field:",seen_sidechain_field)
    for role,v in res["by_role"].items():
        print(f"\n[{role}] n={v['n']:,}")
        print("  ctx p50/p90/p95/p99/max:", v["ctx"]["p50"],v["ctx"]["p90"],v["ctx"]["p95"],v["ctx"]["p99"],v["ctx"]["max"])
        print("  gen p50/p95/max:", v["gen"]["p50"],v["gen"]["p95"],v["gen"]["max"])
    print("\n[by model] (ctx p50/p95/max | gen p50)")
    for mdl,v in list(res["by_model"].items())[:8]:
        print(f"  {mdl:28} n={v['n']:>7,}  ctx {v['ctx']['p50']:>7,}/{v['ctx']['p95']:>7,}/{v['ctx']['max']:>7,}  gen p50 {v['gen']['p50']}")
    print("\nSUBAGENT context coverage (share of subagent calls <= ceiling):")
    for c,p in res["subagent_context_coverage_pct"].items():
        print(f"  <= {c:>7,} tok : {p}%")

if __name__=="__main__":
    main()
