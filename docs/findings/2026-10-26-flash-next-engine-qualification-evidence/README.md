# Flash Next engine scout evidence

**Status:** final no-promotion campaign record. Flare C1 is terminal at 0/8 performance-eligible. Exact prior evaluation routes/output caps and GLM incumbent readmission passed the retained local checks; the router runtime image is a reviewed PR #704 overlay, not the original image. Workload-manifest and run-plan evidence remain explicitly missing; the manifest is finalized.

This directory contains sanitized native-shape evidence for the Ignis, vLLM, and Flare agentic/Aider suites, all three context scouts, all three SWE aggregates with their official grader summaries, and the terminal vLLM and Flare C1 capacity artifacts. Raw reasoning, visible context answers, private endpoint/path/host values, GPU UUIDs, prompts, and task transcripts are not published. Native schemas and machine-readable counts are retained; source hashes and redaction details are in the private campaign record.


## Artifact-set contents

- [Final artifact-role ledger](artifact-manifest.json) — workload-manifest and run-plan roles are marked missing; restoration evidence is retained.
- [Source registry](source-registry.json) — no external prior was used as campaign evidence.
- [Decision summary](summary.json) · [friction log](friction-log.md) · [restoration](restoration.json) · [restoration evidence](final-restoration-evidence.json).
- [Final campaign state](campaign-state.json) · [coverage and gaps](coverage-and-gaps.md) · [publication summary](publication-summary.md).

## Campaign reports

| Lane | Agentic | Aider reports |
|---|---|---|
| Ignis | [Agentic report](ignis-agentic-campaign-report.json) | [A](ignis-aider-campaign-report-a.json) · [B](ignis-aider-campaign-report-b.json) |
| vLLM | [Agentic report](vllm-agentic-campaign-report.json) | [A](vllm-aider-campaign-report-a.json) · [B](vllm-aider-campaign-report-b.json) |
| Flare | [Agentic report](flare-agentic-campaign-report.json) | [A](flare-aider-campaign-report-a.json) · [B](flare-aider-campaign-report-b.json) |

Each of the three agentic reports contains six passed cells. Aider reports retain scored failures and ungraded budget stops separately.

## Context results

- [Ignis native context result](ignis-context-benchmark-result.json)
- [vLLM native context result](vllm-context-benchmark-result.json)
- [Flare native context result](flare-context-benchmark-result.json)

Each result retains nine samples across requested buckets 8,192, 32,768, and 114,688 tokens. Maximum actual prompt length was 114,588 tokens. Raw reasoning and visible-answer strings are redacted in these public derivatives; sample grades, counts, hashes, and schema fields remain.

## SWE results

- [Ignis native aggregate](ignis-swe-benchmark-result.json) · [official grader summary](ignis-swe-official-grader-summary.json)
- [vLLM native aggregate](vllm-swe-benchmark-result.json) · [official grader summary](vllm-swe-official-grader-summary.json)
- [Flare native aggregate](flare-swe-benchmark-result.json) · [official grader summary](flare-swe-official-grader-summary.json)

The official results are Ignis 2/5 resolved, vLLM 3/5, and Flare 0/5; Flare attempted five tasks, with only Django producing a nonempty graded submission. Empty/ungraded tasks remain distinct from graded unresolved outcomes.

## Serving C1

[Flare native capacity result](flare-serving-c1-native-capacity-result.json) and [vLLM native capacity result](vllm-serving-c1-native-capacity-result.json) preserve their native `anvil-serving.benchmark/v1` schemas. Both cells received eight HTTP responses and produced 0/8 performance-eligible requests. C4/C8 were not run.

[Flare offline reconciliation](flare-c1-offline-reconciliation.md) records the terminal eight-response attempt: five no-visible-content length stops, three strict 128-word misses, and zero performance-eligible requests. The native `native_control_mismatch` wrapper failure remains preserved. Counter/resource reconciliation is supplemental and does not establish performance or quality qualification.

## Final restoration

The restored GLM incumbent is `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`, served from its pinned image and recipe on exclusive TP2. Exact prior route/output-cap configuration revision `aadcc9ffd338c32ff44a52d45db1fbe276aeb0e6aaa643da64ad58f21d284225` is admitting at generation 38. The [final restoration receipt](final-restoration-evidence.json) binds twelve source-input hashes; [direct](glm-restoration-direct-preflight.json) and [routed](glm-restoration-routed-preflight.json) sanitized `preflight/v2` artifacts each passed five checks. One real Pi read-tool turn passed. The router runtime image is a reviewed PR #704 overlay (ID suffix DE213), not the original image. Remote-memory terminal remains unknown and `drained` is false. This does not claim whole-fleet, reboot, or fresh-host acceptance.

## Limits

The shared-router buffered-text whitespace defect creates a coding-output attribution risk; original HTTP bodies are unavailable, so historical causality is unproven. PR #704 is merged; a separate controlled capture with the fix preserved the returned code byte-for-byte, passed the repaired supplemental verifier, and passed 16/16 offline exercise tests. It does not rescore historical Aider outcomes. The separate router fix is not represented as a candidate benchmark result. There is no controlled speed result or engine ranking. The prior [GLM marker trace](../2026-10-26-marker-gpu-trace.md) retains a post-stop worker-token discrepancy with no client-visible suffix shown and no runtime fix claimed. Flare C1 is terminal at 0/8 eligible. Exact route/output-cap restoration and GLM incumbent readmission passed local direct/routed checks; the router runtime uses a reviewed PR #704 overlay, not the original image. No whole-fleet, reboot, or fresh-host acceptance is claimed. Flare SWE reached the declared, frozen 60-step ceiling on four ungraded tasks; these are step-budget outcomes, not RAM failures.
