# Find a better model or quantization

**Research reviewed: September 17, 2026.** Start with the workload you need to
improve, then compare exact configurations. A smaller checkpoint can make room
for context or concurrency, but it does not establish better answers or faster
completed work.

## What the local evidence supports

| Goal | Configuration to examine | Measured reason | Limit before replacement |
|---|---|---|---|
| Reliable coding and tools on two 96 GB cards | [GLM-5.3-Flash EXL3 4-bpw, no speculation](models/glm53-flash.md) | September 14 selection: agentic 30/30, frozen SWE 4/5, context 9/9, strict capacity 120/120 | Text only; no broad intelligence ranking, full-window concurrency soak, or fresh reboot proof |
| An alternative with image/OCR evidence | [Qwen3.8 Flash Next EXL3 4.05-bpw](models/qwen38-flash-next.md) | Fixed quality sample 91/100, context 9/9, image/OCR 12/12 | Agentic 21/30; strict120 has zero eligible timings because leading line feeds violate the canary contract |
| Lower warm TTFT on RTX 5090 | [Gittensor Qwen3.8 27B NVFP4, target only](qwen38-27b-rtx5090-quant-comparison.md) | 50.9 ms median warm TTFT and a successful 244,002-token actual prompt | Direct challenger only; calibrated KV, routed clients, broader quality, and endurance remain open |
| Clean bounded 64K speculation on RTX 5090 | [Unsloth Qwen3.8 27B Dynamic V3 NVFP4 + MTP3](models/qwen38-27b.md) | Tools 20/20; 137.7 tok/s warm decode in the September 3 matrix | Does not replace the retained GGUF configuration's 262K contract |
| Shorter answers from a fine-tune | [Signal and Swift Q6_K](models/qwen38-efficient-variants.md) | Each scored 9/10 on a small thinking-enabled diagnostic | Both failed the original exact-output scout; neither qualified a full 262K replacement |

These are separate hardware, runtime, and workload populations. The two
91/100 quality scouts do not demonstrate a meaningful advantage over GLM's
90/100 one-pass result. Read the [full inventory](models/index.md) for rejected,
incomplete, and historical models as well as selected configurations.

## Candidate shortlist

The source claims below are **external priors**, not local qualification or promotion results.
The first candidate now has a failed local startup attempt, recorded separately.
The priority below is an investigation order based on hardware relevance and
the gaps in the retained local evidence.

### 1. GLM-5.3-Flash mixed 3.5-bpw EXL3

The [quantizer's model card](https://huggingface.co/satgeze/GLM-5.3-Flash-EXL3-TR3-3.5bpw)
reports measurements from September 1–3 on two 96 GB SM120 GPUs: a mixed K3/K4
expert quant, 145.4 GiB checkpoint, and a one-million-token MTP3 profile. This is
close hardware correspondence, but the card's forced-generation timing and
quality protocols differ from our current no-speculation profile.

The [runtime source](https://github.com/satindergrewal/GLM-5.3-Flash-EXL3-3.5bpw-Mixed-SM120-TP2/tree/8fc95f0da72072a49a697f2164410b851a4e7377)
requires mixed-trellis B12X files and an EXL3 layer patch. Its Dockerfile refers
to a local base image; the active 4-bpw image is not a verified substitute.
The card also records DCP=1 and prefill-block constraints. Pin and validate
that runtime before interpreting a load failure as a model failure.

**Local outcome, September 17:** the [pinned managed startup](../findings/2026-09-17-glm53-mixed35-startup.md)
exhausted host RAM and swap before readiness and shut down the desktop session.
No inference request ran; the exact 4-bpw baseline was restored. **Retry gate:**
first add managed host-memory containment and diagnose loader peak RAM. Only
a safe startup survivor can advance to JSON/tools, 250K-class retrieval with
output reserve, and completed coding-work comparisons. Treat NVFP4 KV and MTP
as additional configuration changes; isolate them before claiming a quant-only
improvement. Checkpoint savings are not measured GPU savings.

### 2. Signal 27B NVFP4

The [quantizer's card](https://huggingface.co/Shockem/Signal-3.8-27B-NVFP4)
reports a ModelOpt 0.45.0 W4A16 conversion and agentic tests on two RTX 5060 Ti
cards. That hardware differs from the local RTX 5090 lane. The card identifies
sampling and speculative-decoding restrictions; its throughput and quality
claims remain upstream observations.

**Next test:** compare a pinned NVFP4 conversion against the already tested
Signal Q6_K and stock Qwen under identical coding tasks and budgets. First
resolve exact-output failures; count complete, correct work rather than
rewarding terse or truncated answers. No local NVFP4 Signal result is retained.

### 3. Qwen3.8 Flash Next at higher EXL3 precision

The [quantizer's repository](https://huggingface.co/turboderp/Qwen3.8-Flash-Next-exl3)
lists 5.05- and 6.05-bpw variants in addition to the tested 4.05-bpw release.
Their existence is verified; a local quality gain is not. First resolve the
4.05 strict-capacity formatting failure and establish a reproducible control.
Then test whether higher precision improves the nine missed agentic cases
without losing required context, latency, or headroom.

## Sources and comparison rules

| Source | Published or observed | Age at review | Evidence type | Decision impact |
|---|---|---|---|---|
| Mixed GLM card and pinned runtime above | Measurements September 1–3; inspected September 17 | 14–16 days | Quantizer's hardware-matched report | First runtime/feasibility investigation; no local performance transfer |
| Signal NVFP4 card above | Inspected September 17; measurement date not stated | Unknown measurement age | Quantizer's different-hardware report | Secondary efficiency lead, contingent on local correctness |
| Flash Next EXL3 repository above | Inspected September 17; measurement date not stated | Unknown measurement age | Artifact catalog | Higher-precision control candidate, not evidence of improvement |

For a causal comparison, hold hardware, model family, runtime, prompt set,
reasoning policy, output reserve, concurrency, cache state, and validators
constant; change one quantization or runtime feature at a time. If that is
impossible, label the result a comparison of complete configurations. Publish
failures alongside successes and restore the exact baseline after testing.
Promotion remains a separate human decision.
