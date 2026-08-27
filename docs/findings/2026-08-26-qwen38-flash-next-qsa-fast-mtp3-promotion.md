# Qwen3.8 Flash Next QSA-fast MTP3 fix-forward promotion

**Date:** 2026-08-26

**Scope:** dual RTX PRO 6000 Max-Q, WSL2, exclusive TP=2, text Primary

**Decision:** current at 262,144 tokens and concurrency one; multimodal remains unpromoted

## Outcome

The human-authorized fix-forward promotion now uses
`RadixArk/Qwen3.8-Flash-Next-NVFP4@7b719225242aacd3dbd3f9407468c2ee9a9d2594`
through the digest-pinned SGLang image
`sha256:59f06adce6f91401adf443bd168d45fdb2044d77671fd591c7c57a29d851cbae`,
engine revision `d91c3682b0b429e4c70df63cd57f819588ce29b0`. The current recipe is
[`qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-mtp3-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/qwen38-flash-next-radixark-nvfp4-sglang-sm120-qsa-fast-tp2-262k-mtp3-recipe.toml).

This supersedes the same-day portable-QSA reference. It keeps the 262,144-token
server window, 253,952-token client prompt contract, 8,192-token output reserve,
concurrency one, BF16 KV path, no PLE CPU offload, and thinking-disabled text
contract. It does not promote image, OCR, or video routes.

## Exact SM120 translation

The pinned runtime predated the upstream SM120 Qwen sparse-attention fix. The
managed recipe applies only the QSA gate from SGLang PR #36556 at commit
`dac5523d1e5d2f4297fec40ef02fc76fb0f662d1`. Startup verifies the exact source
hash `c959835d...`, the exact patched result `a6b003ed...`, and the required
FlashInfer `trtllm_batch_decode_with_kv_cache` import before launching. It also
retains the already-qualified WSL2 NCCL fallback, Triton attention/prefill,
FlashInfer linear-attention decode, BF16 recurrent state, batch-one graph cap,
and no FP8 KV. These are exact-revision compatibility selections, not generic
SGLang advice.

The matched no-speculation control and MTP3 candidate are identical except for
SGLang NEXTN steps/top-k/draft tokens `3/1/4`, matching the upstream low-latency
cookbook.

## Local qualification

| Profile | 4K TTFT / E2E | 4K decode | 128K TTFT | 128K prefill | 128K decode | Full-reserve decode |
|---|---:|---:|---:|---:|---:|---:|
| Prior portable QSA, no spec | 0.215 / 2.627 s | 12.801 tok/s | 11.961 s at 125,442 | 10,488 tok/s | 12.544 tok/s | not repeated |
| QSA-fast, no spec | 0.14 / 0.69 s | 66.6 tok/s | 12.18 s | 10,057 tok/s | 69.4 tok/s | 66.9 tok/s |
| **QSA-fast, MTP3** | **0.15 / 0.36 s** | **154.9 tok/s** | **12.67 s** | **9,664 tok/s** | **134.1 tok/s** | **102.0 tok/s** |

MTP3 improved decode by 2.33x at 4K and 1.93x at 128K versus the otherwise
matched no-speculation control. Against the former portable-QSA lane, the
decode gains were 12.1x and 10.7x. The full-reserve request measured 253,703
actual prompt tokens with an 8,192-token output request inside the native
262,144-token window.

Both QSA-fast arms passed the direct functional suite, 128K retrieval, the
full-reserve request, tools 20/20, thinking-disabled intelligence 6/6, session
continuation 3/3, and repeated tools 3/3. No bounded quality regression was
observed in the MTP3 arm. This is bounded deterministic evidence, not a broad
intelligence claim.

## Routed and real-client closure

The router transition matched the exact MTP3 served identity and passed
readiness plus admission checks. A fresh client run then synchronized the
installed catalog and exercised the normal binaries:

- Hermes returned the fresh marker through `llm.primary` with no raw tool-call
  markup.
- Pi selected provider `anvil`, model `llm.primary`, returned the fresh marker,
  and retained the catalog's 262,144-token context.
- OpenClaw selected provider `anvil`, model `llm.primary`, reported
  `contextTokens=262144`, returned the fresh marker, and did not fall back.

These checks close the 262K Hermes/Pi/OpenClaw acceptance gate. They do not
claim multimodal acceptance.

## Rejected vLLM translation

The exact Inferact revision `103a7608316173ca6edd49929544244de7ffda70` was
cached and tested through three managed vLLM recipes. Default NCCL failed before
weight load; the WSL2 NCCL controls exposed the V2 runner's UVA failure; and the
V1 workaround loaded about 85.76 GiB per card before TorchInductor autotuning
requested another 47.69 GiB and OOMed before KV allocation. The three exact
recipes are empirically disqualified. This is not a universal claim that the
checkpoint or 262K is impossible. See the retained
[attempt artifacts](2026-08-26-qwen38-flash-next-inferact-vllm-262k-evidence/attempt-01-default-nccl.md).

## Evidence and boundaries

The sanitized machine-readable summary is
[`summary.json`](2026-08-26-qwen38-flash-next-qsa-fast-mtp3-evidence/summary.json).
The source model is pinned in the
[RadixArk revision tree](https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4/tree/7b719225242aacd3dbd3f9407468c2ee9a9d2594),
the SM120 QSA change is pinned in
[SGLang PR #36556](https://github.com/sgl-project/sglang/pull/36556), and the
MTP preset comes from the
[SGLang cookbook PR #36496](https://github.com/sgl-project/sglang/pull/36496).
All external recipes remained priors until reproduced locally.

The same-day [single-PRO community recipe](https://www.reddit.com/r/LocalLLM/comments/1vz20ap/qwen38flashnextnvfp4_on_single_rtx_pro_6000_120ts/)
reports a vLLM TP=1/PLE-CPU-offload lane near 120 tok/s at 256K, with a large
host PLE working set. It remains an efficiency-oriented recipe lead, not a
local result and not a faster result than the qualified TP=2 lane. It was not
staged because this campaign already met the requested speed/quality/context
gates and native-offload absent-container cleanup remains an open product
prerequisite. A dual-DGX-Spark recipe was also hardware-mismatched and did not
alter the local decision.

The current contract is text-only, thinking-disabled, 262,144 tokens,
concurrency one, and a single exclusive TP=2 owner. A future engine, patch,
context, KV dtype, concurrency, offload, speculation preset, or multimodal route
requires its own qualification.
