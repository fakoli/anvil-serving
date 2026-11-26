# AZ-Focus retained after ERNIE's million-token retrieval; Inkling unselected on RTX 5090

**Date:** 2026-11-26
**Status:** completed bounded local evaluation; `no-promotion`; no route or default change.

<!-- benchmark-result-card/v1 -->
## Result card

> ERNIE demonstrated a single correct 999,990-token retrieval; the bounded
> fixed-set scores were AZ-Focus 49/60 and ERNIE 46/60. Inkling completed bounded original-runtime work,
> but neither lane changed a route, default, or deployment decision.

| Lane | Verified scope so far | Boundary |
|---|---|---|
| ERNIE 5.5 Flash Preview | Managed text protocol checks; fixed 60-case local comparison; bounded C1, C8, and C16 observations | Text-only; no broad quality or full-window reliability conclusion |
| Inkling 2 Mini | Original mixed-precision runtime completed 60 matched-cap cases; Q8 completed separate short protocol checks | Separate RTX 5090 lane; no cross-lane comparison |

**Measured setup:** direct local requests. ERNIE ran on Flare with the signed gb202 package, TP2, seven draft tokens, a 1,048,576-token per-request context limit and a 16 GiB host prefix cache; the quality workload was sequential C1
(the managed concurrency limit was configured C4), and the candidate-only
scale observations were C8/C16. Original Inkling used the public
[Inkling 2 Mini model card](https://huggingface.co/thinkingmachines/Inkling-2-Mini)
with vLLM, publisher mixed NVFP4/MXFP8 weights, DSpark, TP1 and 24,576 tokens of configured context. ERNIE’s detailed weight and KV-cache formats and both exact managed recipe revisions are absent from the public bundle. The linked public model cards describe the upstream packages.

**Measured hardware:** two NVIDIA RTX PRO 6000 Blackwell Max-Q GPUs for the
ERNIE/AZ-Focus lane; one NVIDIA GeForce RTX 5090 for the separate Inkling lane.
The C1 fixed-set cache state was not retained publicly. The C8/C16 receipt
records unique per-request prompts, a distinct expected marker per request and measured cache counters. The natural-output batches retrieved 8/8 and 16/16 exact markers. The fixed-length batches measured timing only. Cache reset and cold/warm state were unverified. Reproduction controls, source
hashes, and public-safe per-request receipts are in the [evidence index](2026-11-26-ernie-inkling-runtime-evaluation-evidence/README.md).

| Headline measurement | Result | Condition |
|---|---:|---|
| Fixed text set | AZ-Focus 49/60; ERNIE 46/60 | same 60 prompts; frozen scorer |
| Long retrieval | 999,990 input tokens | one target, one position |
| Fixed C8/C16 | 616/815 tok/s post-first | bounded fixed-output cells |
| Inkling matched output | 44/60 complete-correct | C1/24K, 16,384 maximum output |

See the [artifact manifest](2026-11-26-ernie-inkling-runtime-evaluation-evidence/artifact-manifest.json) and [evidence index](2026-11-26-ernie-inkling-runtime-evaluation-evidence/README.md).

The ERNIE fixed-set lane completed 60 paired text cases against a retained local
baseline: ERNIE was complete-correct on 46/60 and the retained baseline on
49/60; 44 items were complete-correct for both. The profiles are not
interchangeable controls: the candidate emitted more completion tokens and one
candidate response reached the local length limit. The result therefore does
not establish a quality ranking or a speed ranking.

ERNIE completed a single-target needle test at 999,990 input tokens: first
model token at 201.111 seconds and correct completion at 202.005 seconds. This
is one target at one position, not broad context reliability. A bounded C16
natural-output cell matched all 16 semantic checks and a separate C16
length-controlled cell completed; their aggregate timings are retained as
descriptive measurements, not a broad service-speed claim.

The [evidence index](2026-11-26-ernie-inkling-runtime-evaluation-evidence/README.md)
contains the retained request receipts, aggregate summaries, and reconstruction
boundaries.

## Dual RTX PRO 6000 text lane

The matched 60-case comparison scored AZ-Focus 49/60 and ERNIE 46/60. Forty-four
were correct for both, with five AZ-only and two ERNIE-only successes. ERNIE
used 95,688 completion tokens and 447.929 summed sequential request seconds;
AZ-Focus used 36,662 and 215.195 seconds. The shared-correct subset summed to
128.202 seconds for ERNIE and 107.965 seconds for AZ-Focus. These are fixed-set
request sums, not outer wall time, throughput, or a general ranking.

An independent offline reconstruction verified all 180 stored HTTP request
payload hashes across the three arms. The frozen legacy scorer remains AZ-Focus 49/60, ERNIE 46/60, and
Inkling 44/60. A separate visible-answer-only sensitivity scorer yields
50/46/46; it changes the interpretation of appended reasoning, not the frozen
historical record. The three sequential sums were 215/448/766 seconds and
36,662/95,688/187,094 completion tokens. This distinction is why a local speed
observation is not itself a useful-work ranking.

ERNIE passed independent agentic 3/3 and Aider 2/2 checks. Its five-issue SWE
scout had one graded resolution, two graded unresolved issues, and two
call-budget exhaustions before grading; it is incomplete rather than a suite
score. The scorer sensitivity, these bounded checks, and their public-safe
source hashes are retained in
[`quality-scope-receipts.json`](2026-11-26-ernie-inkling-runtime-evaluation-evidence/quality-scope-receipts.json).
The restored AZ-Focus direct smoke, JSON, tools, tool-result, streaming-tools,
and image checks passed; ERNIE is text-only.

The C8/C16 fixed-output cells measured 616/815 tokens/s from first output to
last completion and 353/359 tokens/s over the entire batch. Natural-output
cells returned 8/8 and 16/16 semantic matches. Local Inference Lab reports
about 919/1,340 tokens/s for nominal C8/C16, but its workload, timing boundary,
GPU product, cache setting, and driver setting differ. It is a reference, not a
reproduction or a causal comparison.

The public-safe per-cell timing, context range, output count, measured cache
counter deltas, and source hashes are retained in
[`c8-c16-capacity-receipts.json`](2026-11-26-ernie-inkling-runtime-evaluation-evidence/c8-c16-capacity-receipts.json).

## Inkling and restoration

At C1/24K with a matched 16,384-token maximum output, the original Inkling
runtime transported 60 requests; 44/60 were correct and complete-correct, four
stopped at the limit, and complete-correct median time was 4.274 seconds. Inkling
C2/8K was refused before creation by the host memory guard (38.6 GiB available
versus 45.1 GiB required). A separate existing Swift on-demand recipe was also
refused before creation (41.3 GiB available versus 58.0 GiB required), so no
matched comparison was fabricated. The primary text service
and RTX 5090 split-mode baseline were restored; no candidate was selected.
