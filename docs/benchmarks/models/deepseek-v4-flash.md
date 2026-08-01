# DeepSeek V4 Flash 0731

## Current status and review date

Priority intelligence `challenger`; `no-promotion`. Exact publisher weights
are qualified for the bounded low-reasoning TP=2 contract below. `high`, `max`,
DSpark, 0731-specific NVFP4, context above 32K, and concurrency above one are
not locally qualified. The 2026-07-10 single-card attempt remains a retained
historical rejection. Review date: 2026-08-01.

## Immutable identity

Current result: `deepseek-ai/DeepSeek-V4-Flash-0731` revision
`7872f01b1d1fe23eabc4c98b48bffcef5a386062`. Release weights first appeared at
`9e165c30e2704aec5d9d593cce3eebd58bbef1cb`; the current revision adds model-card
documentation without replacing those weights.

DeepSeek identifies 0731 as a re-post-trained official release that supersedes
Flash Preview while retaining the V4 Flash architecture. Treat it as a
distinct behavioral generation. The target model is described as 284B total
and 13B active; checkpoint inventories that include the bundled DSpark draft
module report approximately 304B. It is MIT licensed with a declared
1,048,576-token maximum context.

The 2026-07-10 `nvidia/DeepSeek-V4-Flash-NVFP4` attempt used NVIDIA's Preview
conversion, did not retain a reusable checkpoint revision, and remains
`historical-invalid`. It is not a measurement of 0731.

## Tested hardware and topology

Two RTX PRO 6000 Blackwell Max-Q cards on Fakoli Dark in exclusive TP=2 over
PCIe without NVLink. Every other inference workload was offline. The 192 GB
figure is aggregate VRAM, not unified memory.

## Engine, quantization, KV, context, and concurrency recipe

The working lane uses SGLang 0.5.16 from pinned derived image
`sha256:0aa5324c4f38bc66f4b55e1e12efab821ef614b1a8629259b2810ff72a6570e6`,
publisher hybrid FP4-expert/FP8 weights, FP8 E4M3 KV, 32,768 context, one
admitted request, `reasoning_effort=low`, and no speculative decoding. The
derived image disables only the WSL2-incompatible symmetric-memory logits
gather and retains SGLang's NCCL fallback.

The checkpoint has no ordinary Jinja template. The qualified serve uses the
DeepSeek-V4 tokenizer mode plus its reasoning and DSML tool-call parsers.
Reasoning state must be preserved across tool-result continuation.

## Evidence by measurement class

The current revision has `functional`, `capacity`, and `quality` local
evidence. Smoke, JSON, 30K retrieval, tools 20/20, streaming tools, tool-result
continuation, and Responses passed. Repeated quality passed intelligence 6/6,
session 3/3, and tools 3/3 with 4,096 reasoning-headroom tokens. The final 32K
capacity lane completed 11/12: TTFO p50 2.705 seconds, first-visible TTFT p50
29.106 seconds, effective prefill 7,818 tok/s, combined reasoning/visible
decode 11.5 tok/s, and aggregate output 7.60 tok/s.

Current `external-prior` evidence strengthens the research priority without
expanding that local contract:

- DeepSeek reports large agentic gains over Preview, including 82.7 versus
  61.8 on Terminal Bench 2.1 and 54.4 versus 7.3 on DeepSWE. The code-agent
  runs used an unreleased harness at `max`; two DSBench sets are internal.
- Artificial Analysis independently scores max-effort 0731 at 50, number 3 of
  101 comparable models, while reporting 210 million evaluation output tokens.
- vLLM and SGLang publish 0731 parser and DSpark recipes, but neither source
  provides the exact two-RTX-PRO non-speculative result recorded here.
- Community 0731 NVFP4 conversions now exist. MJPansa has the strongest
  conversion receipt; Auroter has the strongest four-RTX-PRO performance
  prior. Neither is local TP=2 qualification.

See the [deep research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
and its [source registry](../../findings/2026-08-01-deepseek-v4-flash-0731-research-evidence/source-registry.json)
for the benchmark deltas, architecture, runtime matrix, conversion identities,
GGUF size ladder, DSpark caveats, and source classifications.

## Decision and promotion state

`challenger`, `no-promotion`. Make 0731 the priority dual-card intelligence
research target, but preserve the current production Primary and rollback
chain. The next gates are publisher `low`/`high`/`max`, progressive context,
matched DSpark off/on, then pinned 0731 NVFP4 W4A16/W4A4 qualification.

## Failures and gotchas

One of twelve final capacity requests consumed 2,048 completion tokens in the
reasoning channel without a visible answer. Artificial Analysis's 210-million-
output-token max-effort run points to the same operational risk: reasoning
budget policy can dominate latency, cost, and visible-answer reliability.

The checkpoint does not provide FP8 KV scaling factors, so the runtime's
unscaled FP8 KV path remains an accuracy caveat. The locally served 32K profile
cannot represent DeepSeek's recommendation to allow up to 384K output tokens
for `high` and `max`.

NVIDIA's official NVFP4 repository is Preview, not 0731. Community 0731 NVFP4
artifacts must be pinned and independently checked. In the strongest current
SM120 report, the main experts are NVFP4 but the bundled draft experts remain
MXFP4; without prefix-aware draft routing, DSpark can generate with zero draft
acceptance. Successful startup or text generation alone is not a sufficient
NVFP4+DSpark gate.

The historical NGC vLLM architecture rejection and aborted NVFP4 load are not
measurements of the current 0731 checkpoint.

## Dated run history

- [2026-08-01 deep research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
- [2026-08-01 dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
- [Retained failure detail](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/failures.md)
