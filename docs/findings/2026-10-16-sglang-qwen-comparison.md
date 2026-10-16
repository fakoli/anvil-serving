# SGLang, Qwen, and Nemotron comparison campaign

**Date:** 2026-10-16

**Scope:** Bounded direct scouts on two RTX PRO 6000 Blackwell Max-Q GPUs. This is a dated, public-safe evidence record; it does not describe current routing or placement.

**Decision:** `no-promotion`. No candidate reached the frozen coding floor, and no performance-eligible controlled comparison population exists. Final incumbent restoration passed direct and routed JSON checks with its client-facing contract and router hash unchanged.

<!-- benchmark-result-card/v1 -->
## Result card

> The SGLang GLM scout, Qwen FP4 scout, and Nemotron 3.5 Super VL scout each retain useful native evidence, but none qualifies as a replacement for the retained GLM baseline.

| Setup | Qualified value |
| --- | --- |
| Baseline | GLM-5.3-Flash TR3 EXL3 TP2; fresh preflight 6/6, Oracle 54/54, vision 18/18, context 2/2, SWE 3/5, and C1 0/4 performance-eligible |
| SGLang GLM scout | preflight 6/6 and Oracle 54/54; terminal SWE 0/5 resolved, 2/5 graded |
| Qwen FP4 scout | preflight 6/6 and Oracle 54/54; terminal SWE 2/5 resolved, 4/5 graded |
| Nemotron scout | preflight 6/6, vision 18/18, context 2/2; Oracle 48/54, C1 0/4 performance-eligible, and terminal SWE 2/5 resolved (4/5 graded) |
| Decision | `no-promotion`; no throughput ranking or graph |

**Important caveat:** native preflight, context, and vision passes are gate inputs, not replacement qualification. The SGLang and Qwen coding results miss the 4/5 frozen floor; Nemotron misses the exact 54/54 Oracle gate and has no eligible C1 capacity samples.

[Artifact manifest](2026-10-16-sglang-qwen-comparison-evidence/artifact-manifest.json) · [Evidence index](2026-10-16-sglang-qwen-comparison-evidence/README.md) · [restoration](2026-10-16-sglang-qwen-comparison-evidence/restoration.json)

## Outcome and decision

The exact incumbent remains the restoration choice. The SGLang GLM scout and Qwen FP4 scout both stopped after terminal incomplete five-case SWE runs, at 0/5 resolved (2/5 graded) and 2/5 resolved (4/5 graded), respectively. Neither met the frozen 4/5 replacement gate, so later vision, context, capacity, and stability gates remain deliberately unrun.

Nemotron's independent native checks passed preflight 6/6, vision 18/18, and context 2/2 at 235978 and 123169 measured prompt tokens. Its three Oracle rounds each passed the artifact-native 0.75 floor at 16/18, but collectively missed the campaign's exact 54/54 requirement at 48/54. Its C1 controlled-output requests were all performance-ineligible after strict canary or stream validation failure. Its terminal SWE record has five attempted tasks, four grades, and two resolved tasks: SymPy and Xarray resolved; Sphinx and Django were graded unresolved; Pylint reached the 60-call limit with an empty patch and was ungraded. C4 was therefore not run.

These are not a common performance population. No candidate has a complete passing coding gate and no controlled capacity population is eligible, so this campaign publishes no speed ordering, chart, or model-family conclusion.

## Exact configuration

The sanitized [configuration records](2026-10-16-sglang-qwen-comparison-evidence/) bind each public alias to its exact model revision, runtime image, engine, quantization, TP, configured context, and configured resource policy. The managed recipes and endpoint identities remain private. The baseline uses GLM-5.3-Flash TR3 EXL3 TP2; the SGLang scout uses the pinned GLM W4A16 NVFP4 profile; Qwen uses NVIDIA Qwen3.8-Flash-Next-FP4-Local TP1; and Nemotron uses NVIDIA Nemotron 3.5 Super VL 120B-A12B NVFP4 TP2 with SGLang 0.5.21.

## Method

The frozen campaign used 6-check native preflight, three 18-case Oracle rounds, an 18-attempt vision suite, two context probes, strict controlled-output capacity cells, and a five-instance independently pinned SWE assessment. Native request policy remains model-specific and is retained as a confounder rather than normalized into an unmeasured common policy. Coding requires at least 4/5 resolved and no worse than the fresh baseline; a finalist performance comparison additionally requires complete, correct, performance-eligible capacity evidence.

## Results

The fresh baseline is a continuity control, not a winner: preflight 6/6, Oracle 54/54, vision 18/18, context 2/2, SWE 3/5, and C1 0/4 performance-eligible. SGLang and Qwen each passed preflight and Oracle, but their terminal SWE artifacts are incomplete and below the coding gate. Nemotron's preflight, vision, and context records remain positive bounded observations, while its 48/54 Oracle result, strict C1 failure, and terminal coding result prevent advancement.

The public bundle preserves schema-native projections, including partial and failed attempts. It excludes raw trajectories, prompts, responses, reasoning, tool arguments, commands, logs, endpoints, private paths, host identity, GPU UUIDs, and container identities.

## Failures and caveats

- **Coding gate:** SGLang and Qwen did not meet 4/5; their downstream gates were deliberately stopped.
- **Nemotron Oracle and capacity:** 48/54 misses the exact campaign rule despite each artifact's native 0.75 floor; C1 had 0/4 eligible requests, so C4 is not implied.
- **Baseline capacity:** the original GLM baseline C1 native record also completed 0/4 performance-eligible requests. It is a failed control, not a performance measurement.
- **Nemotron resource observations:** 10,093 samples per GPU had a 13,721 MiB minimum free value, above the 4,096 MiB floor. The final status recorded zero OOM, an effective swap limit of zero, and 3,100 memory-max events at the 52 GiB cap; this is bounded evidence of pressure, not a claim of no pressure.
- **Source validation:** the temporary-root diagnosis and a separate split-source validation confound are retained as process evidence. They do not establish a source bug or reader-probe cause for any benchmark result.
- **Late common packet:** the artifact packet was initialized after early baseline probes. The bundle preserves that chronology rather than fabricating a pre-run record.
- **Router custody:** before model trials, the observed router changed from the starting container to a refreshed custody checkpoint with the same recorded image, mounts, and configuration; the actor and cause are unknown. The accepted refreshed checkpoint was retained through restoration. Six quiescence-time 503 events and six key-audit warnings remain recorded, so this campaign makes no uninterrupted-availability or traffic-exclusivity claim.
- **No comparative throughput:** failed or ineligible capacity artifacts cannot be ranked or graphed.

## What to test next

Only a separately authorized, newly pinned candidate configuration that first clears the frozen correctness and coding gates can enter a controlled capacity comparison. This record does not recommend a retry, route change, or promotion.

## Evidence boundary

The retained artifacts establish bounded dated outcomes for exact configurations. They do not establish a current deployment, a general model-family defect, capacity at configured maxima, four simultaneous full windows, a throughput ranking, or promotion. `promotion_authorized` is false for this evidence set.
