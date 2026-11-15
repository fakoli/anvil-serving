# Full qualification campaign

**Date:** 2026-10-13

**Scope:** direct qualification work on dual RTX PRO 6000 Blackwell Max-Q hardware. This report records only the retained public projection of the campaign. Private topology is omitted; the final restoration checks are bounded live evidence for the retained baseline.

**Decision:** `no-promotion`. The retained official-base GLM continuity line remains the restoration choice; no candidate met the complete fixed-five SWE floor and the required Django and Pylint sentinels.

<!-- benchmark-result-card/v1 -->
## Result card

> The campaign kept a measured GLM baseline and retained several useful compatibility and protocol observations, but none became a promoted replacement.

| Setup | Qualified value |
| --- | --- |
| Models | GLM-5.3 Flash, Qwen3.8 Coder390/Flash Next, DeepSeek V4 Flash 0731, MiMo V2.6 Flash, and an Ignis-served Qwen lane |
| Hardware | Dual RTX PRO 6000 Blackwell Max-Q; only TP1/TP2 local measurements are included |
| Measurement path | Direct, managed qualification; all results are point-in-time and public-sanitized |
| Contract | Fixed five-task SWE assessment; C1 baseline capacity cells; separate protocol, quality, and compatibility gates |
| Evidence | `functional`, `capacity`, `quality`, `compatibility-only`, and `historical-invalid` where stated in the retained native artifact |
| Decision | `no-promotion`; no route, model selection, or deployment change follows |

| Headline measurement | Local result | Conditions |
| --- | ---:|---|
| GLM baseline SWE | 2/5 resolved | complete official grading, fixed five-task denominator |
| GLM capacity prompts | 25,280.5; 25,281.125; 106,520; 216,194.67 mean prompt tokens | separate C1/C4 cells; samples are 3–8, so no p99 claim |
| Ignis terminal SWE | 2/5 resolved, 2/5 graded, 3 empty | terminal diagnostic; no floor or sentinel pass, `no-promotion` |
| Ignis recovery | 24 recovered SWE `engine_full` / all-lanes-occupied retry notices | 503 retry notices during SWE; all recovered; not quality observations or a capacity result |

**Why it matters:** the record distinguishes a usable continuity baseline from a universally better model. It also keeps parser, admission, loader, and grading failures visible instead of treating them as model scores.

**Important caveat:** the campaign did not prove a challenger at the required fixed-five threshold or full-context C4 behavior. R19 independently accepts restoration evidence for the retained baseline; it does not change the qualification decision. The routed thinking-off failure and separate high-thinking client pass remain distinct observations.

[Artifact manifest](2026-10-13-full-qualification-evidence/artifact-manifest.json) · [Evidence index](2026-10-13-full-qualification-evidence/README.md) · [Publication summary](2026-10-13-full-qualification-evidence/publication-summary.md)

## Outcome and decision

The [fixed-five table](2026-10-13-full-qualification-evidence/fixed-five-results.md) retains all thirteen attempted lanes with their full denominators. The baseline completed official grading at 2/5. Coder390 INT4 medium completed at 3/5 and its separate INT8 control at 2/5; the other retained Coder390 and mixed-GLM runs are incomplete and retain their original denominators. Qwen Flash Next QAD, DeepSeek EXL3, and Qwen EXL3 parser work contributed bounded functional, quality, or compatibility observations. They are not a common benchmark population and are not ranked against each other.

The terminal Ignis fixed-five result is 5 attempted, 2 graded, 2 resolved, and 3 empty. It fails the campaign floor; Django resolved and Pylint was ungraded with an empty patch, so the required sentinel pair is not satisfied. Twenty-four SWE `engine_full` / all-lanes-occupied 503 retry notices all recovered. They do not explain the three empty patches or the 60-step churn. The broader C8 protocol preflight separately recorded 6/13 passes and seven immediate 503 refusals, while serial C1 passed 6/6; these are not the same 503 set. JSON structured output is a separate observed boundary. The outcome remains `no-promotion`.

The source-aligned Qwen top-k-20 arm was cancelled before load or query. The parser correction is retained as a source/protocol result; it is not a performance or quality-improvement claim. The optional GLM T1 branch was cancelled. Neither cancellation is a model result.

## Exact configuration

The [exact configuration matrix](2026-10-13-full-qualification-evidence/configuration-and-identity.md) publishes model repositories/revisions, serving-image digests, engine revisions, weight/KV precision, TP, context, concurrency, sampling, and reasoning controls. Its recipe bindings retain safe public pins without publishing operator topology. Serving images are distinct from SWE grader images. The observed baseline capacity lanes used C1/C4 cells and actual prompt-token accounting; configured context is never substituted for observed prompt size.

The selected restoration direction is the last-good non-abliterated official-base GLM lineage. R19 independently accepted direct/routed/client restoration records while preserving the routed thinking-off failure separately from the high-thinking client pass. The retained [`restoration summary`](2026-10-13-full-qualification-evidence/restoration-summary-r19.md) is the public record; it does not infer parser flattening from an absent failed-request wire capture.

