# NVIDIA GLM-5.3-Flash-FP4-Local controlled A/B

**Date:** 2026-10-17

**Later performance follow-up:** the separate
[matched serving comparison](2026-10-17-glm53-serving-performance.md) now records
prefill, first-output/visible latency, decode, concurrency, KV and memory. The
historical timing gaps and results below describe this earlier campaign only.

**Diagnostic status:** the initial pass stopped and restored after 17/18. An
unchanged full replay passed 18/18, and two targeted replays of the missed case
passed 1/1 each. Second restoration completed. `promoted=false`; no
candidate promotion or route change is authorized.

<!-- benchmark-result-card/v1 -->
## Result card

> The NVIDIA FP4 candidate passed direct functional, image, agentic, and
> 32K-context checks, then failed one near-limit needle case at 316249 actual
> prompt tokens. The initial frozen stop preceded candidate timing, overlap, and
> no-speculation measurement. A later authorized unchanged replay passed 18/18,
> and two isolated replays of the original case passed 1/1 each.

| Setup | Recorded configuration |
|---|---|
| Candidate | `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5` |
| Runtime | `ghcr.io/local-inference-lab/vllm:release-10152026@sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29` |
| Candidate recipe | NVIDIA direct recipe: NVFP4/MXFP8 compressed scales, FP8 KV, TP2, DCP2, MTP3, fixed KV budget; API-advertised/configured 327680 tokens and C4 |
| Containment | r2 used a 52 GiB memory limit, effective zero swap, 12 GiB host reserve, and pinned local snapshot; r1 failed its 1 MiB-swap admission before container creation |
| Control | `brandonmusic/GLM-5.3-Flash-tr3-4bpw@a5fee929cf4888b1824323e33e8a19b60129e025`, EXL3 4 bpw, TP2/DCP1, r11, no speculation |
| Hardware and path | Two RTX PRO 6000 Blackwell Max-Q GPUs; direct loopback endpoint |
| Decision boundary | Initial stop retained; diagnostic replay completed; second restoration verified; no route, alias, or promotion change |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Candidate text/image preflight | 9+1 pass | Separate corrected OCR retained |
| Candidate vision | 18/18 | Bounded image suite |
| Candidate agentic | 18/18 | Campaign-required 100% pass rate |
| Candidate context at 32K | 9/9 | Largest passing bucket below the near-limit case |
| Candidate near-limit context | 8/9 | Requested 318464; failed needle at 316249 actual prompt tokens |
| Unchanged full diagnostic replay | 18/18 | Separate full-suite history from initial pass |
| Targeted replay of original miss | 2/2 | Two isolated warm-history jobs; not pooled with the full replay |
| Candidate timing, overlap, and no-spec | Not run | No comparative performance population |

**Important caveat:** the single failed `native-needle-318464-0.900-r0`
reported `finish_reason=stop` but omitted the required visible marker. There
was no OOM or runtime error. The r11 control passed the same case. This bounded
initial stop preserves the failed evaluated configuration under this gate; it
does not identify the cause, estimate a stochastic failure rate, or reject the
model family globally. A public deterministic fixture reconstruction confirms
the test fixture, but the original wire-prompt hash was not retained; it cannot
cryptographically prove the transmitted prompt bytes.

[Evidence index](2026-10-17-glm53-nvidia-fp4-ab-evidence/README.md) ·
[sanitized baseline native results](2026-10-17-glm53-nvidia-fp4-ab-evidence/native/baseline/baseline-native-index.json) ·
[sanitized candidate native results](2026-10-17-glm53-nvidia-fp4-ab-evidence/native/candidate/candidate-native-index.json) ·
[sanitized restoration smokes](2026-10-17-glm53-nvidia-fp4-ab-evidence/native/restoration/restoration-native-index.json) ·
[sanitized diagnostic native results](2026-10-17-glm53-nvidia-fp4-ab-evidence/native/diagnostic/diagnostic-native-index.json)

## Method and bounded results

The candidate and incumbent differ in model artifact, runtime, quantization,
DCP, and speculative configuration. This campaign therefore records a
whole-recipe replacement decision, not an isolated causal comparison.

The API advertised and configured 327680 tokens with C4; this is not a native
checkpoint limit or proof of four simultaneous full windows. The fixed KV
allocation bypassed the 0.985 memory-fraction profiling path. The largest
successful candidate prompt was 316266 actual tokens. The candidate passed the
separate 32K bucket 9/9 but initially failed the 327K gate's near-limit needle
at 316249 actual tokens. An unchanged full replay then passed 18/18, including
that case, and two isolated targeted replays passed it 1/1 each. The full and
isolated jobs used different warm histories; these results are not pooled into
a global failure rate. Every follow-up matched the initial case's calibration
ratio, reconstructed fixture SHA, answer SHA, and 316249-token usage. That
matching reconstruction is not a retained wire-prompt hash. No simultaneous
long-context measurement ran.

The baseline first-pass natural-output scout completed all four unique-cache
cells at eight requests each: 8K/C1, 8K/C4, 32K/C1, and 32K/C4. The 8K and
32K labels are requested target sizes, not actual measured prompt tokens.
Median E2E was respectively 4.375 s, 8.309 s, 8.385 s, and 22.965 s; mean
completion tokens were 295.875, 226.375, 264.375, and 199.5. These are
whole-recipe, variable-output observations, not controlled-decode,
visible-only throughput, traffic-isolation, or stable-tail measurements. They
do not establish a speed winner because no candidate timing population exists.

Baseline controls also retained smoke 2/2, corrected OCR 1/1, vision 18/18,
agentic 18/18 synthetic oracle, and context 18/18 through 316266 actual prompt tokens. The
original combined preflight remains failed: nine non-OCR checks passed, while
the OCR check used the wrong fixture. The corrected separate OCR result does
not turn that original artifact into a pass. Stability r1 failed before
traffic on an identity-selector error, r2 was incomplete because a 2K
reasoning budget lacked a visible anchor, and r3 passed runtime, coverage, and
retrieval after an increased anchor budget. All stability artifacts are
diagnostic and performance-ineligible.

## Decision and restoration boundary

The frozen independent-review stop rule ended the initial candidate dispatch
after the context miss. Candidate timing, overlap, and no-speculation work
was not measured in this initial quality campaign. The recurrence condition for an MTP-off arm was not met, so
that arm did not run. The candidate was unloaded through the managed path.
At 2026-10-17T08:18:06Z, the retained exact r11 baseline was restored with
unchanged original manifests, candidate absence, direct smoke/JSON pass, router
readmission, and authenticated routed smoke/JSON pass. These are restoration
checks, not a new qualification.

No direct alias configuration changed. No serve promotion was authorized or
performed. The incumbent recommendation remains unchanged. The initial failed
327K gate remains retained; the follow-ups do not establish a cause, failure
frequency, configuration fix, qualification, or promotion claim. This is not
a global checkpoint rejection. The agentic 18/18 result is a synthetic oracle,
not a full repository coding qualification. At 2026-10-17T08:45:59Z, exact
r11 restoration again passed direct/routed smoke and JSON checks, had zero OOM
events, unchanged original configuration, router readmission, and no candidate
present. This closes restoration, without qualifying or promoting the candidate.
