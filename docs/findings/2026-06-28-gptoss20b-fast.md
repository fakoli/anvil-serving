# gpt-oss-20b as the FAST tier on RTX 5090 (sm_120) — 2026-06-28

Evaluated `openai/gpt-oss-20b` (native MXFP4, ~21B MoE, ~3.6B active, **standard
attention**) as the FAST-tier replacement for GLM-4.7-Flash, which preflight
disqualified (crashes on both engines; vLLM dies on long-context compressed-tensors
+ DeepSeek-MLA prefill with `ColumnParallelLinear has no .weight`).

Real results only — every number below came from a live serve. Environment: Windows 11,
Docker Desktop (WSL2), `PYTHONUTF8=1`. Heavy serve (SGLang, GPU 1 / :30000) left
untouched throughout.

## Verdict: VIABLE — yes.

gpt-oss-20b loads and serves cleanly on the RTX 5090 (sm_120) under vLLM nightly, and
**passes every correctness test that GLM failed**, including the exact long-context
prefill regime that crashed GLM. It sidesteps both GLM failure classes by design:
native MXFP4 (no compressed-tensors mismatch) and standard TRITON attention (no MLA
prefill path). Tool-calling works (valid JSON args, 20/20 concurrent clean — the key
sm_120 correctness signal). Throughput is strong for a fast tier (~277 tok/s single
stream, ~1494 tok/s aggregate at 8-way concurrency).

The one caveat is operational, not a model defect: gpt-oss is a **harmony reasoning
model, thinking-on by default**, so the anvil preflight's small `max_tokens` budgets
get fully consumed by reasoning and leave empty content — the known preflight.py
thinking-gap. With adequate tokens or `Reasoning: low`, all tests pass.

---

## Scorecard

### Raw anvil preflight (as-is, exercises the known gaps)
`python -m anvil_serving.cli preflight --base-url http://127.0.0.1:30001/v1 --model gpt-oss-20b --needle-ctx 50000`

| Test | Result | Why |
|------|--------|-----|
| smoke (short coding) | FAIL — empty content | thinking-gap: `max_tokens=80` fully spent on reasoning |
| structured JSON | FAIL — empty content | thinking-gap: `max_tokens=64` |
| needle @ ~50000 ctx | FAIL — empty content | thinking-gap: `max_tokens=40` |
| **shared-prefix tool batch x20** | **PASS — 20/20 clean** | valid tool_calls, **zero sm_120 garbage** |

The 3 FAILs are all the thinking-gap (empty content at tiny `max_tokens`), NOT model
failures. Re-running each with adequate budget / low reasoning, all pass:

### Corrective run (adequate `max_tokens` / `Reasoning: low`)
| Test | Result | Evidence |
|------|--------|----------|
| smoke | **PASS** | `` ```python\nsum(xs)\n``` `` (0.76s) |
| structured JSON | **PASS** | `{"language":"python","ok":true}` (0.18s) |
| **needle @ ~50k (41,361 prompt tokens)** | **PASS — no crash** | returned `ZEBRA-42917-QUARTZ` in 3.74s |
| tool batch x20 | **PASS** | 20/20 valid tool_calls |
| tool call (single) | **PASS** | `get_weather {"city":"Oakland"}` valid JSON |

**Overall: PASS** — the model is correct on sm_120; preflight's literal "FAILURES
PRESENT" is entirely tool/config artifacts (thinking-default + needle-ctx > window +
the localhost IPv6 stall below).

---

## Long-context behavior (the GLM crash test) — DOES NOT CRASH

The decisive test. GLM-4.7-Flash crashed the vLLM engine (HTTP 500, scheduler death)
on long-context MLA prefill. gpt-oss-20b at **41,361 prompt tokens** (a needle near
~50k chars, inside the 65,536 window):

```
[needle ~50k] dt=3.74s prompt_tokens=41361 PASS=True got='ZEBRA-42917-QUARTZ'
```

