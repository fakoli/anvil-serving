# Agents-A1

## Current status and review date

Text-qualified `challenger` with thinking disabled; multimodal hard gate
28/30; `no-promotion`.
Review date: 2026-07-28.

## Immutable identity

- BF16: `InternScience/Agents-A1` revision
  `addff08f1653ee72765c5cf458fe84556bb34f8e`.
- Official FP8: `InternScience/Agents-A1-FP8` revision
  `4d7d59380f327b76e73bc71f40e0c589ad0ca1d5`.
- ProtoLabs NVFP4: `protoLabsAI/Agents-A1-NVFP4` revision
  `ff24ba5c35b99af25d7bf03c7997be5a0d2a5520`.

## Tested hardware and topology

One RTX PRO 6000 on Fakoli Dark. Candidate serves and the qualification router
were isolated from the live Primary route.

## Engine, quantization, KV, context, and concurrency recipe

Pinned vLLM nightly `f25953cc...`, FP8 KV, 131,072 operational context,
thinking disabled. BF16 multimodal reached c16; official FP8 reached c32;
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

## Decision and promotion state

BF16 is the correctness control. Official FP8 is the principal
production-shaped candidate but does not clear the predeclared 100%
multimodal assertion gate. NVFP4 is Pareto-preferred for compact text-only
deployment, not image/video. All remain `no-promotion`.
The generated FP8 MoE tune is not adopted: its three-run 8K c16 throughput mean
regressed 1.399% and missed the 5% gate.

## Failures and gotchas

Thinking must remain disabled for the production contract. The two video
failures localized the exact event interval but omitted one required assertion
word; identical BF16 and FP8 output rules out a measured FP8 regression.
NVFP4's publisher documents a vision-tower crash. All variants reject 240K
against the served 131,072-token limit.
Client-observed effective prefill includes queueing and scheduling and must not
be read as a kernel-only prefill rate.

## Dated run history

- [2026-07-28 multimodal and quantization qualification](../../findings/2026-07-28-agents-a1-multimodal-qualification.md)
- [2026-07-27 release-readiness qualification](../../findings/2026-07-27-anvil-serving-release-readiness-sweep.md#agents-a1-qualification)
