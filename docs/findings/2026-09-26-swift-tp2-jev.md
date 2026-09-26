# Swift 1.5 Qwen3.8 Flash Next TP2: coding gate failed; Jev remains shadow-only

**Date:** 2026-09-26
**Decision:** `challenger`, `no-promotion`; current GLM recommendation and aliases are unchanged.
**Status:** Qualification failed: official SWE resolved **2/5**, below the frozen **4/5** floor. The exact GLM incumbent is restored and passed direct and authenticated routed preflights. The independent Jev audit is complete.

<!-- benchmark-result-card/v1 -->

The exact Swift 1.5 Qwen3.8 Flash Next NVFP4 candidate reached bounded TP2 functional, context, agentic, and eight-image gates on two RTX PRO 6000 Max-Q GPUs. This is not a production recommendation or a matched performance comparison.

| Setup | Recorded value |
|---|---|
| Model | `ukisai/Swift-1.5-Qwen3.8-Flash-Next-NVFP4@3ff0520224f264a2d0ac4ab56ece8f2f13aadb38` |
| Runtime | vLLM 0.30.0 image `sha256:8a69ffad015f138d7170c4ddc429e230a3bc1c1719f67e14324749df200a4b90`, engine `ced6857…` |
| Hardware / topology | 2× RTX PRO 6000 Blackwell Max-Q, exclusive TP2; GPU-resident PLE, no CPU offload |
| Configured envelope | 327,680 context and a requested 65,536-token output allowance; C1 probes |
| Decision | `no-promotion`: coding **2/5** against a **4/5** floor; aliases and configuration unchanged; exact GLM incumbent restored |

- Protocol and tools passed; native agentic passed **18/18**.
- Context probes passed **12/12** at approximately 32,658–32,668 and 256,878–256,908 prompt tokens.
- Image probes passed **12/12**; eight-image probes passed **4/4**.
- Official SWE resolved **2/5**: Sphinx and SymPy resolved; Django and Pylint failed official tests; Xarray exhausted 60 calls without a submission and counts as zero.
- These results do not show a full 65,536-token generated completion, an actual 327,680-token prompt, concurrency above C1, or performance benefit. The planned 318K-input boundary, C4 readiness, and matched performance cells were not run because coding failed.

Evidence: [public bundle](2026-09-26-swift-tp2-jev-evidence/README.md), [identity](2026-09-26-swift-tp2-jev-evidence/configuration-and-identity.json), [rank and protocol record](2026-09-26-swift-tp2-jev-evidence/r3-protocol-and-rank-placement.json), [context](2026-09-26-swift-tp2-jev-evidence/native/context-artifact.json), [agentic](2026-09-26-swift-tp2-jev-evidence/native/agentic-artifact.json), [images](2026-09-26-swift-tp2-jev-evidence/native/images.json), and [eight images](2026-09-26-swift-tp2-jev-evidence/native/eight-images.json).

SWE evidence: [native run](2026-09-26-swift-tp2-jev-evidence/native/swe-2-swe.json), [official report](2026-09-26-swift-tp2-jev-evidence/native/swe-official-aggregate-grade.json), [fixed-denominator gate](2026-09-26-swift-tp2-jev-evidence/native/swe-coding-gate.json), and [independent failure review](2026-09-26-swift-tp2-jev-evidence/native/swe-final-independent-review.json). Closure: [restoration receipt](2026-09-26-swift-tp2-jev-evidence/restoration-receipt.json) and [complete artifact manifest](2026-09-26-swift-tp2-jev-evidence/artifact-manifest.json).

## Scope and measured outcome

The candidate is the exact Swift 1.5 Flash Next family, not Swift27 or a base Qwen substitute. TP rank placement was recorded and both ranks loaded. The run used the [bounded exclusive 1 GiB campaign reserve](2026-09-26-swift-tp2-jev-evidence/native/reserve-policy-r3.json); the [r3 memory observation](2026-09-26-swift-tp2-jev-evidence/native/r3-gpu-observation.csv) left roughly 1,543 MiB free per GPU. That observation is not a general operating reserve and cannot establish production fit.

The two long-context input ranges are measured prompt-token ranges, not a maximum-context qualification. The configured 65,536 output allowance is a request setting; no probe generated that full output. All retained context and multimodal results are serial C1 observations. The eight-image result establishes four bounded requests, not an eight-image concurrency soak.

The retained [GLM r11 reference](2026-09-23-glm-dcp1-qualification.md) resolved **4/5**. Independent review verified the same five tasks, pinned agent/grader, environment, profile, sampling, and network policy for the qualification floor. GLM's explicit enabled thinking and Swift's native-default thinking remain different controls; this small comparison does not establish a causal model ranking or timing advantage.

## Failures, investigation, and evidence boundary

Two earlier startup attempts are retained as configuration and resource diagnostics: r1 stopped before rank creation at the checkpoint context ceiling; r2 created TP2 ranks and loaded weights but had insufficient KV allocation at its lower memory-utilization setting. Those failures led to r3; they are not model-quality results.

