# GLM-5.3-Flash Cardillo/Purtell dual-PRO qualification

**Date:** 2026-08-29

**Scope:** two RTX PRO 6000 Blackwell Max-Q cards, WSL2, exclusive TP=2,
text/tools/image/OCR, fixed-MTP and no-speculation controls

**Decision:** `challenger`, `no-promotion`; vision-enabled fixed K5 at 262K is
the preferred interactive GLM profile, no-speculation at 524K is the preferred
maximum-context profile, adaptive MTP is rejected, and the unserved 0xSero
3.0-bpw TP=4 layout is a watch-only lead

<!-- benchmark-result-card/v1 -->
## Result card

> GLM-5.3-Flash TR3/EXL3 4 bpw on two RTX PRO 6000 Blackwell Max-Q cards
> qualified as a text/tools/image/OCR challenger with fixed five-token MTP,
> while the externally proposed adaptive MTP configuration failed repeated
> tool use.

| Setup | Qualified value |
|---|---|
| Model | `brandonmusic/GLM-5.3-Flash-tr3-4bpw@5ab363a8dcf6405955fd5f99671e01a1c9fb124b`; served as `glm53-flash-tr3-4bpw-tp2-262k-fixed-mtp5-vision` for the preferred profile |
| Hardware | 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, 96 GB each, sm_120, TP=2 over PCIe without NVLink |
| Runtime | pinned Purtell image digest; ShapleyMCG TR3/EXL3 4 bpw weights; NVFP4 DS-MLA KV; fixed K5 speculation |
| Recipe | [managed vision-enabled fixed-K5 262K recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-vision-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml) |
| Measurement path | warm direct OpenAI-compatible API under Docker Desktop/WSL2 |
| Contract | 262,144-token ceiling, c1 performance samples, maxseq16, text/tools plus locally proven one-image/OCR, video disabled, low reasoning unless stated |
| Evidence | functional gates, three-repetition performance samples, bounded 15-attempt coding suite, and calibrated near-500K companion-profile probes complete |
| Decision | `challenger`, `no-promotion`; no route, client, or promotion state changed |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Vision fixed-K5 decode | 72.8 tok/s at 4K; 55.7 tok/s at 128K | c1, low reasoning, three repetitions per depth |
| Reliable capability | image understanding, verbatim OCR, tools 20/20, bounded high-reasoning coding 15/15 | vision fixed-K5 profile; direct API |
| Long interactive context | exact retrieval at a 250K target / 206,296 actual prompt tokens | vision fixed-K5 262K profile; one calibrated request |
| Maximum-context control | exact retrieval at 495,045 prompt tokens; valid tool call at 497,976 | no-spec 524K profile; one calibrated request per gate |
| Retained failure | adaptive tools 12/20 | adaptive K1-K5 plus ReplaySSM; repeated structured-tool gate |

**Why it matters:** the same exact 4-bpw weights can expose their visual tower
without giving up the reliable fixed-K5 text/tool lane. A separate no-spec
control provides a reliable near-500K session option when context and scheduling
headroom matter more than image input.

**Important caveat:** video is explicitly disabled, only one-image behavior was
locally qualified, and the current Qwen Primary remains faster. This bounded
campaign does not establish a broad intelligence win. Adaptive MTP is a
correctness failure, not a promotion candidate.

Evidence manifest:
[raw artifacts](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/README.md) ·
Publication summary:
[derivative copy](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/publication-summary.md)

## Outcome

The strongest reliable local GLM configuration is vision-enabled fixed
five-token MTP without adaptive depth or ReplaySSM. The
[262K vision fixed-K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-vision-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml)
is the default hands-on profile because it combines image understanding,
verbatim OCR, 20/20 tool correctness, an exact 250K-target / 206,296-actual
retrieval pass, and more
operating margin than its 524K companion. The
[524K no-spec recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-nospec-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml)
is the maximum-context profile because it passed near-500K retrieval and tool
use while retaining 3.06 complete configured windows of reported KV capacity.

