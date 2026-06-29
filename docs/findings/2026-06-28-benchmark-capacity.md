# Benchmark: capacity + context/KV headroom (two live serves)

Date: 2026-06-28. Tool: `anvil_serving/benchmark.py` (stdlib urllib streaming, measures TTFT/E2E/aggregate decode tok/s + prefix-cache signal). All runs `PYTHONUTF8=1`, against `127.0.0.1` (never `localhost` — that resolves to `::1` on Windows and stalls urllib ~21s/call).

## Topology (real, from `nvidia-smi`)
| GPU | Card | VRAM | Hosts |
|-----|------|------|-------|
| 1 | RTX PRO 6000 Blackwell Max-Q | 97887 MiB (~96 GB) | HEAVY — SGLang, Qwen3-Coder-30B-AWQ |
| 0 | RTX 5090 | 32607 MiB (~32 GB) | FAST — vLLM, gpt-oss-20b (mxfp4) |

The two tiers are on **separate GPUs**, so there is no cross-tier KV/VRAM contention. (Benchmarks were still run sequentially to avoid CPU/PCIe noise.)

- HEAVY: `http://127.0.0.1:30000/v1`, model `qwen3-coder-local` (SGLang, non-thinking).
- FAST: `http://127.0.0.1:30001/v1`, model `gpt-oss-20b` (vLLM, reasoning-default — needs `max_tokens >= 256` or visible content is empty; not a failure).

---

## T001 — Capacity

### HEAVY (SGLang / Qwen3-Coder-30B-AWQ, GPU1)
| Scenario | n / conc | TTFT p50/p95 | E2E p50/p95 | Aggregate tok/s | Notes |
|----------|----------|--------------|-------------|-----------------|-------|
| single-stream (ctx~4k, max256) | 1 / 1 | 0.23 / 0.23 s | 0.66 / 0.66 s | **82** | clean single-stream decode rate |
| burst 1 (shared8k, ctx~16k, max64) | 1 / 1 | 1.50 / 1.50 s | 2.15 / 2.15 s | 30 | cold prefill of 16k-ctx |
| burst 8 cold | 8 / 8 | 0.40 / 0.48 s | 1.26 / 1.26 s | 406 | |
| burst 8 warm (immediate re-run) | 8 / 8 | 0.32 / 0.46 s | 1.16 / 1.22 s | 408 | TTFT p50 dropped 0.40→0.32 (radix prefix cache warm) |
| burst 20 | 20 / 20 | 0.64 / **1.69** s | 1.62 / 2.43 s | **483** | exceeds `max_running_requests=16` → 4 queue; p95 TTFT climbs, no errors |

Single-stream decode ~82 tok/s; aggregate scales 82 → 406 → 483 tok/s across 1/8/20 streams.
**Max sustained concurrency:** 16 (hard scheduler cap `max_running_requests=16`). Beyond 16 it queues and degrades gracefully (burst-20 p95 TTFT 1.69 s) — no errors, no blowup. KV is nowhere near the limit at these context sizes (see T002).

### FAST (vLLM / gpt-oss-20b, GPU0)
| Scenario | n / conc | TTFT p50/p95 | E2E p50/p95 | Aggregate tok/s | Notes |
|----------|----------|--------------|-------------|-----------------|-------|
| single-stream (ctx~4k, max512) | 1 / 1 | 0.72 / 0.72 s | 0.85 / 0.85 s | 34 | content-only tok/s (see caveat) |
| burst 1 (shared8k, ctx~16k, max512) | 1 / 1 | 1.44 / 1.44 s | 1.65 / 1.65 s | 28 | |
| burst 8 cold | 8 / 8 | 1.27 / 1.43 s | 1.59 / 1.76 s | 179 | |
| burst 8 warm | 8 / 8 | 1.30 / 1.38 s | 1.62 / 1.74 s | 186 | |
| burst 20 | 20 / 20 | 2.62 / **3.04** s | 3.14 / 3.43 s | 220 | ~547k KV tokens in flight (20 × ~27k) ≈ KV budget 560k — near saturation, TTFT climbs, still 20/20 OK |

