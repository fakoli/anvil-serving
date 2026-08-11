# DeepSeek V4 Flash 0731 community configuration refresh

**Date:** 2026-08-10

**Campaign type:** discovery and decision support only

**Checkpoint:** `deepseek-ai/DeepSeek-V4-Flash-0731@9e165c30e2704aec5d9d593cce3eebd58bbef1cb`

**Machine-readable record:**
[candidates.json](2026-08-10-deepseek-v4-flash-0731-community-config-refresh-evidence/candidates.json)

## Outcome

The community has made material runtime and configuration progress since the recorded Anvil
Serving r16 qualification. The strongest direct dual-RTX-PRO candidate is the pinned r33 runtime,
not an immediate precision change. After a second, broader community pass, the quality-first
configuration to qualify is now explicit: the released 0731 weights, FP8 DS-MLA KV, target-only
decoding, one sequence, and a 393,216-token configured window. This is an experiment design, not
a measured recipe. It exceeds 300K while requiring substantially less KV capacity than a 650K or
1M profile.

The most credible dual-DGX-Spark recipes have separately converged on K5, TP=2, and explicit
network binding, but disagree on FP8 versus NVFP4 KV, graph mode, and operating-system headroom. Those Spark
settings are translation priors, not evidence that they will work or perform the same on two
discrete SM120 RTX PRO 6000 cards under WSL2.

Eleven configurations and 28 sources across 13 Reddit channel groupings are retained below. None
was downloaded, loaded, benchmarked, routed, or promoted during this campaign. The recorded 650K
r16 Primary profile is therefore unchanged.

## Candidate shortlist

| ID | Target | Material change | Decision action |
|---|---|---|---|
| `pro-r33-k5-131k-auto-allreduce` | Dual RTX PRO 6000 | Pinned r33 runtime, K5, FP8 KV, 131K, automatic FlashInfer PCIe IPC all-reduce | **Compatibility spike first** |
| `pro-r33-k5-131k-b12x-allreduce` | Dual RTX PRO 6000 | Same r33 lane with B12X all-reduce forced for a one-variable comparison | **Benchmark A/B** |
| `pro-r33-target-only-control` | Dual RTX PRO 6000 | Same r33 lane with speculative decoding disabled | **Required control** |
| `pro-r33-native-l1-l2-500k` | Dual RTX PRO 6000 | Repaired native host-L1/file-L2 offload path at 500K | **Conditional spike** after lifecycle gate |
| `pro-community-c24-256k-offload128` | Dual RTX PRO 6000 | Community-reported 24-way concurrency, 256K profile, 128 GiB CPU KV offload | **Watch**, recipe not reproducible yet |
| `spark-tony-1m-nvfp4-k5` | Dual DGX Spark | 1M, K5, NVFP4 KV, cold-prefill and shared-expert patches, explicit RoCE binding | **DGX reference candidate** |
| `spark-mia-1m-regular-graphs` | Dual DGX Spark | 1M, K5, NVFP4 KV, regular graphs instead of breakable graphs | **DGX benchmark A/B** |
| `spark-eugr-fp8-k5-mns8` | Dual DGX Spark | FP8 KV quality/control lane, K5, eight sequences, simpler recipe | **DGX control/watch** |
| `spark-team-200k-c16` | Dual DGX Spark | 200K and 16 sequences, retaining K5 and the agent/cold-prefill fixes | **Concurrency candidate** |
| `pro-r33-quality-393k-fp8-target-only` | Dual RTX PRO 6000 | Released weights, FP8 KV, 393,216 context, one sequence, no speculation | **Preferred quality-first >300K arm** |
| `pro-auroter-w4a16-fp8kv-256k-quality-isolation` | RTX PRO 6000 translation | Exact-0731 NVFP4 W4A16 weights, FP8 KV, target-only; published creator result used four cards | **Precision challenger**, not the final context arm |

Configuration fields, source revisions, evidence labels, gaps, and required local gates are in the
JSON record. “Candidate” means test-next or watch-next; it is not a serving recommendation.

## Quality-first precision conclusion

“Move from FP8 to NVFP4” is not one switch on this checkpoint. There are three separate choices:

