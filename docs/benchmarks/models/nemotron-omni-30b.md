# Nemotron Nano and Omni 30B

## Current status and review date

Nemotron Nano Omni 30B is the `current` exclusive RTX 5090 Omni topology.
Earlier Nano/Omni bakeoff rows remain historical. Review date: 2026-07-28.

## Immutable identity

Current: `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` revision
`dc5f0b0bfddf8b6e0f5891475be9af05b80126fe`, pinned vLLM image digest
`sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.

## Tested hardware and topology

RTX 5090 on Fakoli Dark. The PRO 6000 Primary is separate and may be protected
while this stack runs.

## Engine, quantization, KV, context, and concurrency recipe

vLLM `0.23.1rc1.dev531+ga65f93fb2`, NVFP4, 65,536 context, two sequences,
thinking disabled for text gates, router aliases for auxiliary text,
vision.general, and vision.ocr.

## Evidence by measurement class

`functional`, `capacity`: smoke, JSON, 4K retrieval, three tools, bounded image
understanding, OCR, and residency. Older Nano rows add historical quality and
runtime-compatibility observations.

## Decision and promotion state

`current` topology for auxiliary text/image/OCR. This is not Primary promotion.

## Failures and gotchas

Audio input was not qualified. The architecture required the pinned nightly;
changing image or revision requires all gates again.

## Dated run history

- [2026-07-27 Omni qualification](../../findings/2026-07-27-omni-stack-qualification.md)
- [2026-07-10 Nano/Omni bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