**Max sustained concurrency:** ~8 at full 65k context (KV hard limit 8.56x — see T002); at moderate contexts (~27k tok/req) ~20 requests fit KV and all completed, but TTFT had already climbed to ~3 s. No errors at burst 20.

> **FAST measurement caveats (important — these numbers under-state the engine):**
> 1. gpt-oss is reasoning-default. The benchmark counts only `delta.content` tokens, so **reasoning/CoT tokens are excluded** from the aggregate tok/s — true decode is higher. vLLM's own log showed `Avg generation throughput: 320 tok/s` at 8 running reqs vs the 179–186 the tool reports.
> 2. The tool's "TTFT" = time to first **visible content** token, which for a reasoning model is *after the entire chain-of-thought*. So FAST "TTFT" is really time-to-first-answer-token, not prefill latency — that is why it is higher than HEAVY and grows under load.

### Prefix cache
Neither endpoint returns `usage.prompt_tokens_details.cached_tokens` over the OpenAI-compatible API, so the tool prints "endpoint did not return …" for both — the **API-level KPI is not observable** with this tool as written. Indirect evidence that prefix caching is active:
- HEAVY: warm burst-8 re-run TTFT p50 dropped 0.40→0.32 s (SGLang radix cache).
- FAST: vLLM startup/run logs report `Prefix cache hit rate` climbing to 51–67% during warmup; `enable_prefix_caching=True`.

---

## T002 — Context / KV headroom

### HEAVY (SGLang) — from `docker logs sglang`
```
context_length=131072, mem_fraction_static=0.88, max_running_requests=16, kv_cache_dtype=fp8_e5m2
KV Cache is allocated. dtype: torch.float8_e5m2, #tokens: 1428864, K size: 32.70 GB, V size: 32.70 GB
max_total_num_tokens=1428864, chunked_prefill_size=2048, max_prefill_tokens=16384, context_len=131072
```
- **KV token budget: 1,428,864 tokens** (fp8 KV, ~65.4 GB on the 96 GB card).
- 1,428,864 / 131,072 = **10.9** → ~10 concurrent *max-context (131k)* requests fit in KV.
- At the measured subagent p50 context (~65,536): 1,428,864 / 65,536 = **~21.8** concurrent fit in KV — but the scheduler caps at `max_running_requests=16`.
- **Binding constraint at realistic contexts is `max_running_requests=16`, not KV.** KV headroom is generous.

**Recommendation — keep configured context at 131072.** KV sustains all 16 running slots even if every slot averages ~89k tokens (16 × 89k ≈ 1.43M = the whole pool), so OOM is not the risk. The real limiter at the top of the range is **prefill compute**: the empirical near-max request (~125k tokens) took **TTFT 35.0 s / E2E 37.3 s** at 2 tok/s (chunked prefill 2048). So 131072 is safe to leave configured, but agents should treat ~131k as a worst-case latency corner. If more fan-out is wanted, `max_running_requests` could be raised toward ~20 (KV allows it at p50 contexts) rather than cutting context.

### FAST (vLLM gpt-oss) — from `docker logs vllm-gptoss`
```
max_model_len 65536, gpu_memory_utilization=0.9, enable_prefix_caching=True, kv_cache_dtype=auto (bf16)
Available KV cache memory: 13.27 GiB
GPU KV cache size: 560,887 tokens
Maximum concurrency for 65,536 tokens per request: 8.56x
```
- **KV token budget: 560,887 tokens.**
- 560,887 / 65,536 = **8.56** concurrent *max-context* requests before KV preemption/OOM (vLLM states this directly).
- Empirically corroborated: burst-20 at ~27k tok/req ≈ 547k tokens in flight ≈ the 560k budget, and that is exactly where TTFT started climbing (2.6–3.0 s).
- Near-max single request (~60k tokens) succeeded cleanly: **TTFT 5.4 s / E2E 5.6 s**.

**Recommendation for the FAST/fan-out tier — reduce `max_model_len` to 32768.** This is the fan-out tier; concurrency matters more than a 64k window. 560,887 / 32,768 = **17x** concurrent (vs 8.56x at 65k), which comfortably covers an 8–16-wide subagent wave without KV preemption, and 32k still exceeds the typical fast-tier request size. If 64k single-session context is a hard requirement, keep 65536 and accept the **~8-way concurrency ceiling** (or a middle ground of 40960 → ~13.7x).

