# Qwen3.8 27B RadixArk NVFP4 DFlash2 qualification on RTX 5090

**Date:** 2026-08-21

**Decision:** `rejected` for the retained 128K upstream contract; `no-promotion`

**Evidence:** `functional`, `capacity`; exact arm `compatibility-only`; complete restoration

**Measured hardware:** one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120
**Topology:** isolated Windows 11 / Docker Desktop / WSL2 qualification lane;
no co-resident or protected GPU workload

## Outcome

The exact official RTX 5090 / Default / NVFP4 / Single Node / DFLASH2 /
High-Throughput / float32 selector booted and served after its required
`--mem-fraction-static 0.945` correction, but it is not a viable replacement
for the retained 128K service on this 32 GB host. The successful start exposed
only 24,347 target KV tokens and a 24,341-token maximum input. The independent
preflight's 105,649-token prompt was rejected before generation.

Four bounded memory arms then isolated the limiting allocations. BF16 Mamba
state at the official 0.90 memory fraction produced 30,984 KV tokens. Raising
only the fraction to 0.945 produced 64,790. Disabling prefill CUDA graphs kept
that same pool size because SGLang creates the KV pool before graph capture,
but it restored 1.00 GB of runtime headroom. Finally, disabling radix caching
and pinning one persistent Mamba slot produced the safe measured ceiling of
70,262 KV tokens. The fixed eight-token DFlash verification path still used
1.12 GB of intermediate BF16 SSM state.

The final BF16/single-slot arm passed an independent complete preflight at its
real envelope: short coding, structured JSON, a 49,549-token retrieval prompt,
and 20/20 semantically valid tool calls. DFlash was active; a scheduler sample
during that gate reported an acceptance length of 5.60 tokens, acceptance rate
0.66, and 45.63 output tokens/s. This is functional/capacity evidence, not a
controlled performance comparison.

The candidate was unloaded after the capacity failure. The exact stock 128K
recipe was restored on the same loopback port and stable upstream served-model
name. Its final preflight passed short coding, structured JSON, the approximately
128K retrieval marker, and 20/20 semantically valid tool calls. No external
router policy, alias, or promotion changed.

## Immutable identity and recipe

- Target model: `RadixArk/Qwen3.8-27B-NVFP4` at
  `554ebba9b5f1b79dc11246341960360e6ef05ef4`.
- Draft model: `incoai/Qwen3.8-27B-DFlash2` at
  `dedf8df68adfb1afeaf7b7480c0a0243108177b4`.
- Runtime image:
  `lmsysorg/sglang:dev@sha256:8acc563e39f4e79118cc3c11cb5a8893ca8da140b2280cdd24a9f3bfe38835a0`.
- Runtime source revision from the image label:
  `f825d729363136a2d4a4b330fa694d0b37a878fa`; image version
  `nightly-dev-20260821-f825d729`.
- Anvil Serving lifecycle source: `1e5ba9e879eb817b996c4ac3066b7a7a17123cd4`
  from the isolated qualification worktree.
- Exact-selector managed recipe:
  `configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-262k-dflash2-recipe.toml`.
- Best measured BF16 single-request diagnostic:
  `configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-dflash2-debug-recipe.toml`.
- Stable upstream served name:
  `qwen38-27b-radixark-nvfp4-sglang-rtx5090-128k-mm`.
- ModelOpt NVFP4 target weights, FP8 E4M3 KV, TP=1, FlashInfer attention,
  DFLASH with eight draft tokens, float32 Mamba state, full-memory ratio 10,
  `extra_buffer_lazy`, concurrency one, thinking disabled, and CPU
  multimodal feature transport.

The target cache was already complete at 21,945,295,265 logical bytes. The
draft was pulled through `anvil-serving models pull` and independently verified
at 3,849,113,948 cached bytes before GPU allocation.

## Official prior and local contract

At SGLang revision `f825d729`, the official selector source says the RTX 5090
float32 DFlash2 arm fits only at memory fraction 0.945 with Mamba full-memory
ratio 10; 0.94 is one state slot short and 0.95 runs out of memory. The same
source describes the measured cell envelope as 8,192 input tokens, 1,024
output tokens, and concurrency one. That official compatibility envelope is
an `external-prior`, not evidence for the existing 128K upstream contract.

The local recipe added immutable target and draft snapshot paths, the stable
served-model name, an explicit 262,144 configured context, the existing WSL2
CPU multimodal transport controls, and a thinking-disabled default. These
additions make the same-port qualification reproducible and preserve the
upstream protocol contract, but they do not convert configured context into
resident KV capacity.

## Attempts and measured capacity

