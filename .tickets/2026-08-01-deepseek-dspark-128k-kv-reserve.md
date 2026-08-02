# Fit DeepSeek V4 Flash 0731 DSpark within the protected TP2 KV budget

**Observed:** 2026-08-01

## Problem

The pinned DeepSeek V4 Flash 0731 NVFP4 DSpark image loads the target and draft
models successfully on both RTX PRO 6000 Blackwell Max-Q ranks, but the managed
128K profile fails during KV-cache initialization:

- available KV cache per rank: 3.46 GiB;
- KV cache required for 131,072 tokens: 7.37 GiB;
- engine-estimated maximum at `--gpu-memory-utilization 0.94`: 8,232 tokens;
- failure: `ValueError`, followed by managed exit code 1 and automatic rollback.

The exact DSpark NVFP4/MXFP4 routing and fused draft paths engaged before the
failure. This is a capacity failure, not evidence of zero draft acceptance or a
DSpark routing failure.

## Research conclusion

Each card exposes 97,887 MiB and the topology protects 3,072 MiB. Raising vLLM's
memory fraction from 0.94 enough to provide the missing 3.91 GiB would require
approximately 0.981 before allocator margin. That would leave only about 1.8 GiB
outside vLLM and violate the protected reserve. A 0.99 profile would have more KV
margin but less than 1 GiB outside vLLM, so it is not a production-safe default.

DeepSeek V4's vLLM backend prefers a logical 256-token KV block and automatically
selects it when the recipe does not specify a block size. Add `--block-size 256`
explicitly to new profiles for reproducibility, but do not present it as a fix for
this measured shortfall.

## Resolution path

1. Preserve the failed 128K profile and logs as negative evidence.
2. Prove functional DSpark generation and nonzero acceptance with a distinct 8K
   profile at the unchanged 0.94 memory fraction.
3. Measure a protected-reserve context ladder before increasing the memory budget.
4. Prefer reducing target/draft runtime overhead or another fully GPU-resident
   supported cache layout. Do not use CPU weight offload for the main speed lane.
5. Reattempt 128K only when the startup plan proves at least 7.37 GiB of KV cache
   per rank while retaining the declared reserve.

## Acceptance

- The managed 8K target reaches healthy state and the served-name check passes.
- Low/high/max reasoning, tools, streaming, and coding gates still pass.
- Speculative metrics prove accepted length greater than zero; successful text
  generation alone is insufficient.
- Every context step records requested memory, non-KV memory, KV bytes, physical
  headroom, and exact engine/image identity.
- A 128K profile is accepted only if one request fits without weakening the 3 GiB
  reserve and completes the required retrieval and capacity gates.
