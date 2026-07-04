# Blackwell sm_120 lab notebook

A running record of which models serve (and how) on the fakoli-dark consumer-Blackwell box
(RTX 5090 sm_120 + RTX PRO 6000 96 GB sm_120), and the hard-won engine flags that make them work.
See `CLAUDE.md` gotchas #10–#19 for the cross-cutting rules.

## Results table

| model | quant | engine | GPU | serves? | key flags | notes |
|---|---|---|---|---|---|---|
| gpt-oss-120b | MXFP4 (W4A16) | vLLM :nightly | PRO 6000 | ✅ | `FLASHINFER_CUDA_ARCH_LIST=12.0f`, no `--enforce-eager` | 183 tok/s; MoE backend = Marlin |
| Qwen3.6-27B | NVFP4 | vLLM :nightly | 5090 | ✅ | `modelopt_fp4` + `FLASHINFER_CUDA_ARCH_LIST=12.0f` + qwen3 parsers | hybrid GDN; 0.70 planning |
| Qwen3-32B | NVFP4 | vLLM :latest | 5090/PRO6000 | ✅ | RedHatAI compressed-tensors, omit `--quantization` | dense; 340 tok/s |
| **Qwen3.5-122B-A10B** | **MXFP4 (W4A4)** | **vLLM :nightly → Marlin W4A16** | **PRO 6000** | **✅ (T016)** | **remove `flashinfer/cute_dsl` → forces Marlin fallback** | **hybrid GDN; preflight ALL PASS** |
| Qwen3.5-122B-A10B | NVFP4-MoE | vLLM standard | — | ❌ | — | garbage/crash on sm_120 (gotcha #16) |
| **Nemotron-3-Super-120B-A12B** | **NVFP4 (native)** | **vLLM :nightly (FlashInfer-CUTLASS)** | **PRO 6000** | **✅** | **`--attention-backend TRITON_ATTN` + `--mamba-ssm-cache-dtype float16` + `--max-num-seqs ≤940`; OMIT `--speculative-config` (MTP OOMs)** | **hybrid LatentMoE (Mamba-2+MoE); preflight 3/4 (JSON needs the `super_v3` reasoning parser); slow load (74.8 GB > 53 GB WSL RAM)** |

---

## T016 — Qwen3.5-122B-A10B on sm_120 via vLLM Marlin W4A16 (2026-07-04)

**Checkpoint:** `olka-fi/Qwen3.5-122B-A10B-MXFP4` (74 GB in `vllm-hfcache`). Expert `gate_up`/`down_proj`
+ shared experts are **MXFP4** (e8m0, block 32); attention, **Gated DeltaNet**, router gate, embeddings,
LM head, **MTP** layers stay BF16. This is the flagship 122B MoE, flexibility-only (crashes on standard
vLLM NVFP4-MoE — the T016 premise).

### The blocker (standard vLLM, W4A4 path)
vLLM classifies this checkpoint as **W4A4 MXFP4** (activations also 4-bit) and routes the linear layers
to FlashInfer's `mm_fp4` **cute-dsl** backend, which **dies at engine init on sm_120**:

```
flashinfer.utils.BackendSupportedError: mm_fp4 does not support backend 'cute-dsl' with capability 120
RuntimeError: Engine core initialization failed.
```

Root cause: `FlashInferMxFp4LinearKernel.is_supported()` gates on `has_device_capability(100)` — TRUE for
sm_120 (120 ≥ 100) — so it is SELECTED, then the cute-dsl kernel (sm_100-only) fails at apply. The FP4
cute-dsl kernels are simply not built for the consumer sm_120 `mma.*` ISA (gotcha #16).

### The fix — force the Marlin W4A16 fallback
vLLM's `compressed_tensors_w4a4_mxfp4` scheme is **designed** to fall back:
> *"On SM100+ with FlashInfer: true W4A4. Otherwise: W4A16 weight-only via Marlin."*

`has_flashinfer_cutedsl()` is `has_flashinfer() and importlib.util.find_spec("flashinfer.cute_dsl") is not None`
— no env toggle. So make the module un-importable at container start (it is broken on sm_120 anyway):

The full, reproducible recipe — **with the required env vars actually set** (`FLASHINFER_CUDA_ARCH_LIST=12.0f`,
`VLLM_USE_V2_MODEL_RUNNER=0`, `CUDA_VISIBLE_DEVICES=<PRO 6000 UUID>`), GPU pinning, and the volume — is
**`examples/fakoli-dark/docker-compose.flexibility.yml`**. Do NOT hand-assemble it from a partial snippet
(a missing `FLASHINFER_CUDA_ARCH_LIST` re-breaks engine init). Its sm_120-specific core is the entrypoint:

```yaml
entrypoint: ["sh", "-c"]
command:
  - >
    rm -rf /usr/local/lib/python*/dist-packages/flashinfer/cute_dsl
    /usr/local/lib/python*/site-packages/flashinfer/cute_dsl 2>/dev/null || true;
    exec vllm serve olka-fi/Qwen3.5-122B-A10B-MXFP4 --quantization compressed-tensors
    --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder --trust-remote-code
    --max-model-len 16384 --gpu-memory-utilization 0.90 --kv-cache-dtype fp8 --host 0.0.0.0 --port 30004
```
(the `python*` glob keeps the `rm` working if the image's python version/layout changes.)

With cute_dsl gone the scheme logs and takes the working path:
> *"Weight-only FP4 compression will be used leveraging the **Marlin kernel**."*

The hybrid Gated-DeltaNet loaded cleanly (no prefill-workspace overflow at 16K); MTP stays off (no
speculative decoding requested).

### Result — CORRECTNESS PREFLIGHT: ALL PASS
`anvil-serving preflight --base-url http://127.0.0.1:30004/v1 --model qwen35-122b --no-thinking --needle-ctx 14000`
- ✅ smoke (short coding) · ✅ structured JSON (`keys=['language','ok']`) · ✅ needle @14k (`ZEBRA-42917-QUARTZ`) · ✅ tool batch 20/20 clean.
- Load ~690 s; **87 GB / 96 GB** on the PRO 6000; GPU KV cache **678,765 tokens** (41× concurrency @16k).

**Caveats / next:**
- **`--no-thinking` is required for structured output** — default thinking starves small-budget JSON/tool
  replies to empty content (gotcha #6/#9). preflight's `--no-thinking` injects `enable_thinking=false`.
- **Marlin is W4A16 "quant-ignore"** (activations upcast to bf16) — vLLM warns it "may degrade performance
  for compute-heavy workloads". Correct, just not the theoretical W4A4 speed.
- `--max-model-len 16384` here to be conservative; the 678k-token KV pool easily holds 128k
  (`678765/131072 ≈ 5×`), so re-serve at `--max-model-len 131072` for the full 128k-needle gate.
- This proves the **any-engine seam on the hardest case**: a flexible engine (patched vLLM) serves the
  flagship 122B correctly where the default NVFP4-MoE path is dead. ktransformers not needed for this checkpoint.

---

## Nemotron-3-Super-120B-A12B-NVFP4 on sm_120 (2026-07-04)

**Checkpoint:** `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` (74.8 GB in `vllm-hfcache`). A
**hybrid LatentMoE** — interleaved **Mamba-2** (state-space) + MoE + select attention layers — with
**MTP** (multi-token-prediction / speculative) layers, trained natively at **NVFP4** (NVIDIA
Model-Optimizer checkpoint). Up to 1M context. Notably this is a *different* beast from the Qwen MoE
that fails on sm_120 (gotcha #16): its NVFP4-MoE path actually WORKS here.

### It SERVES on the RTX PRO 6000 (contrary to gotcha #16's MoE-NVFP4 pessimism)
Selected kernels at load: **`FlashInferCutlassNvFp4LinearKernel`** (linear GEMM) + **`FLASHINFER_CUTLASS`
NvFp4 MoE backend** + **`TRITON_ATTN`** attention + **Mamba-2 SSD Triton** kernels. ~73 GB resident on
the 96 GB card. So the CUTLASS NVFP4 grouped-GEMM that produced garbage for Qwen-MoE-NVFP4 serves this
NVIDIA-native checkpoint correctly — the checkpoint's own quant recipe matters, not just "NVFP4-MoE".

### The recipe (RTX PRO 6000; from the model card + HF discussions #7/#9)
```bash
docker run -d --name vllm-nemotron-30005 --ipc host --gpus device=<PRO6000-UUID> \
  -e VLLM_USE_V2_MODEL_RUNNER=0 -e FLASHINFER_CUDA_ARCH_LIST=12.0f -e CUDA_DEVICE_ORDER=PCI_BUS_ID \
  -v vllm-hfcache:/root/.cache/huggingface vllm/vllm-openai:nightly \
  --model nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4 --served-model-name nemotron-3-super \
  --dtype auto --kv-cache-dtype fp8 --attention-backend TRITON_ATTN \
  --mamba-ssm-cache-dtype float16 --trust-remote-code \
  --max-model-len 32768 --max-num-seqs 256 --gpu-memory-utilization 0.92 \
  --async-scheduling --enable-chunked-prefill \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder --host 0.0.0.0 --port 30005
```
Two failure modes hit, both benign config (NOT sm_120/NVFP4/Mamba hazards):
- `vllm: error: unrecognized arguments: --swap-space 0` — the nightly dropped `--swap-space`; remove it.
- `ValueError: max_num_seqs (1024) exceeds available Mamba cache blocks (940)` at cudagraph capture —
  the hybrid Mamba-2 needs ONE cache block per decode seq; cap `--max-num-seqs ≤ 940` (used 256).
- **OMIT `--speculative-config`** (MTP): MTP spec-decode consumes 20 GB+ at startup and OOMs the
  6000 Pro (HF disc #9); dropping it lands the footprint at ~73–77 GB.
- `--dtype auto` (NVFP4 auto-detected; do NOT pass `--quantization modelopt_fp4` for this ckpt).
  `VLLM_NVFP4_GEMM_BACKEND=marlin` did NOT override the CUTLASS selection — it ran CUTLASS and worked.

### Correctness + quality
`anvil-serving preflight ... --model nemotron-3-super --needle-ctx 14000` → **3/4 PASS**: smoke
(coding) ✅, 14k needle ✅, 20/20 tool batch ✅; **structured JSON ✗ only because no reasoning parser**
was set — the model's chain-of-thought bleeds into `content` before the JSON (add
`--reasoning-parser super_v3` + its plugin file to separate it). So a correctness ✅ with a
serving-completeness caveat, not a model defect.

Quality gut-check — an adversarial eval workflow (independent judge + skeptic per probe → synthesis)
over a distributed-rate-limiter **planning** probe + a thread-safe-LRU-cache **coding** probe:
- **Coding: 0.82, SURVIVES refutation.** A correct, genuinely thread-safe, O(1) LRU (dict + doubly-linked
  list, single lock). The skeptic actually ran an 8-thread × 20k-op stress test — zero corruption. Only
  cosmetic prose errors (it wrongly says `get` "copies" the value; it returns the reference — which is fine).
- **Planning: 0.45 after refutation.** Well-structured (5 steps, a sound accuracy/latency/memory tradeoff
  table) BUT its centerpiece — a Redis Lua "atomic token bucket" it labels THE correctness solution — is
  **non-functional**: undefined global `ttl` (won't even execute), never stores `last_refill` (so not a
  time-based bucket), prose contradicts code, and a real cross-node over-limit race under its "atomicity is
  non-negotiable" claim. The single judge gave 0.6; only the adversarial skeptic caught the fatal artifact.
- **Overall 0.6 — coding-competitive but reasoning-shaky on this evidence.** On a planning-primary heavy
  tier, 0.45 sits far below the gpt-oss-120b incumbent's measured **0.92-at-high**. **Verdict: NOT worth an
  A/B yet.** First fix the two blockers that make this probe both unfair and operationally unfit: (1) the
  missing `super_v3` reasoning parser (the preflight JSON fail — reasoning bleeding into `content`
  plausibly DEPRESSED the 0.45 planning output), and (2) the 74.8 GB > 53 GB WSL-RAM load. Add the parser,
  raise WSL memory, re-probe planning, and only A/B if planning closes materially toward 0.92.

### Caveats / next
- **Slow cold load (~7 min):** the 74.8 GB checkpoint exceeds WSL2 RAM (53 GB) so vLLM can't
  page-cache/prefetch it — reads shard-by-shard from ext4 (~25–30 s/shard). Raising `.wslconfig`
  `memory=` toward the checkpoint size would help (gotcha #3).
- **KV budget is tight:** ~73–77 GB weights on 96 GB leaves ~19 GB → `--max-model-len 32768` here;
  the 1M-context claim is not reachable on a single 6000 Pro.
- **Coexistence:** ~74 GB means it needs the PRO 6000 to ITSELF — cannot run alongside the
  gpt-oss-120b heavy tier (both ~74 GB). A tier swap, not a colocation.
- For a real tier, wire the `super_v3` reasoning parser (separates reasoning → `reasoning_content`)
  and run a measured A/B vs the gpt-oss-120b incumbent on the same planning board.