The [524K fixed-K5 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-fixed-mtp5-sm120-tp2-524k-wsl2-v2-no-owner-exchange-recipe.toml)
also passed the near-500K gates and is useful for a single-user experiment, but
its 565,898-token KV pool is only 1.08 configured windows and the host reported
about 1.3 GB physical VRAM free per card. It is not the default concurrency or
headroom choice.

The externally proposed adaptive K1-K5 plus ReplaySSM configuration is
[retained as rejected evidence](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-cardillo-tpurtell-adaptive-mtp-sm120-tp2-262k-wsl2-v2-no-owner-exchange-recipe.toml).
It was fast, but repeated tools fell to 12/20 and output degenerated into
repeated `handle` fragments. This is a correctness failure, not a tuning
preference.

## Immutable identity

- Checkpoint: `brandonmusic/GLM-5.3-Flash-tr3-4bpw`
- Revision: `5ab363a8dcf6405955fd5f99671e01a1c9fb124b`
- Runtime image:
  `ghcr.io/tpurtell/glm-5.3-flash-exl3-4bpw-2x-rtx@sha256:da5cec95778bf6996660b52e28a6e51737fec69cfc3d508bf298c8a89f273ac5`
- Runtime source:
  `tpurtell/glm-5.3-flash-ext3-4-bit-2x-rtx@3bff1d5fdbafcc3d9865abebddbfe1eef435adef`
- Cardillo integration source:
  `samuelcardillo/glm-5.3-flash-2x-rtx-pro-6000-blackwell@5b5623ea07f48683f37f3774d8d5b8bf5b04fdf0`
- Runtime-reported vLLM:
  `0.1.dev20051+g487ecf187`
- Quantization: ShapleyMCG TR3/EXL3 4 bpw weights, NVFP4 DS-MLA KV
- Hardware: 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, 96 GB each, sm_120,
  PCIe without NVLink, Windows 11/Docker Desktop/WSL2

The exact model snapshot occupies 175,788,141,869 bytes in the local cache.
The publisher tree and image are community artifacts. Their source repository
uses the ShapleyMCG license rather than an OSI license and does not provide the
complete reproducible image-build and quantization provenance expected for a
first-party release. Those gaps are promotion caveats; this repository does
not mirror the weights or image.

## Current-source research and candidate selection

