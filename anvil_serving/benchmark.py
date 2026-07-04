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

def _serve_recipes():
    """Import the shared serve-recipe helpers, working whether benchmark.py is imported
    as `anvil_serving.benchmark` (package) or run as a bare script (via cli._run_script)."""
    try:
        from . import serve_recipes as sr  # package import
    except ImportError:
        import serve_recipes as sr  # bare-script import (same dir on sys.path)
    return sr

def build_recipe(a, summary, *, capture=None, hardware=None):
    """Assemble a serve-recipe dict from a completed benchmark run + a live container.

    The reproducible half (image/env/flags/port + gpu_uuid) comes from
    `capture_from_container`; the hardware name/VRAM from `capture_hardware`; the
    [recipe.measured] numbers from THIS run's `summary`; and [recipe.intent] from the
    --recipe-* flags. `capture` / `hardware` are injectable for hermetic tests.
    """
    sr = _serve_recipes()
    capture = capture or sr.capture_from_container
    hardware = hardware or sr.capture_hardware

    cap = capture(a.recipe_from_container) if a.recipe_from_container else {}
    serve = dict(cap.get("serve") or {})
    hw = dict(cap.get("hardware") or {})
    gpu_uuid = hw.get("gpu_uuid")
    gpu = hardware(gpu_uuid) if (a.recipe_from_container or gpu_uuid) else {}

    recipe = {"model": a.recipe_model or a.model, "status": a.recipe_status}
    recipe["source"] = "measured via anvil-serving benchmark (%s)" % summary.get("run_id", "")

    hardware_block = {}
    if gpu.get("gpu"): hardware_block["gpu"] = gpu["gpu"]
    if gpu.get("vram_total_gb") is not None: hardware_block["vram_total_gb"] = gpu["vram_total_gb"]
    if gpu_uuid: hardware_block["gpu_uuid"] = gpu_uuid
    if hardware_block: recipe["hardware"] = hardware_block

    ctx = summary.get("context_tokens") or summary.get("max_context_tokens")
    if ctx and "context_tokens" not in serve:
        serve["context_tokens"] = ctx
    if serve: recipe["serve"] = serve

    metrics = summary.get("metrics") or {}
    measured = {}
    tps = metrics.get("throughput_tok_s")
    if tps is not None:
        measured["throughput_single_tok_s"] = round(tps, 1)
    ttft = metrics.get("ttft_p50_ms")
    if ttft is not None:
        measured["ttft_p50_ms"] = round(ttft, 1)
    if ctx:
        measured["context_tokens"] = ctx
    if measured: recipe["measured"] = measured

    intent = {}
    if a.recipe_intent:
        suited = [s.strip() for s in a.recipe_intent.split(",") if s.strip()]
        if suited: intent["suited"] = suited
    if a.recipe_mode:
        intent["mode"] = a.recipe_mode
    if intent: recipe["intent"] = intent

    return recipe

def emit_recipe(a, summary, *, capture=None, hardware=None, append=None):
    """Render + persist the recipe for this benchmark run (stdout if --recipe-out '-')."""
    sr = _serve_recipes()
    recipe = build_recipe(a, summary, capture=capture, hardware=hardware)
    if a.recipe_out == "-":
        print(sr.format_recipe(recipe), end="")
    else:
        (append or sr.append_recipe)(a.recipe_out, recipe)
        print("recorded serve recipe for %s -> %s" % (recipe["model"], a.recipe_out))
    return recipe

def main(argv=None):
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
    ap.add_argument("--json-out", default=None,
                    help="write a machine-readable JSON summary for external-bench compare")
    # --- GENERATE a serve recipe as a side effect of benchmarking a live serve ------
    # (READ them back with `anvil-serving models recipe list|show`.) All optional.
    ap.add_argument("--recipe-out", default=None,
                    help="after the run, record a [[recipe]] block: PATH to append to the "
                         "serve-recipe registry, or '-' for stdout. Captures the live serve's "
                         "reproducible docker config + THIS run's measured numbers.")
    ap.add_argument("--recipe-from-container", default=None, metavar="NAME",
                    help="docker container NAME of the serve to capture (image/env/flags/port + "
                         "gpu_uuid via `docker inspect`) for the recorded recipe")
    ap.add_argument("--recipe-intent", default=None, metavar="CSV",
                    help="comma-separated work-classes the serve is suited for (-> [recipe.intent].suited)")
    ap.add_argument("--recipe-mode", default=None,
                    help="the mode this recipe belongs to (-> [recipe.intent].mode)")
    ap.add_argument("--recipe-status", default="verified",
                    help="recipe provenance status (default %(default)s = measured on-box)")
    ap.add_argument("--recipe-model", default=None, metavar="NAME",
                    help="model id recorded in the recipe (default: --model)")
    a = ap.parse_args(argv)

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
    summary = {
        "schema": "anvil-serving.benchmark/v1",
        "run_id": time.strftime("benchmark-%Y%m%dT%H%M%SZ", time.gmtime(t0)),
        "base_url": a.base_url,
        "model": a.model,
        "requests": n,
        "completed": len(results),
        "concurrency": conc,
        "context_tokens": a.ctx_tokens or None,
        "max_context_tokens": max_model_len,
        "max_tokens": a.max_tokens,
        "serve_flags": {
            "shared_prefix_burst": bool(a.burst),
            "no_thinking": bool(a.no_thinking),
        },
        "metrics": {
            "ttft_p50_ms": pctile(ttfts, 50) * 1000.0,
            "ttft_p95_ms": pctile(ttfts, 95) * 1000.0,
            "e2e_p50_ms": pctile(e2es, 50) * 1000.0,
            "e2e_p95_ms": pctile(e2es, 95) * 1000.0,
            "throughput_tok_s": (out_tot / wall) if wall else 0.0,
            "output_tokens": out_tot,
            "prefix_cache_hit_avg": statistics.mean(cfs) if cfs else None,
        },
    }
    if cfs:
        print(f"prefix-cache hit: {statistics.mean(cfs)*100:.1f}% avg cached prompt tokens (KEY KPI)")
    else:
        print("prefix-cache hit: endpoint did not return prompt_tokens_details.cached_tokens")
    print("-"*60)
    print("Tip: run once cold, then immediately again — TTFT should drop sharply on the 2nd run if prefix cache works.")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
            f.write("\n")
        print("wrote JSON summary: " + a.json_out)
    # Benchmarking a serve ALSO records its reproducible recipe when asked.
    if a.recipe_out:
        emit_recipe(a, summary)

if __name__ == "__main__":
    main()