Clean retrieval, 3.74s, engine healthy afterward. Attention backend in the logs is
`TRITON_ATTN` (standard attention) — the DeepSeek-MLA prefill path that broke GLM is
simply not on the code path here. (The needle @ default 128000 returns HTTP 400 — that
is just exceeding `max-model-len=65536`, expected, not a crash.)

---

## Throughput (live, over 127.0.0.1)

| Metric | Value |
|--------|-------|
| Single-stream decode | **~277 tok/s** |
| Time-to-first-token (single, warm) | **0.44s** |
| Aggregate @ 8-way concurrency | **~1494 tok/s** |
| Model load time | ~15 min (slow 9P bind-mount, ~5 min/shard; one-time) |

VRAM: 31.3 / 32.6 GiB on the RTX 5090 (the `--gpu-memory-utilization 0.90`
reservation; weights are ~12.8 GiB MXFP4, remainder is the KV pool — ample KV for a
65k window). vLLM picked the **MARLIN** MXFP4 MoE backend.

---

## Exact run command (working)

Saved to `scratchpad/gptoss20b-vllm-run.sh`. Core invocation:

```bash
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'
docker rm -f vllm-gptoss 2>/dev/null || true
docker run -d --name vllm-gptoss \
  --gpus all \
  -e CUDA_VISIBLE_DEVICES=GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1 \
  -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
  --ipc=host \
  -p 30001:30001 \
  -v "C:/Users/sdoum/models/gpt-oss-20b:/models/gpt-oss-20b:ro" \
  vllm/vllm-openai:nightly \
  /models/gpt-oss-20b \
  --served-model-name gpt-oss-20b \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.90 \
  --enable-auto-tool-choice \
  --tool-call-parser openai \
  --host 0.0.0.0 --port 30001
```

vLLM version: `0.23.1rc1.dev531+ga65f93fb2`. Quantization auto-detected as
`gpt_oss_mxfp4`; reasoning parser auto-selected as `openai_gptoss`.

---

## Gotchas (new, hard-won this session)

1. **Tool-calling needs the flags.** Without `--enable-auto-tool-choice
   --tool-call-parser openai` you get empty content + zero tool_calls — looks like the
   model can't call tools, but it's the server not extracting harmony tool calls. The
   parser key is `openai` → `GptOssToolParser` (confirmed in
   `vllm/tool_parsers/__init__.py`). After adding it: 20/20 concurrent tool calls clean,
   valid `{"city":"Oakland"}` JSON args.

2. **Thinking-default + small `max_tokens` = empty content.** gpt-oss reasons by
   default; with `max_tokens` ≤ ~80 the whole budget goes to the reasoning channel and
   visible content is empty. This is why anvil preflight (smoke/json/needle use 40–80
   tokens) reports FAIL. Fixes: give adequate tokens, or send a system message
   `Reasoning: low`. This is the same class as the documented Qwen3.5 thinking gap in
   preflight.py — preflight should grow a thinking-aware mode (raise max_tokens / set
   low effort) so it doesn't false-FAIL reasoning models.

3. **Windows localhost IPv6 stall (client-side, ~21s/call).** Python `urllib`
   (which preflight.py uses) resolves `localhost` to IPv6 `::1` first; against Docker's
   `[::]:30001` bind it stalls ~21s per request before falling back to IPv4. `curl` is
   unaffected (happy-eyeballs). Measured: `localhost` 21.37s vs `127.0.0.1` 0.09s for
   the identical call. **Use `http://127.0.0.1:30001` for preflight on Windows** — the
   model and server are fast (sub-2s for 400 tokens); the 21s is purely DNS/connect.
   The first preflight run on `localhost` succeeded but every call paid the 21s tax.

---

## Bottom line

gpt-oss-20b is a **viable FAST tier on the RTX 5090 / sm_120**: it loads (MXFP4/Marlin),
serves, handles 41k-token prefill without the GLM crash, tool-calls correctly under
concurrency, and decodes at ~277 tok/s. Adopt it as the fast pick. Operational notes for
the serve/preflight: enable the tool-call parser, drive it with `Reasoning: low` (or
adequate token budgets) for terse tasks, and hit it over `127.0.0.1` on Windows.
