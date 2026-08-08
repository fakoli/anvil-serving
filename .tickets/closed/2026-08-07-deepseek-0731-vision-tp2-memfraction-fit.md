# DeepSeek 0731 Vision TP=2: mem-fraction-static fit ladder

- **Status:** resolved in attempt 7 (0.97 mem-fraction + persistent JIT volume + skip-warmup + multimem-gatherer patch; healthy serve verified)
- **Lane:** tp2-deepseek-0731-vision-nvfp4-sglang-4k (SGLang v0.5.16-cu130 digest-pinned, marlin/marlin, 4096 ctx)

## Attempt 1 (2026-08-07 08:41–08:45)

`serves mode enter` drained/stopped the 650K Primary and started the candidate.
Weights loaded fully on both ranks (Load weight begin: avail 93.60 GB/rank),
then the scheduler exited:

```
ValueError: Loaded weights leave no GPU memory for the KV cache under
--mem-fraction-static=0.94. Raise --mem-fraction-static above 0.942
(minimum viable = 1 - available/pre = 0.9411).
```

The transaction preserved the exited container and left exclusive admission
closed (Primary intentionally down for the approved campaign window).

## Diagnosis

Not a defect: measured weight residency for the ~175.6 GB mixed FP8/NVFP4
checkpoint TP=2-sharded is 94.11% of the 96 GB card. The intake-note fit
prediction (~88 GB/card) was correct; 0.94 static fraction was below the
floor the engine measured.

## Fix

Recipe `MEM_FRACTION_STATIC` 0.94 -> 0.96 (~1.9 GB/card KV+runtime budget at
4096 ctx c1; MLA KV is compact). Balance risk: the SGLang TokenizerManager
opens an unaccounted CUDA context on GPU 0, so overshooting the static
fraction risks GPU-0 OOM — step in +0.01/0.02 increments, not straight to 0.98.

## Attempt 2 (2026-08-07 08:46–08:49)

`MEM_FRACTION_STATIC=0.96`: weights loaded, KV floor (0.942) cleared, but the
pool allocation still failed — `RuntimeError: Not enough memory. Please try to
increase --mem-fraction-static.` Non-static consumers (BF16 vision tower +
projector ~0.9 GB, DSV4 indexer transient buffers, TokenizerManager CUDA
context on GPU 0) squeeze the pool from both sides. Attempt 3: 0.97.

## Attempt 3 (2026-08-07 08:50–09:0x) — loads, then detokenizer heartbeat wedge

0.97 fit: weights 88.08/87.87 GB per card, `The server is fired up and ready`,
Uvicorn on :39070. NO silent ext-import fallback (marker absent); hybrid
FP8+NVFP4 auto-detected; marlin/marlin active; kv fp8_e4m3.

Then: health checks fail forever with `Server couldn't get a response from
detokenizer for last 20 seconds ... last_heartbeat 08:51:01` (frozen at the
detokenizer's first beat, during early init while TP ranks were loading
weights). A /generate request is accepted by HTTP but never reaches the GPUs
(0% util). Detokenizer process: alive, 0% CPU, 67 threads, main thread parked
in poll. The mode-enter health gate therefore timed out and stopped the
container (preserved).

Isolated repro (same image/volume/env, CPU-only AND --gpus all, including the
exact TokenizerManager path `import_processors` + `get_mm_processor` with
`_get_processor_wrapper`/`_determine_tensor_transport_mode`): ALL STEPS PASS
in seconds. Conclusion: not deterministic init breakage — a startup
race/contention wedge between TokenizerManager ext-processor init, detokenizer
IPC bring-up, and concurrent 88 GB/rank weight load.

Also recorded: this tokenizer has `chat_template=None` (native encoding is
custom) — OpenAI chat endpoint behavior must be verified separately; config's
image_token_id=129280 is deliberately out-of-vocab (transformers warning is
benign).

## Attempt 4 (2026-08-07 09:05–09:1x) — ROOT CAUSE FOUND: first-request marlin JIT compile

Identical-config retry reproduced the symptom. SIGABRT on scheduler TP0
(faulthandler enabled upstream) captured the definitive stack: the scheduler
was mid-forward-pass in `fused_marlin_moe -> moe_wna16_marlin_gemm ->
load_jit -> tvm_ffi build_inline -> build_ninja -> subprocess ninja` — i.e.
the FIRST marlin NVFP4 MoE call JIT-compiles its kernel with ninja inside the
container (~133% CPU per rank = the compile, GPU 0%). The "no response from
detokenizer" health message is a misnomer: /health fires a 1-token generate
whose completion path is scheduler->detokenizer->tokenizer-manager, so a
scheduler busy in a long compile freezes `last_receive_tstamp` (which
initializes at process start — the 08:51:01 "heartbeat" was never a beat).
`--skip-server-warmup` makes readiness a lie by construction (research:
sglang#20836 mechanism, health path confirmed from v0.5.16 source).

Fixes applied to the recipe:
1. `named_volumes += deepseek-0731-vision-jit:/root/.cache/tvm-ffi`
   (`TVM_FFI_CACHE_DIR` default; tvm_ffi cache keys are arch/ABI-aware and
   designed for shared volumes) — compile once, reuse across restarts.
2. Dropped `--skip-server-warmup` so readiness includes the first real
   generate (the compile) and the transaction health gate measures truth.
3. `startup_timeout_seconds` 1800 -> 4200 to accommodate the one-time compile.

## Attempt 5 (2026-08-07 09:13–09:17) — stock warmup incompatible

Dropping `--skip-server-warmup` made SGLang's built-in warmup run and fail
immediately: `AssertionError: {"object":"error","message":"texts cannot be
empty and tokenizer must be initialized","type":"BadRequestError"}` →
initialization failed, scheduler killed. The vendor's launch wrapper skips
warmup deliberately; the stock warmup request shape is incompatible with this
external-processor integration. Resolution: restore `--skip-server-warmup`;
rely on the serve health gate's own 1-token generate to drive the first
compile against the now-persistent JIT volume (health only returns 200 once
the full pipeline works, which is an honest readiness signal).

## Attempt 6 (2026-08-07 09:18–09:2x) — marlin JIT completed; SIGFPE in symm-mem logits gather

With the JIT volume and health-driven warmup, the first health generate
compiled the marlin MoE kernel (persisted to deepseek-0731-vision-jit) and ran
the full 43-layer forward — then crashed hard: `Fatal Python error: Floating
point exception` inside `LogitsProcessor._get_logits ->
triton_symm_mem_ag.MultimemAllGatherer -> torch.distributed._symmetric_memory
rendezvous` (line 1991). Torch symmetric-memory/multicast rendezvous SIGFPEs
at C level on this WSL2 + PCIe (no NVLink/fabric) sm_120 pair, so the class's
try/except NCCL fallback never gets the chance to fire. v0.5.16 has no env or
server-arg kill switch for the gatherer (`enabled` is derived solely from TP
topology), and torch exposes no disable env for multicast rendezvous.

Fix: third narrow source patch applied fail-closed at container start
(exact-anchor grep count + sed, idempotent): force `MultimemAllGatherer(...,
enabled=False)` in logits_processor.py so `_state=None` and every call takes
the battle-tested `tensor_model_parallel_all_gather` NCCL path. Upstreamable:
this deserves an sglang issue (guard rendezvous by multicast support probe or
add a kill-switch env).

## Follow-ups

- Record the measured per-card weight residency in the finding and dossier.
- Long-context lanes on this checkpoint under TP=2 will need KV headroom that
  does not exist at this quant mix; treat 4096–8192 as the realistic ceiling
  unless a smaller-footprint quant lane is used.
