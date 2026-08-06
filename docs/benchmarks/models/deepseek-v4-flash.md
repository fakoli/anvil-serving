# DeepSeek V4 Flash 0731

## Current status and review date

`current` for the human-approved `llm.primary` alias. The pinned r16 B12X TP=2
recipe uses DSpark K5, 650,000 served tokens, 4,096-token batching, 16 admitted
sequences, and a router-enforced 32,768-token output cap. It passed ~640K
retrieval, the complete low-reasoning Pi protocol gate, a matched 32K c1 run at
141.6 tok/s median decode, and current high-reasoning smokes from Pi on Fakoli
Dark, Pi on Fakoli Mini, and OpenClaw on Fakoli Mini. The former 1M/maxseq16
candidate remains capacity evidence but is no longer client-facing: two real
agent request shapes fatally exceeded the locked B12X workspace.

A derived WSL2 image now also qualifies native CPU KV offload at a 262,144
served-token ceiling. It passed a cold ladder through 249,573 prompt tokens and
the managed shared-memory lifecycle regression. This extends the capacity
contract but does not replace the 131K profile as the preferred performance
recipe. Review date: 2026-08-02.

The earlier SGLang 32K low-reasoning lane remains valid point-in-time evidence,
not the current performance recipe. Community 0731 NVFP4 and GGUF conversions
remain unqualified. The local maxseq16 lane has bounded c16 and three-tool-burst
evidence, but not broad repeated multi-agent quality.

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
- [256K 8 GiB native-offload recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload8-wsl2-mmap-unpinned-recipe.toml)
- [256K 16 GiB CPU-reload recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-256k-offload16-wsl2-mmap-unpinned-recipe.toml)
- [650K maxseq16 Pi recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-650k-recipe.toml)
- [1M maxseq4 deep-session recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq4-1m-recipe.toml)
- [1M maxseq16 deep-session recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/deepseek-v4-flash-0731-r16-b12x-dspark5-maxseq16-1m-recipe.toml)
- [Complete qualification](../../findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md)
- [Native-offload and 256K qualification](../../findings/2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md)
- [650K/1M Pi qualification](../../findings/2026-08-02-deepseek-v4-flash-0731-650k-1m-pi-qualification.md)
- [650K Primary promotion](../../findings/2026-08-02-deepseek-v4-flash-0731-primary-promotion.md)

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

The derived native-offload lane adds `functional` and `capacity` evidence:

- The 128K configuration stored 7.15 GB GPU-to-CPU and replayed 1.13 GB
  CPU-to-GPU; a 52,495-token replay reached 99.5% cache hits and 0.514-second
  time to first output.
- The 256K configuration passed cold 92,754-, 193,064-, and
  249,573-prompt-token requests. The largest measured 43.75-second TTFO,
  45.58-second first-visible TTFT, 5,705 effective prefill tok/s, and
  135.2 tok/s decode.
- The 8 GiB 256K session stored 11.06 GB GPU-to-CPU, but its follow-ups stayed
  in the larger GPU prefix tier. With a 16 GiB CPU tier, six distinct 150K
  planned-context requests stored 13.63 GB; an exact replay then produced
  113,408 external hits and loaded 1.002 GB CPU-to-GPU in 0.344 seconds while
  GPU-prefix hits remained unchanged. Replay TTFO was 0.825 seconds and visible
  TTFT was 1.974 seconds.
- The ownership-aware managed lifecycle blocked cleanup while both TP workers
  mapped the 8 and 16 GiB files, then reclaimed each after container removal
  and restored split mode.

The GPU-only Pi-context lane adds `functional` and `capacity` evidence:

- The 650K/maxseq16 profile recovered a needle at approximately 640K in 120.6
  seconds and passed smoke, JSON, three typed tools, streaming tools,
  tool-result continuation, and Responses.
- Its matched 32K c1 run completed 3/3 in 16.6 seconds at 5.59-second median
  E2E, 8,793 effective prefill tok/s, and 141.6 tok/s median decode.
- The first 1M profile admitted one sequence and recovered a 985K needle, but
  a three-tool burst fatally exceeded its locked B12X workspace. It is rejected
  for Pi agentic use.
