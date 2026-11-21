# GLM-5.3-Flash derivative survey: matched fixed-set result

**Date:** 2026-11-21
**Status:** completed focused local measurement; `no-promotion`; restored serving with the pre-existing monitoring-registration gap retained.

<!-- benchmark-result-card/v1 -->
## Result card

> On the fixed local 60-case subset, AZ327-Focus and Mia tied at 49/60; AZ327
> used less summed sequential C1 request time, without establishing an accuracy,
> throughput, capacity, or promotion result.

| Setup | Qualified value |
|---|---|
| Model | Mia EXL3 TP2 and AZ327-Focus EXL3 TP2; distinct snapshots |
| Hardware | Two RTX PRO 6000 Blackwell Max-Q GPUs, TP2 |
| Contract | 60 fixed text/reasoning cases, 16,384-token cap, sequential C1 |
| Measurement | Sum of individual request times, not outer wall-clock |
| Decision | `no-promotion`; no default, route, or deployment change |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Complete correct | 49/60 each | literal publisher parser |
| Summed sequential C1 request time | 215.195 s AZ vs 487.233 s Mia | 55.83% less for AZ |
| Important boundary | no broad speed conclusion | matched C4 cells are distinct n=8 observations |

**Important caveat:** coverage is partial: the paired SWE result is only five
official images, broad capacity/concurrency remains unmeasured, and the
pre-existing monitoring-registration gap is retained.
See the [evidence index](2026-11-21-glm-derivative-survey-evidence/README.md),
[artifact ledger](2026-11-21-glm-derivative-survey-evidence/artifact-manifest.json),
and [publication summary](2026-11-21-glm-derivative-survey-evidence/publication-summary.md).

## Decision

On one fixed 60-case text/reasoning set, the final AZ327-Focus EXL3 profile and the
Mia EXL3 profile achieved the same publisher-parser score: 49/60 complete
correct responses (25/30 MMLU-Pro and 24/30 BBH). AZ327-Focus completed that
sum of sequential C1 request times of 215.195 seconds versus 487.233 seconds
for Mia: 2.26× as fast by this summed-request measure, or 55.8% less summed
request time. This is matched local per-request timing
evidence, not an accuracy improvement, general throughput claim, capacity
result, or promotion recommendation.

The campaign also retained a separate one-card Qwen FP4 compatibility lane.
It received all eight responses but none was performance-eligible because the
opening canary failed in every case; it supplies neither a timing nor a quality
comparison.

## Matched GLM measurement

Both profiles used the same 60 inputs, request controls, external literal
publisher parser, and 16,384-token local output cap. The paired corpus and
parser hashes are retained in the raw extracts. The reported total is the sum
of individual sequential C1 request times, not an outer-process wall-clock
duration, token throughput, or time to solve a shared interactive task.

| Profile | Complete correct | Length-limited | Sum of C1 request times | Completion tokens |
|---|---:|---:|---:|---:|
| Mia EXL3 TensorFold | 49/60 | 1 | 487.233 s | 86,651 |
| AZ327-Focus EXL3 TensorFold | 49/60 | 0 | 215.195 s | 36,662 |

The profiles were jointly correct on 46 cases. Each had three cases the other
missed; the exact two-sided McNemar result is 1.0. For jointly correct cases,
the median request wall latency was 1.297 s for Mia and 0.970 s for AZ327-Focus.
AZ327 had lower request time on 35/60 requests; its median paired per-request
speed ratio was 1.09118×. These per-request figures are distinct from the
summed-request-time ratio.
All 60 visible answers are byte-identical to the earlier AZ64 run, but all 60
request and raw-event digests differ; the final result is a real reexecution,
not a reused trace. The [per-case digest receipt](2026-11-21-glm-derivative-survey-evidence/az-reexecution-digests.json)
retains the public-safe hash proof without prompts or answers. These facts do
not establish an accuracy uplift.

The public evidence describes dual RTX PRO 6000 Blackwell Max-Q TP2 local
measurement. AZ327 used the same 327,680 configured context, runtime image,
and invocation flags as Mia except for name/port and offline-download settings;
the model snapshots remain distinct. It passed a standard approximately-128K
retrieval check and a 20/20 shared-prefix tool batch. Those are bounded
functional checks, not a context or broad tools qualification.

The matched 327K/C4 summary does not show a broad concurrent speed win:
AZ327 first-visible latency was 4.579 s versus Mia's 4.027 s, median end-to-end
latency was 5.258 s versus 5.279 s, and aggregate output was 48.93 versus
106.94 tok/s with 86.5 versus 191.38 output tokens per request. The
[machine-readable AZ327 C4 receipt](2026-11-21-glm-derivative-survey-evidence/raw/az327-natural-c4.json)
and [Mia C4 receipt](2026-11-21-glm-derivative-survey-evidence/raw/mia-natural-c4.json)
retain the eligible eight-request populations. The serial 60-case result
therefore does not support a broad concurrent-speed claim.

The [matched-C4 observation matrix](2026-11-21-glm-derivative-survey-evidence/graphs/paired-c4-matrix.svg)
and its [numeric data](2026-11-21-glm-derivative-survey-evidence/graphs/paired-c4-matrix-data.json)
show the AZ327/Mia and Swift/Mia pairs separately; they are not pooled.

## Paired SWE-Bench Verified five-image scout