All five coding attempts and the official report were retained. The native wrapper reports `state=incomplete`, `model_failure/swe_limits_exceeded`, and `official_grader_complete=false`: the official grader classifies Xarray as an empty patch, not a completed test run. Its native summary is 2 resolved out of 4 graded; the frozen qualification denominator remains all **5 attempted tasks**, giving **2/5**, not 2/4. No retry, recovered unsubmitted patch, or threshold change was used.

Independent review of the authoritative logs confirmed a Django Unicode-display assertion failure and a Pylint malformed-regex failure: patch installation and the selected tests ran. The Django per-test report also labels `test_label_for_field` failed, while raw unittest output marks it successful; that retained discrepancy does not erase the separate Unicode assertion failure. Pylint passed 19/20 tests; its nonfatal `_distutils_hack` warning was not evidence of a harness failure.

No cancellation was used. The worker process group exited, all five Mini-SWE test containers were independently observed absent, and official grader output reported zero unstopped containers. Cancellation fixes retain native partial output and verify owned POSIX workers, but detached Mini-SWE test containers remain a separate tracked cleanup gap (`.tickets/2026-09-26-swe-detached-container-cancellation-cleanup.md` in the source repository). Worker termination alone must not be presented as verified container cleanup.

## Jev controlled-proxy comparison

The independent audit found no acceptance-grade benefit. Thirty owner-labeled packets were split into 10 tuning and 20 heldout cases. All three paths used the same requested GPT-6 Astra/high settings, one tool-free downstream turn, and retained baseline prompts and usage receipts. The heldout comparison is:

| Path | Median input tokens | Median total latency | p95 total latency | Category correct | Escalation correct | Joint correct | Critical selections preserved |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full-context proxy | 16,503 | 7.291 s | 8.897 s | 15/20 | 15/20 | 14/20 | 20/20 |
| Deterministic | 16,483.5 | 6.791 s | 9.948 s | 15/20 | 14/20 | 14/20 | 17/20 |
| Jev | 16,481.5 | 7.871 s | 11.011 s | 11/20 | 15/20 | 10/20 | 20/20 |

Jev saved 2 median downstream input tokens (0.0121%) against the stronger deterministic baseline, far below the 20% target. Its heldout selection calls added 23,196 input/output tokens; downstream input savings alone were only 288 aggregate tokens. Observed median latency increased by 1.080 seconds and category/joint correctness each fell by four cases. These are small-sample observations, not reliability estimates.

Accounting includes preprocessing latency, failed attempts, smoke calls, and fallbacks: the instrumented comparison used 150 provider attempts, including five failures with unknown usage. The known input/output total is **1,596,888 tokens**. Four supplemental live Jev calls add 2,927 tokens, giving **1,599,815 known tokens across 154 attempts**, still with those five unknowns. This counts the retained comparison/preflight requests, not development, corpus authoring, or review sessions. Complete cost is unknown; one successful Claude preflight recorded $0.003615. The earlier input-only subtotal is superseded by the separate audited accounting, not treated as total usage.

The comparison is shadow-only research. Its fixed path order and unequal cache state prevent a causal latency interpretation; shared skills context dominates the roughly 16.5K-token inputs; complete cost is unknown; and the exact final labels are not hash-bound before the 50 shadow calls. It does not establish interactive Codex savings, automatic consumption safety, or a model-quality result. The original 20-packet heldout result is not pooled with supplemental live r1/r2 diagnostic labels. The [independent audit](2026-09-26-swift-tp2-jev-evidence/pilot/independent-audit.json) retains the metric and provenance boundary.

The 20/20 critical-selection result is relative to this finite labeled set. The live r2 packet incorrectly marked an unresolved RoPE concern optional, and Jev omitted it. That concern must be pinned for qualification; the shadow proposal was never consumed. Four heldout labels also force benign situations into an incident taxonomy without a no-issue category. Original packets and labels remain unchanged so these limitations are reviewable. Measurements bind Serving `cd8b7415`; later conservative fallback and usage-accounting fixes are separately tested software changes.

The supported pilot is documented in [Jev benchmark pilot](../benchmarks/jev-pilot.md): `anvil-serving eval benchmark jev --allow-export` reads conventional non-secret configuration; `--no-jev` retains the baseline path. Software correctness, the failed process-benefit target, and Swift model qualification are separate results.

## Decision boundary

The current GLM recommendation remains in place. No router alias, route configuration, or client setting changed for this candidate. The managed incumbent was temporarily drained and unloaded for the authorized exclusive trial, then restored with unchanged router, recipe, topology, serve-manifest, and startup-unit hashes. Direct and authenticated routed preflights passed smoke, JSON, a 5/5 tool batch, streaming tools, and tool-result continuation; the primary tier is admitting requests again. This verifies current-host restoration, not a reboot or fresh rebuild. The user conditionally authorized promotion if the frozen gates passed. This recipe failed the coding floor, so that condition was not met; no promotion or provisioning preview was applied. No matched performance baseline or controlled performance population was collected, so no speed, latency, throughput, cost, or causal comparison is made here.
