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

```yaml
# examples/fakoli-dark/docker-compose.experiment.yml + this override:
entrypoint: ["sh", "-c"]
command:
  - >
    rm -rf /usr/local/lib/python3.12/dist-packages/flashinfer/cute_dsl 2>/dev/null || true;
    exec vllm serve olka-fi/Qwen3.5-122B-A10B-MXFP4 --served-model-name qwen35-122b
    --quantization compressed-tensors
    --reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder --trust-remote-code
    --max-model-len 16384 --max-num-seqs 2 --gpu-memory-utilization 0.90 --kv-cache-dtype fp8
    --host 0.0.0.0 --port 30004
# env: FLASHINFER_CUDA_ARCH_LIST=12.0f, VLLM_USE_V2_MODEL_RUNNER=0, CUDA_VISIBLE_DEVICES=<PRO 6000 UUID>
```

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