1. **Released weights.** DeepSeek 0731 already mixes MXFP4 routed experts with FP8/BF16 dense,
   attention, shared-expert, embedding, and head tensors. It is not simply an all-FP8 model.
2. **Converted routed-expert weights and activation kernels.** The exact-0731
   [Auroter NVFP4 artifact](https://huggingface.co/auroter/DeepSeek-V4-Flash-0731-NVFP4/tree/17e0f9da8257371654d458ba518659aa99954c86)
   reports a bit-exact MXFP4-to-NVFP4 weight cast. Its W4A16 path measured perplexity 5.160/5.182
   versus 5.178/5.189 for the source-weight control, within the creator's run-to-run spread. The
   same artifact's native W4A4 path measured 5.297 perplexity for a reported 4-6% throughput gain.
   This supports testing W4A16 as a later quality challenger; it does not justify choosing W4A4
   when quality outranks speed.
3. **KV-cache precision.** NVIDIA's
   [ModelOpt contract](https://docs.nvidia.com/nemo/rl/nightly/design-docs/modelopt-real-quant-architecture.html)
   explicitly leaves KV precision under vLLM's independent `kv_cache_dtype` control. NVIDIA's
   [adjacent preview-checkpoint NVFP4 model](https://huggingface.co/nvidia/DeepSeek-V4-Flash-NVFP4)
   reports close baseline/NVFP4 scores at up to 384K, but its verified vLLM launch still uses FP8
   KV. That evidence supports NVFP4 weights as plausible; it says nothing about NVFP4-KV parity.

The quality-first order is therefore **released weights + FP8 KV**, then exact-0731 NVFP4 W4A16
weights + FP8 KV, and only then a matched FP8-KV versus NVFP4-KV comparison. No controlled
exact-0731 cache-precision quality A/B above 300K was found. NVFP4 KV may buy important capacity,
especially on two Sparks, but capacity is not quality evidence.

## Expanded Reddit channel pass

The second pass did not stop at r/LocalLLM and r/DeepSeek. It reviewed r/LocalLLaMA,
r/LocalLLM, r/DeepSeek, r/LocalAIServers, r/BlackwellPerformance, r/Vllm, r/unsloth,
r/LocalAIStack, r/opencode, r/ollama, r/hermesagent, r/AIProgrammingHardware, and searches of
r/nvidia/r/DGX. The machine-readable record captures the role, retained sources, and disposition
for every channel. r/AIProgrammingHardware was useful for discovery but mostly summarized linked
GitHub or video material, so it was not counted as independent corroboration.

Five cross-channel findings materially affect qualification:

- A current [vLLM reasoning-effort fix](https://github.com/vllm-project/vllm/pull/50684) documents
  missing or mislabeled 0731 `high`/`max` prefixes and message-level tool loss. The thread contains
  independent dual-Spark and four-L40S reproductions. An
  [r/DeepSeek provider sweep](https://www.reddit.com/r/DeepSeek/comments/1vdqjwr/openrouter_reasoning_effort_levels_are_broken_for/)
  found the same 6/85/98 prompt-token signatures across hosted providers. Reasoning-prefix
  conformance must pass before any quant quality result is trusted.
- Configured context is not exercised context. A current
  [r/LocalLLM report](https://www.reddit.com/r/LocalLLM/comments/1vjy7n8/deepseekv4flash_0731_full_precision_lossless_on/)
  includes a concrete Q8/Q8-KV hybrid-offload recipe and a commenter claiming a 1M dual-Spark
  window, but that commenter had not exceeded 500K. Other threads report low Spark OS reserve or
  failures after roughly 100K. The acceptance gate must record actual input, output, memory use,
  and continued endpoint health.
- Harness choice can be larger than an apparent quant effect. A
  [local benchmark follow-up](https://www.reddit.com/r/LocalLLaMA/comments/1vjiypj/updated_benchmark_deepseek_v4_flash_on/)
  reports that switching from OpenCode to Pi recovered enough task performance to offset some
  apparent low-bit loss. r/DeepSeek, r/opencode, and r/hermesagent contain sharply contradictory
  experiences with the same model label.
- Community precision labels drift. The high-visibility r/LocalLLM post calls a Q8_K_XL GGUF with
  Q8 KV “full precision,” while the
  [Unsloth release discussion](https://www.reddit.com/r/unsloth/comments/1vbw4q1/deepseek_v4_flash_0731_out_now/)
  says FP8-to-Q8 conversion is not bitwise lossless. Qualification must capture exact artifact and
  component dtypes rather than repeat a post title.
- [Ollama doom-loop reports](https://www.reddit.com/r/ollama/comments/1vjmzh4/doom_loop_anyone_else_having_deepseek_v4_flash/)
  and the existing malformed `apply_diff` reports are useful failure-corpus leads, but their
  backends, templates, output caps, and cache precision are not controlled. They do not prove an
  NVFP4 quality defect.

## What changed since r16

### 1. The runtime moved more than the headline model settings

The local-inference-lab r29-r33 line reports fixes for verifier-row corruption at high
concurrency, fuller DeepSeek graph capture, repaired native tiered-offload lifecycle, and a
FlashInfer PCIe IPC all-reduce path. The pinned
[r33 recipe](https://github.com/local-inference-lab/rtx6kpro/blob/6c111c20c2bf2efec038e4daf14fc67030717e46/models/ds4dspark-v20-r33.md)
uses the same official 0731 checkpoint and retains fixed K5. Its external dual-GPU validation
reports 180.6 tok/s at C1, 397.1 aggregate tok/s at C4, 580.7 aggregate tok/s at C8, and
12,849 tok/s for its 8K prefill case. These are creator measurements from a native-Linux host,
not matched Anvil Serving results; they must not be compared directly with the local r16 numbers.

Automatic all-reduce now selects FlashInfer PCIe IPC for TP=2, while B12X remains an explicit
override. The source does not show a universal winner, so the correct local question is a pinned,
otherwise-identical A/B rather than assuming either implementation is superior.

### 2. K5 remains the stable common choice; K7 is workload-specific

The r33 source calls K5 the preferred mixed-workload setting and describes K7 as functional only
for predictable code. The actively maintained dual-Spark recipes from
[Tony Dinh](https://github.com/tonyd2wild/DeepSeek-v4-Flash-0731-DSpark-1M-NVFP4-KV-2x-DGX-Spark/tree/f277b3dfa718a5962bed64e69e7e640a5384ec2f)
and [MiaAI Lab](https://github.com/MiaAI-Lab/DeepSeek-v4-Flash-DSpark-2x-DGX-Spark/tree/a4ce87a2a73a22358eae3f9d07e8e06db87f8cee)
also use K5. That strengthens K5 as the default experimental arm, but a no-spec target-only lane
is still required because speculative gains and quality do not transfer automatically across
runtime, graph, context, concurrency, and prompt changes.

### 3. Dual Spark has converged on a recipe family, not one configuration

The Tony recipe runs the official FP8 weights over two Sparks with TP=2, a 1M-token window,
six maximum sequences, 0.78 utilization, K5, and NVFP4 DS-MLA KV. It includes explicit fixes for
cold-prefill corruption and shared-expert loading and now gives the second engine startup up to
3,600 seconds. The repository reports Patch 4 raising a content-dependent acceptance measure from
25.7% to 60.2% and mean generation from 32.7 to 55.4 tok/s. A corresponding
[Reddit field report](https://www.reddit.com/r/unsloth/comments/1vd0z8k/best_way_to_run_unsloths_deepseek_v4_0731_on_2x/)
also emphasizes selecting the live RoCE interface and GID instead of relying on automatic NIC
selection.

MiaAI's independent 1M recipe instead disables breakable CUDA graphs. It reports C1 decode rising
from 74.55 to 95.9 tok/s and C2 aggregate decode from 134.2 to 151.8 tok/s, plus 263.7 at C4 and
340.5 at C6, on its test method. That is a useful dual-Spark A/B hypothesis, but the referenced
container is a mutable tag rather than a published digest. The simpler
[eugr recipe](https://github.com/eugr/spark-vllm-docker/blob/e5f3cf9e5320d9a424966a801570bf452405d122/recipes/deepseek-v4-flash-0731.yaml)
keeps FP8 KV, K5, eight sequences, and full-plus-piecewise graphs; it provides a quality/control
lane but no matched 0731 performance claim.

### 4. Capacity, concurrency, and cache precision are separate choices

The dual-Spark 1M profiles leave little operating-system headroom. A
[community memory report](https://www.reddit.com/r/LocalLLaMA/comments/1vig3tw/serving_deepseek_v4_flash_0731_on_2x_dgx_spark_57/)
describes only 5-7 GB remaining and notes that ordinary CPU offload does not create a distinct
memory tier on unified-memory Spark systems. By contrast, the dual-PRO host has discrete GPU and
host memory; its native L1/file-L2 offload path is structurally relevant, but it remains gated on
managed cleanup and WSL2 mapping behavior.

A separate [dual-PRO community report](https://www.reddit.com/r/LocalAIServers/comments/1vgzvjs/built_a_2x_rtx_pro_6000_box_to_serve_deepseek/)
claims a 256K profile, 24 concurrent sequences, a 482,004-token KV pool, and 128 GiB of CPU KV
offload, with roughly 1,000 aggregate tok/s at C24. It does not publish an exact reproducible
recipe or runtime revision. It is retained only as a high-concurrency watch lane and test-shape
lead.

### 5. Agent protocol behavior remains a release gate

An active [vLLM pull request](https://github.com/vllm-project/vllm/pull/50686) addresses consecutive
assistant-message encoding in DeepSeek V4 histories; the reported failure mode can corrupt
reasoning/tool histories over repeated tool rounds. It was still open when captured and is not
treated as upstream-shipped behavior. A separate
[tool-use report](https://www.reddit.com/r/Vllm/comments/1vdwopg/looking_for_help_with_deepseekv4flash0731_on_vllm/)
describes intermittent malformed `apply_diff` output on a dual-Spark 1M/K5/NVFP4 setup. Every
candidate therefore retains multi-turn tool-result recovery, consecutive-assistant history, and
patch-format integrity gates; throughput alone cannot qualify one.

Official vLLM work has meanwhile added native DeepSeek V4 support using SparseMLA and fuller CUDA
graph capture in [PR #46995](https://github.com/vllm-project/vllm/pull/46995). The
[vLLM X announcement](https://x.com/vllm_project/status/2072545387639189798) is retained as the
social discovery source, while the merged pull request is the auditable technical source. Its
large-Blackwell results are architecture evidence, not dual-PRO or dual-Spark qualification.

## Decision sequence for a later campaign

1. Reproduce the pinned r33 startup and protocol contract at 131K without changing precision.
2. Qualify `pro-r33-quality-393k-fp8-target-only` with an actually exercised prompt above 300K,
   explicit output headroom, low/high/max prompt fingerprints, visible-answer checks, repeated
   tool recovery, both-card memory sampling, and continued endpoint health. In exclusive AI-only
   operation, no separate video-workload VRAM reserve is a pass/fail gate. This is the first
   decision-relevant quality arm.
3. If it passes, add K5 as an otherwise-identical comparison. Keep automatic-versus-B12X
   all-reduce as a separate performance A/B after correctness and stability are established.
4. Compare exact-0731 Auroter W4A16 weights against the native-weight control with FP8 KV held
   constant. Prove dual-card fit at 256K before attempting 393K.
5. Only after those gates compare FP8 and NVFP4 KV at matched context. Do not infer cache-quality
   equivalence from speed, adjacent-checkpoint weight benchmarks, or configured capacity.
6. Qualify 500K native tiered offload or 24-way concurrency last. Snapshot and restore shared
   memory, GPU ownership, router state, and the managed serve profile around any future live run.

## Scope, caveats, and publication safety

- Evidence here is external prior or community report. No claim is local confirmation.
- Source branches, mutable image tags, Reddit reports, and open pull requests are labeled as such
  in the JSON record. Immutable commit URLs and the r33 image digest are preserved where available.
- Reddit channels were searched broadly, but only concrete configurations, failure signatures,
  benchmark methods, and integration observations were retained. Praise, pricing, and popularity
  were not treated as technical corroboration.
- No external hostnames, IP addresses, GPU UUIDs, local paths, or credentials were copied into this
  public record. Some source pages contain operator-specific values; a future implementation must
  substitute manifest-owned interfaces and public placeholders.
- No model pull, serve lifecycle operation, route or mode change, live benchmark, or promotion
  occurred. There is no runtime restore action from this research campaign.
