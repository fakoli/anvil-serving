# DeepSeek V4 Flash 0731

## Current status and review date

Priority intelligence `challenger`; `no-promotion`. The preferred experiment
profile is the pinned r16 B12X TP=2 recipe with DSpark K5, 131,072 served
tokens, and low/high/max reasoning support. It passed 128K correctness and a
27/27 coding-agent slice, and DSpark materially beat the same-image no-spec
control. Neither profile passed the standing 3 GiB reported-free VRAM policy,
so no production alias changed. Review date: 2026-08-01.

The earlier SGLang 32K low-reasoning lane remains valid point-in-time evidence,
not the current performance recipe. Community 0731 NVFP4 and GGUF conversions,
256K local context, and concurrency above one remain unqualified.

## Immutable identity

The r16 result pins `deepseek-ai/DeepSeek-V4-Flash-0731` revision
`9e165c30e2704aec5d9d593cce3eebd58bbef1cb`, the release revision containing
the tested weights. The upstream repository has since advanced to
`7872f01b1d1fe23eabc4c98b48bffcef5a386062` for model-card documentation; that
later commit was not substituted into the measured recipe.

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

The working profile uses image digest
`sha256:48518e91cf87dd0c0483c76ff86e81dfc0f46de7e364b46f7a82c481ce08188f`,
vLLM base commit `30038602b71395f481ef4a6edfe4fcf8551d9c15`, B12X W4A8
NVFP4 MoE and FP8 dense kernels, FP8 MLA KV, InstantTensor, TP=2, 131,072
context, eight admitted sequences, and DSpark fixed-depth five-token drafting.
The measured workload is c1.

The native-Linux source recipe required an SM120-to-WSL2 translation:
direct NCCL P2P and NCCL cuMem device/host allocation are disabled, shared
memory remains enabled, and PyTorch expandable segments are disabled. Model,
JIT, and temporary build data use named Docker volumes.

- [DSpark K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-128k-recipe.toml)
- [Same-image no-spec control](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-nospec-128k-recipe.toml)
- [Complete qualification](../../findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md)

## Evidence by measurement class

The r16 revision has `functional`, `capacity`, and bounded `quality` evidence:

- Low reasoning passed smoke, JSON, three typed tools, streaming tools,
  tool-result continuation, and Responses. High and max passed smoke, JSON,
  tools, and tool-result continuation.
- The context ladder passed 32K, 64K, and a clamped 126,464-token request.
  The warmed 125,785-prompt-token row measured 19.44-second TTFO,
  23.81-second first-visible TTFT, 6,469 effective prefill tok/s, and
  128.9 tok/s combined reasoning/visible decode.
- Nine coding/intelligence/session/tool items ran three times each and passed
  27/27 attempts with 4,096 reasoning-headroom tokens.
- In a same-image c1 A/B, DSpark increased median per-request decode from
  64.9 to 130.7 tok/s, aggregate output from 59.6 to 101.7 tok/s, and reduced
  median E2E from 3.88 to 1.60 seconds.
- Cumulative DSpark counters recorded 4,865 accepted of 8,830 draft tokens,
  or 55.1% acceptance and 2.75 accepted tokens per draft.

The prior SGLang lane used image digest
`sha256:0aa5324c4f38bc66f4b55e1e12efab821ef614b1a8629259b2810ff72a6570e6`,
publisher hybrid FP4-expert/FP8 weights, FP8 E4M3 KV, 32,768 context, one
admitted request, low reasoning, and no speculative decoding. It passed
functional and repeated quality gates. Its final 32K capacity lane completed
11/12 at 2.705-second TTFO, 29.106-second first-visible TTFT, 7,818 effective
prefill tok/s, and 11.5 tok/s combined reasoning/visible decode.

Current `external-prior` evidence strengthens the research priority without
expanding the local contract:

- DeepSeek reports large agentic gains over Preview, including 82.7 versus
  61.8 on Terminal Bench 2.1 and 54.4 versus 7.3 on DeepSWE. The code-agent
  runs used an unreleased harness at `max`; two DSBench sets are internal.
- Artificial Analysis independently scores max-effort 0731 at 50, number 3 of
  101 comparable models, while reporting 210 million evaluation output tokens.
- Community 0731 NVFP4 conversions now exist. MJPansa has the strongest
  conversion receipt; Auroter has the strongest four-RTX-PRO performance
  prior. Neither is local TP=2 qualification.

See the [deep research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
and its [source registry](../../findings/2026-08-01-deepseek-v4-flash-0731-research-evidence/source-registry.json)
for the benchmark deltas, architecture, runtime matrix, conversion identities,
GGUF size ladder, DSpark caveats, and source classifications.

## Decision and promotion state

`challenger`, `no-promotion`. The r16 DSpark profile is the preferred local
performance experiment and is suitable for further coding-agent and deployment
work. Preserve the current production Primary and rollback chain. The next
material gates are a policy-compliant 128K memory recipe, broader coding/tool
recovery evaluation, concurrency qualification, 256K feasibility, and pinned
0731 NVFP4 W4A16/W4A4 comparisons.

## Failures and gotchas

The first r16 start exposed an offline nested-speculative-model localization
gap. The second exposed native-Linux NCCL defaults incompatible with WSL2. The
third loaded both ranks but missed the 131K KV admission gate by 0.15 GiB at
`max_num_seqs=16`; the qualified c1 recipe reduces admission to eight and the
CUDA-graph cap to 48 without raising its memory-utilization ceiling.

Both r16 profiles fail the 3 GiB reported-free reserve. Per-context sampling
found only 1,179-1,203 MiB free on `dark-compute-a` and 2,031 MiB on
`dark-compute-b`. WSL/WDDM global allocation differs from native Linux, but a
successful request does not authorize silently weakening the gate.

Reasoning-budget exhaustion is still a real operational risk. One earlier
SGLang capacity request and one additional r16 no-spec control run completed
without visible content. High/max support proves protocol compatibility, not
that arbitrarily large reasoning budgets are efficient.

The pinned runtime source repository has no root license file at the tested
revision. The model weights are MIT, but local qualification does not establish
permission to redistribute the image or derived runtime code.

The historical NGC vLLM architecture rejection and aborted NVFP4 load are not
measurements of the current 0731 checkpoint.

## Dated run history

- [2026-08-01 r16 DSpark qualification](../../findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md)
- [2026-08-01 deep research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
- [2026-08-01 dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
- [Retained failure detail](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/failures.md)
