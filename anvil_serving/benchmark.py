#!/usr/bin/env python3
"""benchmark.py - replay the measured request-size distribution against a local endpoint.

Reproduces the Claude Code SUBAGENT (specialist) profile and a fan-out burst, then
reports TTFT, end-to-end latency, throughput, and prefix-cache hit signal.
Default distribution = measured subagent percentiles (ctx p50 55K / p95 159K; gen tiny).

Stdlib only (urllib + threading; streaming for TTFT).

Usage:
  # steady mixed load:
  python3 benchmark.py --base-url http://localhost:30000/v1 --model coder-specialist \
      --requests 60 --concurrency 20
  # fan-out burst sharing ONE prefix (exercises prefix cache like a workflow wave):
  python3 benchmark.py --base-url ... --model ... --burst 20 --shared-prefix-tokens 8000 --ctx-tokens 64000
"""
import argparse
import json
import time
import random
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

FILLER = "def helper_%d():\n    return compute(%d)  # routine specialist context\n"

def make_prompt(shared_prefix, ctx_tokens, uniq):
    # shared_prefix is identical across requests (prefix-cache hit); tail varies to reach ctx_tokens
    approx_words = int(ctx_tokens * 0.75)
    lines = []
    w = len(shared_prefix.split())
    i = 0
    while w < approx_words:
        s = FILLER % (uniq + i, i)
        lines.append(s); w += len(s.split()); i += 1
    return shared_prefix + "\n" + "".join(lines) + f"\n# request {uniq}: summarize the above in one line."

def stream_chat(base, model, prompt, key, max_tokens, timeout=900):
    url = base.rstrip("/") + "/chat/completions"
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
            "stream_options": {"include_usage": True}}
    headers = {"Content-Type": "application/json"}
    if key: headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    t0 = time.time(); ttft = None; out_toks = 0; usage = None
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"): continue
            data = line[5:].strip()
            if data == "[DONE]": break
            try: chunk = json.loads(data)
            except Exception: continue
            if chunk.get("usage"): usage = chunk["usage"]
            for ch in chunk.get("choices", []):
                delta = ch.get("delta", {})
                if delta.get("content"):
                    if ttft is None: ttft = time.time() - t0
                    out_toks += 1
    return dict(ttft=ttft if ttft is not None else (time.time()-t0),
                e2e=time.time()-t0, out_toks=out_toks, usage=usage)

def cached_fraction(usage):
    if not usage: return None
    det = usage.get("prompt_tokens_details") or {}
    cached = det.get("cached_tokens")
    pt = usage.get("prompt_tokens")
    if cached is None or not pt: return None
    return cached / pt

def pctile(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs: return 0
    i = min(len(xs)-1, int(round((len(xs)-1)*q/100)))
    return xs[i]

# measured subagent ctx percentiles (from role_split): rough inverse-CDF sampler
SUBAGENT_CTX = [(0.0,16000),(0.218,32768),(0.602,65536),(0.906,131072),(0.995,262144)]
def sample_ctx():
    r = random.random()
    for p, v in SUBAGENT_CTX:
        if r <= p: return v
    return 262144

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True); ap.add_argument("--model", required=True)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--requests", type=int, default=60)
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--burst", type=int, default=0, help="if >0, fire N requests sharing ONE prefix concurrently")
    ap.add_argument("--shared-prefix-tokens", type=int, default=8000)
    ap.add_argument("--ctx-tokens", type=int, default=0, help="fixed ctx; 0 = sample measured subagent distribution")
    ap.add_argument("--max-tokens", type=int, default=64, help="generation length (subagent median is tiny)")
    a = ap.parse_args()

    shared = (FILLER % (0, 0)) * max(1, int(a.shared_prefix_tokens * 0.75) // 6)
    n = a.burst if a.burst else a.requests
    conc = a.burst if a.burst else a.concurrency
    jobs = []
    for i in range(n):
        ctx = a.ctx_tokens or sample_ctx()
        jobs.append(make_prompt(shared, ctx, i if not a.burst else 0))  # burst: identical-ish prefix

    print(f"BENCH {a.base_url} model={a.model}  n={n} concurrency={conc} "
          f"{'BURST(shared-prefix)' if a.burst else 'mixed'} max_tokens={a.max_tokens}")
    t0 = time.time(); results = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = [ex.submit(stream_chat, a.base_url, a.model, p, a.api_key, a.max_tokens) for p in jobs]
        for f in as_completed(futs):
            try: results.append(f.result())
            except Exception as e: print("  req error:", e)
    wall = time.time() - t0
    ttfts = [r["ttft"] for r in results]; e2es = [r["e2e"] for r in results]
    out_tot = sum(r["out_toks"] for r in results)
    cfs = [cached_fraction(r["usage"]) for r in results]
    cfs = [c for c in cfs if c is not None]
    print("-"*60)
    print(f"completed:        {len(results)}/{n} in {wall:.1f}s")
    print(f"TTFT  p50/p95:    {pctile(ttfts,50):.2f}s / {pctile(ttfts,95):.2f}s")
    print(f"E2E   p50/p95:    {pctile(e2es,50):.2f}s / {pctile(e2es,95):.2f}s")
    print(f"throughput:       {out_tot/wall:.0f} output tok/s (aggregate)")
    if cfs:
        print(f"prefix-cache hit: {statistics.mean(cfs)*100:.1f}% avg cached prompt tokens (KEY KPI)")
    else:
        print("prefix-cache hit: endpoint did not return prompt_tokens_details.cached_tokens")
    print("-"*60)
    print("Tip: run once cold, then immediately again — TTFT should drop sharply on the 2nd run if prefix cache works.")

if __name__ == "__main__":
    main()
