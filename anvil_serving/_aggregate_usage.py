#!/usr/bin/env python3
"""aggregate_usage.py - roll up ALL Claude Code session logs into inference-sizing metrics.

Mirrors session-retro/session_stats.py field definitions (output/input/cache tokens,
tool_use, workflow <usage> fan-out) and adds the distributions that size a local
inference server: per-call context size, generation length, concurrency, throughput.

Stdlib only. Reads ~/.claude/projects/**/*.jsonl. Writes JSON to stdout (or --out).
"""
import json, os, re, sys, glob
from collections import Counter, defaultdict
from datetime import datetime

PROJECTS = os.environ.get("ANVIL_CLAUDE_LOGS") or os.path.expanduser("~/.claude/projects")

def pct(sorted_vals, q):
    if not sorted_vals: return 0
    if q <= 0: return sorted_vals[0]
    if q >= 100: return sorted_vals[-1]
    i = (len(sorted_vals)-1) * q/100.0
    lo = int(i); hi = min(lo+1, len(sorted_vals)-1)
    frac = i-lo
    return int(round(sorted_vals[lo]*(1-frac) + sorted_vals[hi]*frac))

def fiso(x):
    try: return datetime.fromisoformat(x.replace("Z","+00:00"))
    except Exception: return None

def main():
    out_path = None
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out")+1]
    files = glob.glob(os.path.join(PROJECTS, "**", "*.jsonl"), recursive=True)

    # global accumulators
    tot = Counter()                      # output/input/cc/cr/asst_calls
    by_model_calls = Counter()
    by_model_out = Counter()
    tools = Counter()
    skills = Counter()
    agent_types = Counter()
    ctx_sizes = []                       # input+cc+cr per assistant call
    out_sizes = []                       # output_tokens per assistant call
    per_min_calls = Counter()            # ts[:16] -> calls
    per_min_sessions = defaultdict(set)  # ts[:16] -> {sessionId}
    per_hour_out = Counter()             # ts[:13] -> output tokens
    per_day = defaultdict(lambda: [0,0,0])  # day -> [out_tokens, calls, ]
    per_day_sessions = defaultdict(set)
    wf_count = 0
    wf_agent_counts = []                 # agent_count per workflow dispatch
    wf_subagent_tokens = 0
    sess_summ = []                       # (duration_h, asst_turns, out_tokens)
    n_sessions = 0

    g = lambda pat, txt: (re.search(pat, txt, re.S).group(1) if re.search(pat, txt, re.S) else None)

    for f in files:
        n_sessions += 1
        s_first=s_last=None; s_asst=0; s_out=0; sid=None
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except Exception:
            continue
        with fh:
            for line in fh:
                line=line.strip()
                if not line: continue
                try: d = json.loads(line)
                except Exception: continue
                if sid is None: sid = d.get("sessionId")
                ts = d.get("timestamp")
                if ts:
                    s_first = s_first or ts; s_last = ts
                m = d.get("message")
                if not isinstance(m, dict): continue
                t = d.get("type")
                if t == "assistant":
                    u = m.get("usage") or {}
                    it = u.get("input_tokens",0) or 0
                    ot = u.get("output_tokens",0) or 0
                    cc = u.get("cache_creation_input_tokens",0) or 0
                    cr = u.get("cache_read_input_tokens",0) or 0
                    tot["out"]+=ot; tot["inp"]+=it; tot["cc"]+=cc; tot["cr"]+=cr; tot["asst"]+=1
                    s_asst+=1; s_out+=ot
                    model = m.get("model") or "unknown"
                    by_model_calls[model]+=1; by_model_out[model]+=ot
                    ctx = it+cc+cr
                    ctx_sizes.append(ctx); out_sizes.append(ot)
                    if ts:
                        mn = ts[:16]; hr = ts[:13]; day = ts[:10]
                        per_min_calls[mn]+=1
                        if sid: per_min_sessions[mn].add(sid)
                        per_hour_out[hr]+=ot
                        per_day[day][0]+=ot; per_day[day][1]+=1
                        if sid: per_day_sessions[day].add(sid)
                    for c in (m.get("content") or []):
                        if isinstance(c, dict) and c.get("type")=="tool_use":
                            nm = c.get("name","?"); tools[nm]+=1
                            inp = c.get("input") or {}
                            if nm=="Skill": skills[inp.get("skill","?")]+=1
                            elif nm=="Agent": agent_types[inp.get("subagent_type") or "general-purpose"]+=1
                elif t == "user":
                    c = m.get("content")
                    txt = c if isinstance(c,str) else (" ".join(x.get("text","") for x in c if isinstance(x,dict) and x.get("type")=="text") if isinstance(c,list) else "")
                    if "<usage>" in txt or "<task-notification>" in txt:
                        wf_count += 1
                        ac = g(r"<agent_count>(\d+)", txt); st = g(r"<subagent_tokens>(\d+)", txt)
                        if ac: wf_agent_counts.append(int(ac))
                        if st: wf_subagent_tokens += int(st)
        if s_asst:
            dur = 0.0
            if s_first and s_last:
                a,b = fiso(s_first), fiso(s_last)
                if a and b: dur = round((b-a).total_seconds()/3600,3)
            sess_summ.append((dur, s_asst, s_out))

    ctx_sizes.sort(); out_sizes.sort()
    durs = sorted(x[0] for x in sess_summ)
    turns = sorted(x[1] for x in sess_summ)

    # concurrency / throughput
    peak_calls_min = per_min_calls.most_common(1)[0] if per_min_calls else ("",0)
    min_call_counts = sorted(per_min_calls.values())
    peak_sessions_min = max(((mn, len(s)) for mn,s in per_min_sessions.items()), key=lambda x:x[1], default=("",0))
    sess_per_min = sorted(len(s) for s in per_min_sessions.values())
    peak_hour = per_hour_out.most_common(1)[0] if per_hour_out else ("",0)

    days = sorted(per_day.keys())
    daily = [{"day":d, "out":per_day[d][0], "calls":per_day[d][1], "sessions":len(per_day_sessions[d])} for d in days]
    busiest = max(daily, key=lambda x:x["out"], default=None)

    wf_agent_counts.sort()
    res = dict(
        window=dict(files=len(files), sessions_with_calls=len(sess_summ),
                    first_day=days[0] if days else None, last_day=days[-1] if days else None,
                    active_days=len(days)),
        totals=dict(assistant_calls=tot["asst"], output_tokens=tot["out"], fresh_input_tokens=tot["inp"],
                    cache_creation_tokens=tot["cc"], cache_read_tokens=tot["cr"],
                    total_processed=tot["out"]+tot["inp"]+tot["cc"]+tot["cr"]),
        model_mix=dict(calls=dict(by_model_calls.most_common()), output_tokens=dict(by_model_out.most_common())),
        context_size_per_call=dict(n=len(ctx_sizes), p50=pct(ctx_sizes,50), p90=pct(ctx_sizes,90),
                                   p95=pct(ctx_sizes,95), p99=pct(ctx_sizes,99), max=ctx_sizes[-1] if ctx_sizes else 0,
                                   mean=int(sum(ctx_sizes)/len(ctx_sizes)) if ctx_sizes else 0),
        generation_per_call=dict(p50=pct(out_sizes,50), p90=pct(out_sizes,90), p95=pct(out_sizes,95),
                                 p99=pct(out_sizes,99), max=out_sizes[-1] if out_sizes else 0,
                                 mean=int(sum(out_sizes)/len(out_sizes)) if out_sizes else 0),
        concurrency=dict(
            peak_calls_per_min=dict(minute=peak_calls_min[0], calls=peak_calls_min[1]),
            p99_calls_per_min=pct(min_call_counts,99), p95_calls_per_min=pct(min_call_counts,95),
            peak_parallel_sessions=dict(minute=peak_sessions_min[0], sessions=peak_sessions_min[1]),
            p99_parallel_sessions=pct(sess_per_min,99), p95_parallel_sessions=pct(sess_per_min,95),
        ),
        throughput=dict(peak_output_tokens_per_hour=dict(hour=peak_hour[0], tokens=peak_hour[1]),
                        peak_sustained_tok_per_s=round(peak_hour[1]/3600,1)),
        workflows=dict(dispatches=wf_count, subagent_tokens=wf_subagent_tokens,
                       agent_count_p50=pct(wf_agent_counts,50), agent_count_p95=pct(wf_agent_counts,95),
                       agent_count_max=wf_agent_counts[-1] if wf_agent_counts else 0,
                       total_subagents=sum(wf_agent_counts)),
        sessions=dict(count=len(sess_summ), median_duration_h=pct(durs,50), p95_duration_h=pct([int(x*1000) for x in durs],95)/1000.0 if durs else 0,
                      max_duration_h=durs[-1] if durs else 0, median_asst_turns=pct(turns,50), max_asst_turns=turns[-1] if turns else 0,
                      sessions_per_active_day=round(len(sess_summ)/max(len(days),1),1)),
        tools=dict(tools.most_common(25)),
        skills=dict(skills.most_common(20)),
        agent_types=dict(agent_types.most_common(20)),
        busiest_day=busiest,
        daily=daily,
    )
    txt = json.dumps(res, indent=1)
    if out_path:
        open(out_path,"w").write(txt)
        print("WROTE", out_path, len(txt), "bytes")
    # always print a compact human summary
    T=res["totals"]; C=res["context_size_per_call"]; G=res["generation_per_call"]; X=res["concurrency"]; W=res["workflows"]
    print("=== SUMMARY ===")
    print("window:", res["window"])
    print("assistant_calls:", T["assistant_calls"], "| output:", T["output_tokens"], "| total_processed:", T["total_processed"])
    print("model_mix calls:", res["model_mix"]["calls"])
    print("ctx/call p50/p90/p95/p99/max:", C["p50"],C["p90"],C["p95"],C["p99"],C["max"])
    print("gen/call p50/p95/max:", G["p50"],G["p95"],G["max"])
    print("peak calls/min:", X["peak_calls_per_min"], "| peak parallel sessions:", X["peak_parallel_sessions"])
    print("throughput:", res["throughput"])
    print("workflows:", W)
    print("sessions:", res["sessions"])
    print("busiest_day:", res["busiest_day"])

if __name__ == "__main__":
    main()
