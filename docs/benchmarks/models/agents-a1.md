# Agents-A1

## Current status and review date

`current` Primary with thinking disabled. The retained strict multimodal corpus
is 28/30 with a documented assertion caveat.
Review date: 2026-07-29.

## Immutable identity

- BF16: `InternScience/Agents-A1` revision
  `addff08f1653ee72765c5cf458fe84556bb34f8e`.
- Official FP8: `InternScience/Agents-A1-FP8` revision
  `4d7d59380f327b76e73bc71f40e0c589ad0ca1d5`.
- ProtoLabs NVFP4: `protoLabsAI/Agents-A1-NVFP4` revision
  `ff24ba5c35b99af25d7bf03c7997be5a0d2a5520`.

## Tested hardware and topology

One RTX PRO 6000 on Fakoli Dark. Qualification used isolated candidate serves;
the exact official FP8 profile was promoted through the managed Primary
transaction on 2026-07-29.

## Engine, quantization, KV, context, and concurrency recipe

Pinned vLLM nightly `f25953cc...`, FP8 KV, thinking disabled. The official FP8
profile now passes a 262,144 operational context at c1; the earlier 131,072
profile reached c32. BF16 multimodal reached c16; official FP8 reached c32;
NVFP4 uses text-only Marlin with the FlashInfer sampler disabled and no MTP.
The hardware-specific official FP8 MoE tune was generated and loaded but is
rejected; the default kernel selection remains the qualified recipe.

## Evidence by measurement class

`functional`, `capacity`, `quality`: BF16 and FP8 passed text, image, OCR,
direct video, tools, streaming, Responses, session, unified-diff, timeout, and
128K c1/c2/c4 gates. Both scored the same 28/30 multimodal corpus result:
12/12 image, 4/4 mixed, and 12/14 video. NVFP4 passed the repeated text gate,
128K c4, and a compact-allocation follow-up. The isolated FP8 router passed
same-dialect video, media admission, tools, SSE, malformed media, and
fail-closed unsupported-dialect probes after the two recorded router fixes.
At 262K, official FP8 passed 240K retrieval and 20/20 tools, retained 51.93
GiB KV, and measured 32.97 s TTFT plus 155.8 tok/s decode at 231,426 prompt
tokens.

## Decision and promotion state

BF16 is the correctness control. Official FP8 is the human-gated `current`
Primary after winning the bounded Qwen comparison and passing the complete
three-repetition protocol-v3 suite at the 262K serving profile. NVFP4 is
Pareto-preferred for compact text-only deployment, not image/video, and
remains `no-promotion`.
The generated FP8 MoE tune is not adopted: its three-run 8K c16 throughput mean
regressed 1.399% and missed the 5% gate.

## Failures and gotchas

Thinking must remain disabled for the production contract. The two video
failures localized the exact event interval but omitted one required assertion
word; identical BF16 and FP8 output rules out a measured FP8 regression.
NVFP4's publisher documents a vision-tower crash. The earlier 131K profiles
reject 240K; official FP8 accepts it only in the new 262K c1 profile.
Client-observed effective prefill includes queueing and scheduling and must not
be read as a kernel-only prefill rate.

## Dated run history

- [2026-07-29 Primary promotion](../../findings/2026-07-29-agents-a1-primary-promotion.md)
- [2026-07-29 Qwen 262K head-to-head](../../findings/2026-07-29-agents-a1-qwen-262k-head-to-head.md)
- [2026-07-28 multimodal and quantization qualification](../../findings/2026-07-28-agents-a1-multimodal-qualification.md)
- [2026-07-27 release-readiness qualification](../../findings/2026-07-27-anvil-serving-release-readiness-sweep.md#agents-a1-qualification)