| Attempt | Configuration | Target KV tokens | Result |
|---|---|---:|---|
| Initial transcription | float32, `extra_buffer_lazy`, ratio 10, memory fraction 0.895 | 0 admissible requests | Startup failed after both target and draft loaded: two Mamba cache slots could not satisfy the four persistent slots required per request. |
| Corrected official selector | float32, `extra_buffer_lazy`, ratio 10, memory fraction 0.945 | 24,347 | Started healthy; the 105,649-token gate was rejected at a 24,341-token input ceiling. |
| BF16 official fraction | BF16, `extra_buffer_lazy`, explicit four-slot cache, memory fraction 0.90 | 30,984 | Started healthy; valid but still far below the route contract. |
| BF16 higher fraction | Previous arm with only memory fraction raised to 0.945 | 64,790 | Started healthy; target prefill graph capture consumed 1.13 GB and exhausted in-budget headroom. |
| No prefill graph | Previous arm with only prefill CUDA graphs disabled | 64,790 | Pool size was unchanged, proving graph capture occurs after KV sizing; runtime headroom rose to 1.00 GB. |
| Single-slot/no-radix | Previous arm with radix disabled and the persistent Mamba cache reduced from four slots to one | 70,262 | Complete preflight passed at approximately 60K generated context / 49,549 actual prompt tokens, including tools 20/20. |
| Final restoration | Exact digest-pinned stock 128K recipe | retained 128K capacity | All preflight gates passed, including the 105,649-token marker prompt and tools 20/20. |

The root cause is therefore not an incompatible checkpoint. Target and draft
weights consume 20.14 GB and 3.92 GB respectively before state, KV, and graph
allocations. DFlash adds eight verification states; even with BF16, their
intermediate SSM cache is 1.12 GB. The `--context-length 262144` flag declares
the model limit but cannot create resident KV memory. At the locally proven
0.945 ceiling, the remaining safe single-request pool stops at 70,262 tokens.

For a distinct short-context service, the measured adjustments are BF16 Mamba
state, memory fraction 0.945, one persistent Mamba slot, disabled radix cache,
and disabled prefill CUDA graphs. That profile gives up prefix reuse and prefill
graph acceleration. For the existing 128K service, retain the no-spec recipe.
A future speculative 128K trial should use a method whose Mamba verification
states can be replayed rather than resident (the current SGLang documentation
describes that path for EAGLE/MTP, not DFlash), or use more aggregate VRAM.

## Raw evidence

- [Sanitized qualification and restoration summary](2026-08-21-qwen38-dflash2-rtx5090-evidence/qualification-summary.json)
- [BF16 single-request functional preflight](2026-08-21-qwen38-dflash2-rtx5090-evidence/bf16-single-request-preflight.json)
- [Post-debug baseline restoration preflight](2026-08-21-qwen38-dflash2-rtx5090-evidence/baseline-restoration-preflight-after-debug.json)
- [Reproducible rejected recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-262k-dflash2-recipe.toml)
- [Reproducible best-capacity DFlash2 diagnostic](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-dflash2-debug-recipe.toml)
- [Official selector source at the image revision](https://github.com/sgl-project/sglang/blob/f825d729363136a2d4a4b330fa694d0b37a878fa/docs/src/snippets/configs/Qwen/qwen3.8-27b.jsx)
- [SGLang DFlash integration](https://github.com/sgl-project/sglang/pull/35371)
- [Qwen3.8 DFlash2 support merge](https://github.com/sgl-project/sglang/pull/35496)
- [Pinned draft checkpoint](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2/tree/dedf8df68adfb1afeaf7b7480c0a0243108177b4)

## Caveats and tooling observations

1. This was a functional/capacity preflight, not an `eval benchmark` artifact.
   No stable throughput, TTFT, quality, routed behavior, or multimodal score is
   claimed. Scheduler throughput and acceptance values are diagnostic samples.
2. The configured 262,144-token model context is not the resident request
   capacity of this 32 GB memory layout. The exact selector exposed a 24,341
   input-token limit; the best safe BF16 single-request arm allocated 70,262 KV
   tokens.
3. The runtime repeated the existing warning that FP8 KV scaling factors were
   absent and defaulted to 1.0. No quality equivalence is claimed.
4. The installed Anvil Serving 0.33.1 executable reconstructed old recipes
   without their pinned `serve.model_path`. All mutations and rollback used the
   current 0.34.1 source CLI, which rendered the immutable snapshot correctly.
5. The exact runtime image is digest- and source-revision-pinned, but it is a
   daily development image rather than a released SGLang tag.

## Decision boundary and current-doc impact

Reject both the exact screenshot profile and the memory-tuned DFlash2 arm as
replacements for the qualified 128K RTX 5090 service. Retain both recipes and
the capacity ladder for reproducibility. The tuned arm is a functional
short-context lead only; a future single-stream DFlash tier needs a distinct
truthful served name, controlled speed and quality benchmarks, route admission
limits, and a separate human promotion gate.

The preferred RTX 5090 challenger remains the stock 128K RadixArk NVFP4 recipe.
The failed trial changes neither the current recommendation nor any route.