### Context-enforcement check (and a tool bug found)
Both serves correctly reject over-length input with HTTP 400 (real bodies captured):
- HEAVY: `The input (228999 tokens) is longer than the model's context length (131072 tokens).`
- FAST: `Input length (94062) exceeds model's maximum context length (65536).`

> **Finding / flag — `benchmark.py` `--ctx-tokens` heuristic is badly miscalibrated for code filler.** It maps `ctx_tokens` via `words = ctx_tokens * 0.75`, but the `FILLER` line tokenizes to far more tokens than that word count implies. Measured real/heuristic ratio: **~2.08x (HEAVY)** and **~1.71x (FAST)**. So `--ctx-tokens 110000` actually sent 228,999 tokens. Any long-context run silently overshoots the model limit and 400s (and the error line prints *above* the summary block, so it is easy to miss with `tail`). To hit a true ~125k-token HEAVY prompt you must pass `--ctx-tokens ~60000`; for ~60k FAST, `--ctx-tokens ~35000`. Recommend fixing the heuristic (or tokenizing the built prompt and trimming to the requested count) so `--ctx-tokens` means what it says.

---

## Exact commands run
```bash
export PYTHONUTF8=1   # all runs

# --- T001 HEAVY ---
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30000/v1 --model qwen3-coder-local --requests 1 --concurrency 1 --ctx-tokens 4000 --max-tokens 256
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30000/v1 --model qwen3-coder-local --burst 1  --shared-prefix-tokens 8000 --ctx-tokens 16000 --max-tokens 64
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30000/v1 --model qwen3-coder-local --burst 8  --shared-prefix-tokens 8000 --ctx-tokens 16000 --max-tokens 64   # run twice: cold then warm
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30000/v1 --model qwen3-coder-local --burst 20 --shared-prefix-tokens 8000 --ctx-tokens 16000 --max-tokens 64

# --- T001 FAST (max-tokens >= 256 for reasoning-default) ---
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30001/v1 --model gpt-oss-20b --requests 1 --concurrency 1 --ctx-tokens 4000 --max-tokens 512
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30001/v1 --model gpt-oss-20b --burst 1  --shared-prefix-tokens 8000 --ctx-tokens 16000 --max-tokens 512
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30001/v1 --model gpt-oss-20b --burst 8  --shared-prefix-tokens 8000 --ctx-tokens 16000 --max-tokens 512   # run twice: cold then warm
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30001/v1 --model gpt-oss-20b --burst 20 --shared-prefix-tokens 8000 --ctx-tokens 16000 --max-tokens 512

# --- T002 near-max long-context (corrected for the ~2x/1.7x heuristic skew) ---
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30000/v1 --model qwen3-coder-local --requests 1 --concurrency 1 --ctx-tokens 60000 --max-tokens 64    # ~125k real tokens -> TTFT 35.0s
python anvil_serving/benchmark.py --base-url http://127.0.0.1:30001/v1 --model gpt-oss-20b      --requests 1 --concurrency 1 --ctx-tokens 35000 --max-tokens 512   # ~60k real tokens  -> TTFT 5.4s

# T002 KV/context facts: docker logs sglang | grep KV/max_total; docker logs vllm-gptoss | grep "KV cache"/"Maximum concurrency"
```

## Anomalies / flags
1. **benchmark.py `--ctx-tokens` heuristic ~2x off** (HEAVY) / ~1.7x off (FAST) — see finding above. Real bug; long-context runs overshoot and 400 silently. Fix recommended.
2. **Prefix-cache KPI not observable via this tool** — neither SGLang nor vLLM returns `prompt_tokens_details.cached_tokens` over the OpenAI API here. Prefix caching *is* active (warm TTFT drop on HEAVY; vLLM prefix-hit-rate in logs), but the tool's headline KPI line always says "did not return". Consider reading SGLang/vLLM `/metrics` or vLLM's logged `Prefix cache hit rate` instead.
3. **FAST throughput/TTFT understated** by the reasoning-default behavior (content-only token counting; TTFT measured post-CoT). True decode ~320 tok/s per vLLM logs vs ~180 reported.