- Raising admission to four retained 1M capacity, passed the same Pi gate and
  985K retrieval, and delivered 119.9 tok/s median decode at 32K c1. Near-limit
  retrieval still took 235.7 seconds.
- Raising admission again to 16 retained 1,715,610 KV tokens, passed the same
  Pi gate and 985K retrieval plus a post-probe coding request, and improved
  median 32K decode to 129.0 tok/s. Later real requests required 703.64 and
  687.83 MiB workspaces when only 514.25 MiB was available. The latter had a
  19,118-token Pi prompt and only 5,120 requested output tokens, proving that
  output clamping alone does not make the 1M profile safe.

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

`current`, human-approved. The maxseq16 r16 DSpark profile is the exclusive
TP=2 Primary for one Pi/OpenClaw coding user, with high reasoning as the client
default and `llm.rollback` preserved explicitly. The router's optional per-tier
output cap is 32,768 and warns instead of rejecting an oversized caller budget.

2026-08-06 operator-directed retune: the pinned recipe now serves
`MAX_MODEL_LEN=262144` with `MAX_NUM_BATCHED_TOKENS=8192` (previously
650,000/4,096; serve and model names retain the historical `650k` suffix). The
engine sized GPU KV cache at 272,040 tokens (1.04x concurrency at a full
262,144-token request) and a same-day functional preflight (smoke, structured
JSON, tool batch x20, tool-result continuation) passed. The ~640K retrieval,
650K-envelope performance rows, and client smokes above are dated history from
the prior envelope and do not transfer without fresh measurement.

The 1M profiles are experimental only. Remaining gates include restoring a
policy-compliant reserve, sustained multi-turn high/max testing, fixing and
requalifying the client-shaped 1M B12X workspace failure, and pinned 0731 NVFP4
W4A16/W4A4 comparisons.

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

Moving Windows display output to the AMD iGPU allowed the previously failing
maxseq16 graph envelope to start. The 650K profile reported only 797/805 MiB
free after its workload, the 1M/maxseq4 profile reported 207/209 MiB after
the 985K probe, and the 1M/maxseq16 profile reported 339/335 MiB. The experiment
intentionally waived the separate 3 GiB
acceptance gate while retaining `GPU_MEMORY_UTILIZATION=0.975`; this is a
capacity result, not a new reserve policy. The 1M/maxseq1 profile also remains
an explicit retained failure because its locked 514.25 MB B12X workspace could
not satisfy a later 873.62 MB compressed-MLA allocation.

Reasoning-budget exhaustion is still a real operational risk. One earlier
SGLang capacity request and one additional r16 no-spec control run completed
without visible content. High/max support proves protocol compatibility, not
that arbitrarily large reasoning budgets are efficient.

The pinned runtime source repository has no root license file at the tested
revision. The model weights are MIT, but local qualification does not establish
permission to redistribute the image or derived runtime code.

The historical NGC vLLM architecture rejection and aborted NVFP4 load are not
measurements of the current 0731 checkpoint.

Native offload originally crashed on WSL2 because CUDA host registration of
the process-shared mmap conflicted with the allocator lifetime. The derived
image skips registration only for that mmap while preserving the global V2
UVA path. A separate 256K failure was stale tmpfs exhaustion: four orphan
offload files filled `/dev/shm`, producing `mmap.madvise: Bad address`. Anvil
Serving now performs two ownership checks and exact-path cleanup before load
and after managed teardown. Page-cache reclaim alone is not sufficient.

## Dated run history

- [2026-08-02 650K/1M Pi qualification](../../findings/2026-08-02-deepseek-v4-flash-0731-650k-1m-pi-qualification.md)
- [2026-08-02 650K Primary promotion](../../findings/2026-08-02-deepseek-v4-flash-0731-primary-promotion.md)
- [2026-08-02 native KV offload and 256K qualification](../../findings/2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md)
- [2026-08-01 r16 DSpark qualification](../../findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md)
- [2026-08-01 deep research update](../../findings/2026-08-01-deepseek-v4-flash-0731-research-update.md)
- [2026-08-01 dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
- [Retained failure detail](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/failures.md)