## Method

The campaign used a fixed five-task SWE-bench Verified diagnostic with an independently pinned grader. A result is complete only when all five tasks are submitted and officially graded; a qualifying candidate also needs at least four resolved tasks and the required Django/Pylint sentinel resolutions. Graded-only rates never replace the fixed denominator.

The capacity cells used controlled output and request canaries. The four measured prompt means are 25,280.5 (32K/C1), 25,281.125 (32K/C4), 106,520 (128K/C1), and 216,194.67 (256K/C1). Their populations are below 100, so percentile tails are deliberately not used as service claims. The [chart pack](2026-10-13-full-qualification-evidence/baseline-chart/baseline-capacity-matrix.svg) is a derived view of the retained native artifacts.

## Results

### Baseline and mixed GLM lanes

The retained GLM baseline is a continuity/contract control, not a universal-quality winner: it completed fixed-five at 2/5. The four direct capacity cells preserved complete canaries and controlled output at their observed prompt means. These cells support only their stated C1/C4, context, and sample conditions.

Mixed GLM TP2 no-spec and thinking-off fixed-five runs are incomplete at 1/5 with one and two graded submissions respectively. They are preserved as incomplete diagnostics. No mixed-quant ranking, capacity result, or restoration decision follows.

### Qwen and DeepSeek lanes

Coder390 INT4 TP1 results retain 3/5 for the completed medium lane; the distinct INT8 TP1 control reached 2/5. The initial INT4 TP1 result remains incomplete at three graded submissions. Qwen Flash Next QAD and EXL3 lanes retain separate native protocol and quality records. The Tabby schema correction reached full-13 protocol and quality-12 evidence, but it does not establish a top-k-20 improvement because that arm never ran.

DeepSeek EXL3 TP1 retained bounded functional/quality evidence. Its TP2 record is compatibility-only. An earlier invalid paired-image map remains excluded from outcome claims.

### MiMo and Ignis lanes

MiMo attempted six managed TP2/C1/32K loads, all stopped before any query under the required 4 GiB reserve policy and conversion/trust constraints. TP1 was pruned at a 95.9 GiB CPU requirement; this is policy/feasibility evidence, not a model-quality score. The corrected 2 GiB offload choice fixes a selector error but does not isolate a quantization effect.

Ignis R4 passed the retained serial C1 protocol controls and strict quality observations, while the separate C8 preflight passed 6/13 with seven immediate admission 503s and JSON_OBJECT returns `unsupported_value`. The terminal five-task result did not meet the floor. This is useful evidence about the serving contract, not a replacement decision.

## Failures and caveats

- A timeout is not a quality score; the merged timeout classification repair is source evidence, not campaign acceptance.
- A load, a health response, or a prepared control is not context, throughput, or quality qualification.
- The public staging history is part of the evidence boundary. R12, R13, and the first R16 projection are excluded because their semantic redaction was superseded. R16-r2 and R17 are retained only with their independent review disposition.
- R19 accepts restoration/client evidence separately from qualification. It retains the routed thinking-off failure and high-thinking client pass as different observations; unified evaluation remains open where it hardcodes thinking-off.

## Reflection and next actions

The [campaign reflection](2026-10-13-full-qualification-evidence/reflection.md) and [friction log](2026-10-13-full-qualification-evidence/friction-log.md) preserve the full process lessons and fix-forward dispositions. The [expert reading guide](2026-10-13-full-qualification-evidence/source-guide.md) maps the 100 public sources to practical decisions. The remaining work is bounded: address the admission, JSON structured-output, evidence-reader, host-memory, transition-audit, and routed-thinking-off gaps. None of these is permission to retry, reroute, or promote a model.

## Cleanup closure

[Terminal cleanup](2026-10-13-full-qualification-evidence/cleanup-summary.json) removed 11 exact model-cache snapshots and 106 Docker image IDs. All selected snapshots and image targets were verified absent. The retained 50 images have container, declared configuration, or child-image references; active services and declared rollback/library model assets remain protected. The exact incumbent was still running with its expected served identity and native API HTTP 200 after cleanup, and the router configuration bytes were unchanged.

Docker cache helpers reported 624,806,192,173 logical bytes removed. Native removal plans accounted for 402,592,907,264 allocated bytes across five removals. These measures are reported separately and do not establish combined filesystem free space. The first 97-image pass observed 160,793,673,728 additional available bytes, with concurrent filesystem activity explicitly limiting attribution. The reviewed native cleanup source was used without changing the globally installed CLI or applying a new engine, kernel, or sampler setting.

## Evidence boundary

The public bundle contains 100 dated public sources plus accepted, schema-preserving public-native artifacts from R1, R3, R5, R6, R10, R11, R14, R15, R16-r2, R17, R18, and R19. It contains no private paths, endpoints, identities, credentials, GPU UUIDs, or private discussion. All promotion, source, deployed, and live-verified states remain separate.
