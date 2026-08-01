# DeepSeek V4 Flash

## Current status and review date

Qualified TP=2 `challenger`; `no-promotion`. The 2026-07-10 single-card attempt
remains a retained historical rejection. Review date: 2026-08-01.

## Immutable identity

Current result: `deepseek-ai/DeepSeek-V4-Flash-0731` revision
`7872f01b1d1fe23eabc4c98b48bffcef5a386062`. The 2026-07-10
`nvidia/DeepSeek-V4-Flash-NVFP4` attempt did not retain a reusable checkpoint
revision and remains `historical-invalid`.

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q cards on Fakoli Dark in exclusive TP=2 over
PCIe without NVLink. Every other inference workload was offline.

## Engine, quantization, KV, context, and concurrency recipe

The working lane uses SGLang 0.5.16 from a pinned derived image, publisher
hybrid FP4-expert/FP8 weights, FP8 E4M3 KV, 32,768 context, one admitted
request, and `reasoning_effort=low` for the comparable reasoning gate. The
derived image disables only the WSL2-incompatible symmetric-memory logits
gather and retains SGLang's NCCL fallback.

## Evidence by measurement class

The current revision has `functional`, `capacity`, and `quality` evidence.
Smoke, JSON, 30K retrieval, tools 20/20, streaming tools, tool-result
continuation, and Responses passed. Repeated quality passed intelligence 6/6,
session 3/3, and tools 3/3 with 4,096 reasoning-headroom tokens. The final 32K
capacity lane completed 11/12: TTFO p50 2.70 seconds, first-visible TTFT p50
29.11 seconds, effective prefill 7,818 tok/s, and combined reasoning/visible
decode 11.5 tok/s.

## Decision and promotion state

`challenger`, `no-promotion`. The campaign changed no production alias or
router profile.

## Failures and gotchas

One of twelve final capacity requests consumed 2,048 completion tokens in the
reasoning channel without a visible answer. Publish that tail failure with the
11/12 result. The checkpoint does not provide FP8 KV scaling factors, so the
runtime's unscaled FP8 KV path remains an accuracy caveat. The historical NGC
vLLM architecture rejection and aborted NVFP4 load are not measurements of the
current 0731 checkpoint.

## Dated run history

- [2026-08-01 dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
- [Retained failure detail](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/failures.md)
