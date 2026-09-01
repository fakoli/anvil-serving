# Agents-A1

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical human-approved Primary profile with retained
      text, multimodal, router, and promotion-era evidence; it is not presented
      as the current Primary or as a claim about live routing.
    - **Selected or best-qualified configuration:** official FP8 with thinking
      disabled, FP8 KV, 262,144-token context, concurrency one, and direct
      text/image/video support.
    - **Measured hardware:** one NVIDIA RTX PRO 6000 Blackwell Max-Q on
      Fakoli Dark.
    - **Evidence:** 240K retrieval, tools 20/20, 28/30 strict multimodal cases,
      and 155.8 tok/s decode at 231,426 actual prompt tokens.
    - **Decision:** retain the official FP8 profile as reproducible
      promotion-era evidence; BF16 remains the correctness control and
      ProtoLabs NVFP4 remains a text-only `no-promotion` alternative.
    - **Important limitation:** thinking must remain disabled for the qualified
      contract, and the two retained video failures prevent a perfect strict
      multimodal result.
    - **Review dates:** retained evidence through 2026-07-29; dossier-format
      review 2026-08-31.

### Review narrative

#### 2026-07-27–28 — multimodal and quantization qualification

BF16 and official FP8 both completed the retained strict multimodal corpus at
28/30. ProtoLabs NVFP4 qualified as a compact text-only option, while its
publisher-documented vision-tower crash kept image and video outside that
profile. The generated FP8 MoE tune loaded successfully but regressed the
three-run 8K c16 throughput mean by 1.399%, so the default kernel selection
remained the qualified recipe.

#### 2026-07-29 — 262K head-to-head and promotion-era decision

The official FP8 profile passed a 262,144-token operational context at c1,
including 240K retrieval and tools 20/20, then became the human-approved
Primary through the managed transaction. That is a dated product decision;
the profile is now retained as historical evidence rather than described as a
current live assignment.

## Immutable identity

- BF16: `InternScience/Agents-A1` revision
  `addff08f1653ee72765c5cf458fe84556bb34f8e`.
- Official FP8: `InternScience/Agents-A1-FP8` revision
  `4d7d59380f327b76e73bc71f40e0c589ad0ca1d5`.
- ProtoLabs NVFP4: `protoLabsAI/Agents-A1-NVFP4` revision
  `ff24ba5c35b99af25d7bf03c7997be5a0d2a5520`.
- Qualified vLLM revision:
  `f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1`; promotion-era image digest
  `sha256:212a1bd7b4267c604408d17dc0048ef152101bc67fbe6ba8567899fc1f227bcd`.

## Tested hardware and topology

One RTX PRO 6000 Blackwell Max-Q on Fakoli Dark. Qualification used isolated
candidate serves; the exact official FP8 profile was promoted through the
managed Primary transaction on 2026-07-29. That historical promotion does not
describe current live serve or route state.

## Engine, quantization, KV, context, and concurrency recipe

### Official FP8 262K profile

The qualified profile uses pinned vLLM nightly
`f25953cc59f9b4ba9b04b16228d2b86dcfbcbdb1`, official FP8 weights, FP8 KV,
thinking disabled, 262,144 configured tokens, and concurrency one. The exact
managed recipe is retained in
[`configs/agents-a1-qwen-262k-head-to-head-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/agents-a1-qwen-262k-head-to-head-recipes.toml).

### BF16 and earlier concurrency controls

The earlier 131,072-token BF16 multimodal profile reached c16. The official
FP8 profile reached c32 at that earlier window. Their text and multimodal
recipes remain in
[`configs/agents-a1-qualification-text-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/agents-a1-qualification-text-recipes.toml)
and
[`configs/agents-a1-qualification-multimodal-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/agents-a1-qualification-multimodal-recipes.toml).

### ProtoLabs NVFP4 and rejected tune

NVFP4 uses text-only Marlin with the FlashInfer sampler disabled and no MTP;
its compact recipe is
[`configs/agents-a1-qualification-nvfp4-compact-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/agents-a1-qualification-nvfp4-compact-recipe.toml).
The hardware-specific official FP8 MoE tune was generated and loaded but is
rejected; the default kernel selection remains the qualified recipe.

## Evidence by measurement class

### Functional and quality

`functional`, `capacity`, and `quality` evidence covers BF16 and FP8 text,
image, OCR, direct video, tools, streaming, Responses, session, unified-diff,
timeout, and 128K c1/c2/c4 gates. Both scored the same 28/30 multimodal corpus
result: 12/12 image, 4/4 mixed, and 12/14 video. NVFP4 passed the repeated text
gate, 128K c4, and a compact-allocation follow-up.

### Capacity and performance

At 262K, official FP8 passed 240K retrieval and 20/20 tools, retained 51.93
GiB KV, and measured 32.97 s TTFT plus 155.8 tok/s decode at 231,426 prompt
tokens. Client-observed effective prefill includes queueing and scheduling; a
kernel-only prefill rate was **not measured**.

### Router isolation

The isolated FP8 router passed same-dialect video, media admission, tools,
SSE, malformed media, and fail-closed unsupported-dialect probes after the two
recorded router fixes.

## Decision and promotion state

### Historical official FP8 decision

Official FP8 was the human-gated `current` Primary in the dated 2026-07-29
campaign after winning the bounded Qwen comparison and passing the complete
three-repetition protocol-v3 suite at the 262K serving profile. It is retained
as historical promotion-era evidence and is not a current live-state claim.

### Retained controls

BF16 is the correctness control. NVFP4 is Pareto-preferred for compact
text-only deployment, not image/video, and remains `no-promotion`. The
generated FP8 MoE tune is not adopted: its three-run 8K c16 throughput mean
regressed 1.399% and missed the 5% gate.

## Failures and gotchas

### Thinking and multimodal assertion boundary

Thinking must remain disabled for the qualified contract. The two video
failures localized the exact event interval but omitted one required assertion
word; identical BF16 and FP8 output rules out a measured FP8 regression.
NVFP4's publisher documents a vision-tower crash.

### Context and timing interpretation

The earlier 131K profiles reject 240K; official FP8 accepts it only in the
262K c1 profile. Client-observed effective prefill includes queueing and
scheduling and must not be read as a kernel-only prefill rate.

## Dated run history

- [2026-07-29 Primary promotion](../../findings/2026-07-29-agents-a1-primary-promotion.md)
- [2026-07-29 Qwen 262K head-to-head](../../findings/2026-07-29-agents-a1-qwen-262k-head-to-head.md)
- [2026-07-28 multimodal and quantization qualification](../../findings/2026-07-28-agents-a1-multimodal-qualification.md)
- [2026-07-27 release-readiness qualification](../../findings/2026-07-27-anvil-serving-release-readiness-sweep.md#agents-a1-qualification)