Both arms completed the same five independently checked official images, with
the same selection, profile hash, request controls, agent/grader revisions,
container limits, prompt contract, and network policy. AZ resolved 4/5 in
740.844 s of agent time; Mia resolved 3/5 in 1668.975 s. Both resolved SymPy
and xarray; AZ alone resolved Sphinx and pylint; Mia alone resolved Django.
Official grading took roughly two minutes per arm. This is a five-image scout,
not a full SWE-Bench score or a promotion recommendation.

See the [paired per-case projection](2026-11-21-glm-derivative-survey-evidence/raw/paired-swe-five.json)
and [native-score provenance](2026-11-21-glm-derivative-survey-evidence/native-scores/oracle-outcomes.json).

## AZ327 capability scout

AZ327 resolved both bounded Aider tasks (Affine and Go) on their first attempts,
passed all 12 context checks across 8K and 32K, and passed its 3/3 native
agentic task set (structured-edit rev8, tool-sequence rev8, debug-loop-v2 rev9).
The matched Mia controls used the same OCI oracle: Mia passed
Affine first attempt and Go second attempt, while AZ passed both first attempt.
Two earlier missing-oracle failures occurred before inference; the recovery used
a verified same-day cache and the matched oracle image. This remains a scoped
capability comparison, not a broad quality conclusion.

## Swift light scout

The Swift light profile exercised four actual 24,576-token slots, a configured
vision projector, and a two-GPU layer split. Its 2,048-cap oracle arm passed 3/3
semantic and 3/3 exact checks; the Mia arm passed 3/3 semantic and 2/3 exact.
The vision observation was console-only and length-stopped, without a raw image
receipt; it establishes configuration, not accepted visual accuracy. The clean
C4 seed-482204 run was 8/8 eligible with 0.17% cache hit, 21.419 s first-visible,
23.065 s end-to-end, and 46.377 tok/s aggregate output. The matched Mia cohort
was also 8/8 clean (0 cache hit, 5.408 s TTFT, 6.489 s end-to-end, 118.029 tok/s).
This is a matched C4 result, not a general capacity or deployment conclusion.
The initial C1, n=3, temperature-1 timing population is not comparable to Mia;
The matched natural C4 cohort is retained above. This is a narrow scout, not a
speed, capacity, or replacement result.

See the [capability-scout aggregate](2026-11-21-glm-derivative-survey-evidence/capability-scouts-summary.json).

## Tev compact scout

The compact 146.01-GiB Tev community release loaded as a two-card layer split
with four 24,576-token text-only slots. It passed smoke, JSON, and 20/20 tools,
plus 3/3 native agentic tasks (structured-edit rev8, tool-sequence rev8,
dependent-result rev8) and Go alphametics 1/1 on its second attempt. Its
oracle image matched the earlier Mia 0/1 image on one historical case; that is
not a comparative control. No visual, speed, capacity, or replacement claim
follows.

## Separate compatibility lanes

The one-card Qwen FP4 lane is a bounded functional receipt only: 8/8 responses
arrived, but all 8 failed its opening-position validation, so the eligible
population is 0/8. The AZ64 native template's image path is a separate failed
profile: it substituted image parts with text and produced 12/12 pre-generation
HTTP 500 outcomes. The final AZ327 profile instead used Mia's exact image-built
template and passed all 12/12 image cases. This establishes only that bounded
image-case result, not broad vision qualification.

QAD R2 repeated the C4 boundary: all eight responses had exactly one nonleading
marker and zero foreign markers at client concurrency four, leaving 0/8
performance-eligible. R3 then passed bounded multimodal 12/12 and native
agentic 3/3 (structured-edit rev8, tool-sequence rev8, dependent-result rev8) on one
NVIDIA GPU. Its NVIDIA recipe uses a patched vLLM build, FP8 KV, configured
262,144 tokens, and about 26.82 GiB host embedding-table offload; it is not a
GPU-only fit or a tested full-window result. No timing canary rerun or
performance result is claimed. The pinned [NVIDIA model and recipe
source](https://huggingface.co/nvidia/Qwen3.8-Flash-Next-FP4-Local/tree/b9ce18a6887e18c089fdea8509a33e63429a3300)
is retained in the configuration reconstruction.

The final one-card QAD 12-item publisher pilot scored 10/12, alongside AZ327
10/12 and Mia 9/12 on identical items. Its summed request time was 183.073 s
and 35,865 completion tokens, versus AZ327 39.97 s and Mia 57.36 s. QAD uses
`xhigh` while GLM uses `max`, with different engines and GPU counts; this is a
partial cross-runtime pilot, not a broad speed or quality ranking.

## Comparability limits

This local pair uses a 16,384-token cap and a top-level reasoning-effort
control. The publisher's external demonstration used a 32,768-token cap and a
different template-control surface. Therefore, this finding compares the two
local profiles with each other; it is not a direct reproduction of an external
published score or speed result.

Managed candidate trial loads and unloads occurred during this campaign. No
promotion, default, or route change is claimed. A verified Mia restoration
checkpoint returned authenticated primary-alias HTTP 200/READY at 327,680
configured context and direct running status. The monitoring-registration gap predates this campaign;
restored serving remains working, but that gap is retained and no promotion is
authorized. See the [restoration record](2026-11-21-glm-derivative-survey-evidence/restoration.json).

## Evidence

- [Evidence index](2026-11-21-glm-derivative-survey-evidence/README.md)
- [GLM-5.3-Flash dossier](../benchmarks/models/glm53-flash.md)
- [Qwen3.8 Flash Next dossier](../benchmarks/models/qwen38-flash-next.md)
- [Benchmark run catalog](../benchmarks/runs.md)