| Source | Observed | Evidence class | Relevant claim or recipe lead | Local decision impact |
|---|---|---|---|---|
| [Cardillo dual-PRO repository](https://github.com/samuelcardillo/glm-5.3-flash-2x-rtx-pro-6000-blackwell/tree/5b5623ea07f48683f37f3774d8d5b8bf5b04fdf0) | 2026-08-29 | community recipe | native-Linux TP=2, 262K, adaptive MTP K1-K5, ReplaySSM, NVFP4 MLA KV, text/tools/vision | strongest hardware-matched starting point; all headline results treated as external priors |
| [Purtell runtime source](https://github.com/tpurtell/glm-5.3-flash-ext3-4-bit-2x-rtx/tree/3bff1d5fdbafcc3d9865abebddbfe1eef435adef) | 2026-08-29 | community runtime | custom EXL3/B12x vLLM fork and image; publisher reports up to 500K NVFP4 context and fixed/adaptive speculation results | selected exact digest and source revision for the local translation |
| [Exact Hugging Face quant](https://huggingface.co/brandonmusic/GLM-5.3-Flash-tr3-4bpw/tree/5ab363a8dcf6405955fd5f99671e01a1c9fb124b) | 2026-08-29 | community checkpoint | 4 bpw TR3/EXL3 conversion with target, visual, and MTP weights | selected because the exact image/recipe pair was hardware-matched and fit the 192 GB aggregate budget |
| [0xSero 3.0-bpw layout](https://huggingface.co/0xSero/GLM-5.3-Flash-EXL3-3.0bpw) | 2026-08-29 | community checkpoint and self-audit | selective K3 experts with BF16 backbone/vision/MTP, 149.56 GB files, custom TP=4 loader required; full load/server/API/vision/MTP acceptance not run; held-out PPL/KL gates failed | watch only; the publisher's own ledger does not support spending a dual-PRO qualification cycle yet |
| [0xSero Q4 sibling](https://huggingface.co/0xSero/GLM-5.3-Flash-EXL3-Q4) | 2026-08-29 | community checkpoint and self-audit | stronger reported quant-quality deltas than the 3.0-bpw arm, but the same custom TP=4 serve path remains incomplete | useful future runtime lead, not a current replacement for the locally working TP=2 pair |
| [Official BF16 tree](https://huggingface.co/zai-org/GLM-5.3-Flash-BF16/tree/f12e0fe1f6b2ea274c11a569582edfd99d993c5e) | 2026-08-29 | first-party identity prior | official base identity and model files | provenance anchor, not a fitting two-card recipe |
| [LocalLLaMA megathread](https://www.reddit.com/r/LocalLLaMA/comments/1vyzzxu/megathread_glm53flash_former_oxalpha/) | 2026-08-29 | practitioner reports | multiple runtime/quant leads and early compatibility reports | discovery only; no Reddit report was promoted into local evidence |
| [vLLM issue 47292](https://github.com/vllm-project/vllm/issues/47292) and [PR 47579](https://github.com/vllm-project/vllm/pull/47579) | 2026-08-29 | upstream compatibility | evolving upstream GLM-5.3 support | reinforced use of the pinned custom runtime rather than an unpinned generic image |

The supplied X post could not be fetched directly by the research client, but
its linked GitHub repository resolved and was inspected at the immutable commit
above. External speed, context, retrieval, vision, and quality statements remain
external priors unless reproduced in this finding.

## Feasibility screen

The pre-load interval model used a 250,000-token prompt plus 8,192 output
reserve. The text-only candidate had an estimated safe-envelope margin of
1.210-6.731 GiB; the vision-loaded envelope was only 0.160-4.631 GiB. Both
were paper-feasible and survived mathematical screening. The vision arm was
then loaded and qualified because it preserved more than two complete 262K KV
windows while adding useful image/OCR capability.

The exact inputs and interval result are retained in
[`feasibility-input-v0.json`](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/feasibility-input-v0.json)
and
[`feasibility-result-v0.md`](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/feasibility-result-v0.md).

## WSL2 translation

The upstream native-Linux recipe did not start unchanged. The final managed
translation preserves the exact model, image, EXL3/B12x compute path, TP=2,
DCP=2, NVFP4 DS-MLA KV, 2,048-token batch chunks, maxseq16, prefix cache,
tool/reasoning parsers, and 0.95 memory utilization. It changes only bounded
transport and WSL2 controls:

| Failure boundary | Local evidence | Final translation |
|---|---|---|
| NCCL cuMem | CUDA error 999 before load | `NCCL_CUMEM_ENABLE=0` |
| CUDA expandable allocator | allocator initialization failure | `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:False` |
| custom all-reduce CUDA IPC | peer import failed | `--disable-custom-all-reduce` and PCIe custom path off |
| V2 UVA under WSL2 | UVA capability rejected | explicit `VLLM_WSL2_ENABLE_PIN_MEMORY=1` |
| B12x DCP A2A CUDA IPC | worker initialization failed | `VLLM_B12X_DCP_A2A=0` |
| lazy top-K owner exchange | first 128K prefill failed on CUDA IPC import | `VLLM_B12X_DCP_TOPK_OWNER_EXCHANGE=0` |

A V1 fallback loaded all 120 weight shards but could not preserve the required
MTP/NVFP4 combination, so it was abandoned. FP8 KV was also tested and rejected
because the runtime required an incompatible block-size path. Every load and
unload used the managed recipe lifecycle.

## Matched local results

All performance rows are low-reasoning, direct, c1 unless stated otherwise.
The three 4K and 128K repetitions use the same checkpoint, image, context
contract, WSL2 controls, parsers, and measurement harness. The intended causal
difference is the speculation mode.

| Profile | 4K TTFT / E2E | 4K decode | 128K TTFT / E2E | 128K prefill | 128K decode | Tools | Bounded coding |
|---|---:|---:|---:|---:|---:|---:|---:|
| adaptive K1-K5 + ReplaySSM, 262K | 1.05 / 1.83 s | 71.1 tok/s | 32.88 / 33.81 s | not retained as decision data | 59.5 tok/s | 12/20 repeated | 13/15 low |
| no spec, 262K | 1.13 / 2.23 s | 42.7 tok/s | 30.89 / 31.92 s | 3,422 tok/s | 43.5 tok/s | 20/20 | 14/15 low |
| fixed K5 text, 262K | 1.06 / 1.76 s | 69.8 tok/s | 32.90 / 33.69 s | 3,133 tok/s | 61.9 tok/s | 20/20 | 14/15 low; 15/15 high on 524K companion |
| **fixed K5 vision, 262K** | **1.05 / 1.57 s** | **72.8 tok/s** | 32.84 / 33.69 s | 3,138 tok/s | **55.7 tok/s** | **20/20** | **15/15 high** |

Fixed K5 improved matched decode by 63.3% at 4K and 42.1% at 128K versus
no-speculation while preserving the complete functional gate. It also passed
streaming tools, tool-result continuation, Responses, agent-protocol checks,
and a structured tool call beyond 100K prompt tokens.

The vision-enabled arm stayed within run-to-run variation at 4K and was about
10% slower than the text-only fixed-K5 arm at 128K. Its full preflight passed
smoke, JSON, tools 20/20, streaming tools, tool-result continuation, Responses,
semantic image understanding, and verbatim OCR twice. The visual encoder and
multimodal warmup initialized in the target runtime; this was not a
text-fallback answer. Video was disabled and multiple-image behavior was not
qualified. The MTP drafter cannot consume external visual embeddings, so image
requests give the image to the target model while the draft path receives text
only. That retained correctness locally but may reduce speculative benefit on
image calls.

The single missed low-reasoning coding item in both reliable profiles was a
Windows-safe recursive-move plan check. It passed only two of three attempts.
The 524K fixed-K5 high-reasoning rerun passed all five items at three
repetitions each, or 15/15 attempts. This is a bounded diagnostic result, not a
general intelligence score. The artifact records the requested high-reasoning
control, but the server does not independently prove that the requested effort
maps to a distinct internal policy.

## Maximum-context and concurrency results

| Profile | Reported KV tokens | Full 524,288-token windows | Near-limit retrieval | Near-limit tool | c16 short aggregate |
|---|---:|---:|---|---|---:|
| no spec, 524K | 1,603,111 | 3.06x | exact needle at 495,045 prompt tokens in 144.3 s | valid tool at 497,976 prompt tokens in 143.7 s | not rerun at 524K; 262K companion: 33.69 output tok/s |
| fixed K5, 524K | 565,898 | 1.08x | exact needle at 495,045 prompt tokens in 166.0 s | valid tool at 497,976 prompt tokens in 158.0 s | 23.85 output tok/s, 16/16 |

The 524K fixed profile's c16 result does not establish 16-way full-window
capacity. It is a short-prompt scheduling diagnostic. Its p50 per-request TTFT
was 21.74 seconds and E2E was 24.55 seconds. The no-spec 262K control completed
16/16 with 10.77-second p50 TTFT, 20.63-second E2E, and 33.69 aggregate output
tok/s.

The preferred vision profile reported 560,866 KV tokens, or 2.14 complete
262,144-token windows. It recovered `ZEBRA-42917-QUARTZ` at a 250K target,
206,296 actual prompt tokens, in 66.9 seconds. Its c16 short diagnostic
completed 16/16 at 28.3
aggregate output tok/s with 18.45-second p50 TTFT and 21.01-second p50 E2E.
This proves short-request scheduling at c16, not sixteen simultaneous 262K
windows; the reported KV pool supports only about two complete configured
windows before other limits.

## Why the 0xSero 3.0-bpw quant was not selected

The user-supplied `0xSero/GLM-5.3-Flash-EXL3-3.0bpw` is not the model tested in
this campaign. It is a different, selective quantization layout: only routed
expert gate/up/down tensors in language layers 3-44 are K3, while the backbone,
shared path, dense layers 0-2, embeddings/head, visual components, and MTP
remain BF16. Its published files total 149.56 GB, about 26.2 GB or 14.9% less
than the 175.79 GB logical size of the working 4-bpw snapshot. That makes it
paper-feasible on two 96 GB cards and could leave more KV headroom.

The current release is not a usable two-card recipe, however. Its own model
card and release ledger require a custom selective-EXL3 TP=4 loader; stock
Transformers, vLLM, SGLang, and generic EXL3 loaders are explicitly excluded.
Primitive checks ran four logical ranks in two waves across two physical GPUs,
not a complete dual-GPU TP=4 server. Full model load, OpenAI-compatible API
generation, vision execution, and MTP execution remain unrun, and no launcher
is published pending acceptance.

The publisher also records a failed held-out quality gate: perplexity rose from
3.19940 to 3.49685 (+9.30%), forward KL was 0.15251 against a 0.15 ceiling, and
top-1 agreement was 87.28%. Its release status is
`weights_public_validation_incomplete`. Those are unusually useful negative
disclosures, but they mean the apparent memory gain does not outweigh the
unimplemented runtime and failed quality gate for this campaign. The sibling
Q4 release reports better quant-quality deltas but still depends on the same
unfinished custom TP=4 serving path. Both remain watch leads; neither justified
another roughly 150-187 GB download while the exact TP=2 4-bpw pair was already
locally functional and quality-gated.

## Current Primary comparison

The current Qwen3.8 Flash Next QSA-fast MTP3 Primary remains substantially
faster and retains text/image/OCR/video acceptance. Its qualified same-host
reference is 154.9 tok/s decode at 4K and 134.1 tok/s at 128K, versus the
vision GLM's 72.8 and 55.7. GLM therefore delivers about 47.0% and 41.5% of the
current Primary's decode rate at those two depths, and its 128K TTFT is about
2.6x higher. This is a directional local serving comparison, not a matched
model-quality ranking.

GLM's distinct value is its qualified text/tool/image/OCR behavior and
near-500K text-prompt capacity in this quant/runtime pair. The current Qwen record validates a
253,703-token prompt plus 8,192 output request and extensive multimodal/client
acceptance. No common broad intelligence or repository-agent suite has been
run across both exact profiles, so this campaign does not establish that GLM
is more intelligent than Qwen.

## Promotion and hands-on boundary

The campaign closes as `challenger`, `no-promotion`. No router alias, client
catalog, serve manifest, production route, or operator promotion state changed.
Before promotion:

1. Run hands-on use against the 262K vision fixed-K5 profile, with high
   reasoning for complex work and low reasoning for latency.
2. Use the 524K no-spec profile for sessions that actually need near-500K
   context or greater scheduling headroom.
3. Require routed exact identity, admission, streaming, tool-result recovery,
   and real Hermes/Pi/OpenClaw acceptance before any production change.
4. Decide whether GLM's perceived intelligence advantage is worth the
   materially slower decode, disabled video, and community-runtime provenance.

The 524K fixed-K5 profile is available for a deliberate single-user experiment,
but its narrow 1.08x KV envelope keeps it behind the two-profile recommendation.

## Cleanup and restoration

All intermediate candidate containers were unloaded through
`models recipes unload`. The final vision fixed-K5 service was intentionally
left running on the direct loopback endpoint for the hands-on gate. It consumes
both GPUs as a direct candidate but is not selected by the production router;
no route, client configuration, or promotion state changed. When the hands-on
gate ends, it can be unloaded through the same managed recipe lifecycle.

The exact model snapshot and pinned runtime image remain cached for the
hands-on gate. No model snapshot was deleted because the older revision shares
all 120 weight files with the exact revision; deleting it would not recover the
apparent weight size. No broad Docker prune or VHDX compaction was performed.

## Evidence

The [evidence manifest](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/README.md)
links the bounded raw artifacts and SHA-256 hashes. The
[publication summary](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/publication-summary.md)
contains derivative short-form copy and a claim ledger. Raw artifacts contain
the measured values; this narrative owns the interpretation and promotion
boundary.
