#!/usr/bin/env python3
"""benchmark.py - replay the measured request-size distribution against a local endpoint.

Reproduces the Claude Code SUBAGENT (specialist) profile and a fan-out burst, then
reports TTFT, end-to-end latency, throughput, and prefix-cache hit signal.
Default distribution = measured subagent percentiles (ctx p50 55K / p95 159K; gen tiny).

Stdlib only (urllib + threading; streaming for TTFT).

Usage:
  # steady mixed load:
  python3 benchmark.py --base-url http://127.0.0.1:30000/v1 --model coder-specialist \
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

# Conservative chars-per-token: real code tokenizes to ~3-4 chars/token, so dividing
# char length by this OVER-estimates the token count -> a clamp built on it never lets
# the prompt sneak past the serve's max_model_len.
CHARS_PER_TOKEN = 3.0
# Token headroom left below max_model_len when clamping (absorbs heuristic error +
# template/role overhead so prompt + generation stay inside the context window).
DEFAULT_CTX_MARGIN = 1024

def est_tokens(text):
    """Conservative (slightly high) token estimate for a string, stdlib-only."""
    return int(len(text) / CHARS_PER_TOKEN)

def ctx_cap(max_model_len, max_tokens, margin=DEFAULT_CTX_MARGIN):
    """Largest prompt-token budget that keeps prompt + generation inside the serve's
    context window. Returns None when no limit is known -> no clamp (legacy behavior)."""
    if not max_model_len:
        return None
    return max(256, int(max_model_len) - int(max_tokens) - int(margin))

def clamp_ctx(ctx, cap):
    """Clamp a sampled/fixed ctx-token target down to the serve's usable budget."""
    return ctx if cap is None else min(ctx, cap)

def make_prompt(shared_prefix, ctx_tokens, uniq, max_prompt_tokens=None):
    # shared_prefix is identical across requests (prefix-cache hit); tail varies to reach ctx_tokens
    approx_words = int(ctx_tokens * 0.75)
    lines = []
    w = len(shared_prefix.split())
    i = 0
    while w < approx_words:
        s = FILLER % (uniq + i, i)
        lines.append(s); w += len(s.split()); i += 1
    prompt = shared_prefix + "\n" + "".join(lines) + f"\n# request {uniq}: summarize the above in one line."
    # The word->token heuristic UNDER-estimates real tokens (code tokenizes to >1.33
    # tok/word), so an unclamped prompt can blow past a small serve's max_model_len and
    # 400. When a cap is known, truncate to a conservative char budget that keeps the
    # REAL token count under the cap (front-truncation preserves the shared prefix for
    # prefix-cache hits).
    if max_prompt_tokens is not None and est_tokens(prompt) > max_prompt_tokens:
        prompt = prompt[: int(max_prompt_tokens * CHARS_PER_TOKEN)]
    return prompt

def build_body(model, prompt, max_tokens, chat_template_kwargs=None):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "temperature": 0.0, "stream": True,
            "stream_options": {"include_usage": True}}
    # chat_template_kwargs (e.g. {"enable_thinking": False}) is honored by SGLang/vLLM
    # for Qwen3.x / GLM so thinking-by-default models don't burn the whole token budget
    # on hidden reasoning and emit ZERO content deltas (which the report reads as a FALSE
    # 0 tok/s with TTFT==E2E).
    if chat_template_kwargs:
        body["chat_template_kwargs"] = chat_template_kwargs
    return body

def detect_max_model_len(base, model=None, key=None, timeout=15):
    """Best-effort probe of <base>/models for the serve's context window. SGLang & vLLM
    expose it on the model card. Returns None on any problem so callers fall back to
    current (unclamped) behavior."""
    url = base.rstrip("/") + "/models"
    headers = {}
    if key: headers["Authorization"] = "Bearer " + key
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:
        return None
    entries = data.get("data") if isinstance(data, dict) else None
    if not entries:
        return None
    chosen = None
    for e in entries:
        if not isinstance(e, dict):
            continue
        if model and e.get("id") == model:
            chosen = e; break
        if chosen is None:
            chosen = e
    if not isinstance(chosen, dict):
        return None
    for k in ("max_model_len", "max_context_length", "context_length"):
        v = chosen.get(k)
        if isinstance(v, int) and v > 0:
            return v
    return None

def stream_chat(base, model, prompt, key, max_tokens, timeout=900, chat_template_kwargs=None):
    url = base.rstrip("/") + "/chat/completions"
    body = build_body(model, prompt, max_tokens, chat_template_kwargs)
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
    ap.add_argument("--max-model-len", type=int, default=0,
                    help="serve's context window (max_model_len). Sampled/fixed ctx is clamped to "
                         "stay under it (ctx <= max_model_len - max_tokens - margin) so a small-context "
                         "serve (e.g. 16384) doesn't HTTP 400. 0 = auto-detect from /v1/models, else no clamp.")
    ap.add_argument("--margin", type=int, default=DEFAULT_CTX_MARGIN,
                    help="token headroom left below max_model_len when clamping (default %(default)s)")
    ap.add_argument("--no-thinking", action="store_true",
                    help="inject chat_template_kwargs={'enable_thinking': False} so thinking-by-default "
                         "models (Qwen3.x, GLM) emit CONTENT instead of spending the whole max_tokens "
                         "budget on hidden reasoning and reporting a FALSE 0 tok/s (TTFT==E2E). NOTE: "
                         "gpt-oss-style models IGNORE this kwarg (they gate reasoning via 'reasoning effort').")
    a = ap.parse_args()

    # Resolve the serve's context window: explicit flag wins; else best-effort probe /v1/models.
    max_model_len = a.max_model_len or detect_max_model_len(a.base_url, a.model, a.api_key)
    cap = ctx_cap(max_model_len, a.max_tokens, a.margin)
    ctk = {"enable_thinking": False} if a.no_thinking else None

    shared = (FILLER % (0, 0)) * max(1, int(a.shared_prefix_tokens * 0.75) // 6)
    n = a.burst if a.burst else a.requests
    conc = a.burst if a.burst else a.concurrency
    jobs = []
    for i in range(n):
        ctx = clamp_ctx(a.ctx_tokens or sample_ctx(), cap)
        jobs.append(make_prompt(shared, ctx, i if not a.burst else 0, max_prompt_tokens=cap))  # burst: identical-ish prefix

    capnote = f" max_model_len={max_model_len}(ctx<={cap})" if cap is not None else ""
    thinknote = " thinking=off" if a.no_thinking else ""
    print(f"BENCH {a.base_url} model={a.model}  n={n} concurrency={conc} "
          f"{'BURST(shared-prefix)' if a.burst else 'mixed'} max_tokens={a.max_tokens}{capnote}{thinknote}")
    t0 = time.time(); results = []
    with ThreadPoolExecutor(max_workers=conc) as ex:
        futs = [ex.submit(stream_chat, a.base_url, a.model, p, a.api_key, a.max_tokens,
                          chat_template_kwargs=ctk) for p in jobs]
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
