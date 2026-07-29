# Gemma 4 RTX PRO 6000 variants

## Current status and review date

Historical strict-quality control; no variant is current. The official 12B QAT
W4A16 is `no-promotion`; 26B timeout behavior and 31B latency are `rejected`.
Review date: 2026-07-28.

## Immutable identity

Retained PRO configurations:

- official 12B QAT W4A16 `5d8bb23cdbff01e89d2a1a47f3b3d29b877bca76`,
  tokenizer `12ace6d648d72bd41519e140f1185f34d38c7e3d`;
- official 26B-A4B BF16 `01e5b3ee840d3a9e0b0b493c593e85398a30ef75`;
- official 31B QAT W4A16 `a766e9afa44931dfa9ff5de90af9494ca193e74c`,
  tokenizer `b9ea41a2887d8607f594846523f94c6cc75ac8a4`;
- Unsloth 12B NVFP4 `b1f649734b34aa5575b03d186abd1b9be3d0d5c4`;
- Unsloth 26B-A4B NVFP4 `20df0542b1a86ce19f495ac2eca2c7c12bce82f9`;
- Unsloth 31B NVFP4 `373c00b5ecb0a8ee43942b5ca08b93805de8eee4`.

## Tested hardware and topology

RTX PRO 6000 on Fakoli Dark; matched 5090 rows are kept in the same findings
but are not PRO measurements.

## Engine, quantization, KV, context, and concurrency recipe

vLLM 0.25.1, compressed-tensors W4A16 or tested mixed NVFP4/FP8 variants, FP8
KV, Gemma reasoning/tool parsers. The 12B control served 262,144 with five
sequences and passed a 240K needle.

## Evidence by measurement class

`compatibility-only`, `capacity`, and `quality` across official and Unsloth
12B/26B/31B configurations.

## Decision and promotion state

Historical strict-quality control only; `no-promotion` or `rejected`.

## Failures and gotchas

Pin model and tokenizer revisions separately. The 26B failed timeout triage;
the 31B passed quality but was rejected for latency. Do not infer current status
from older promotion-era findings.

## Dated run history

- [2026-07-17 31B optimization](../../findings/2026-07-17-gemma4-31b-optimization.md)
- [2026-07-16 vLLM 0.25.1 sweep](../../findings/2026-07-16-gemma4-vllm0251-wsl2-c128.md)
- [2026-07-16 Unsloth follow-up](../../findings/2026-07-16-gemma4-unsloth-nvfp4-follow-up.md)
- [2026-07-16 template bakeoff](../../findings/2026-07-16-gemma4-chat-template-bakeoff.md)
