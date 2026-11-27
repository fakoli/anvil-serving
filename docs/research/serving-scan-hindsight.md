# LLM serving research scan: what the shared memory bank holds

**Scanned:** 2026-11-26. This is a research inventory, not a plan, a
qualification, or a design decision. It lists the LLM-serving research that had
been ingested into the operator's shared memory bank (Hindsight) by that date,
summarizes each idea, and records whether Anvil Serving, SGLang, or vLLM already
covers it.

Three limits apply to everything below:

- **Most summaries come from the retained notes, not from re-reading the papers.**
  The exceptions are S1 to S5, whose key claims were checked on 2026-11-26
  against text quoted from the papers, and the two EMNLP titles. Nothing here
  was reproduced locally.
- **The Anvil Serving column was checked against this repository** (ADRs and
  `anvil_serving/router/`) at commit `efeec13b`.
- **The SGLang and vLLM columns are from general knowledge of both projects and
  were not re-verified against current releases in this scan.** Confirm against
  the pinned upstream version before building on any of them.

Operator topology, promoted routes, and private evidence are deliberately
omitted; see [OPERATOR-PRIVACY.md](../OPERATOR-PRIVACY.md).

**Update, 2026-11-27.** Three changes merged after this scan and are not
reflected in the tables below. ADR-0034 and ADR-0039 now record that same-host
replica tiers are implemented. Usage accounting now records admission wait,
first-content time and error codes. And the gateway gained one opt-in redirect
for an unavailable alias, with an ADR-0039 amendment, so "no fallback" now has
that single named exception. [ship-log.md](ship-log.md) has the details.

## What the scan was looking for, and what was actually there

The scan asked for routing, KV cache, prefill/decode disaggregation, speculative
decoding, scheduling, model cascades, and agentic workloads. The bank is much
thinner on those than the question assumes:

| Topic | Sources ingested |
| --- | --- |
| Routing by fit or capacity across hosts | None. Only a recorded product decision against it (see [Fit against the current contract](#fit-against-the-current-contract)). |
| Prefill/decode disaggregation | None. |
| Cache-aware or session-affinity routing | None. |
| SLO-aware scheduling, queueing, batching | None. |
| Learned routers and model cascades | One architecture paper that uses cascaded tiers (S5). No router or cascade paper. |
| KV cache | One paper that touches reuse versus re-prefill (S4), plus operator notes. |
| Speculative decoding | No paper. Operator trial-method notes only. |
| Engine parallelism and kernels | Two papers (S1, S3) and two source studies (S6, S7). |
| Serving observability | One paper (S2). |
| Agentic workloads and harness design | Sixteen papers (table B). This is most of the bank. |

So the honest headline is that most of the ingested research is about agent
harnesses, not about serving. The serving-specific set is seven sources.

## A. Serving-relevant sources

| # | Source | Link | Ingested | Idea | Anvil Serving | SGLang / vLLM |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | Revisiting Pipeline Parallelism for LLM Serving (OSDI '26) | [osdi26-hwang](https://www.usenix.org/system/files/osdi26-hwang.pdf) | 2026-11-07 | Removes the idle "bubbles" that make layer-split (pipeline) parallelism slow online, using dynamic chunked-prefill sizing and delay scheduling of decode batches. Built on SGLang and tested with four PCIe-connected A100s, where the authors report it beats tensor parallelism; it argues the approach suits GPUs without NVLink. | No. Parallelism mode is engine launch configuration in a recipe or manifest, never a route input. The reference topology declares a TP=2 exclusive mode only. | The paper's changes are to SGLang; whether they are upstream was not checked. vLLM exposes a pipeline-parallel size option. |
| S2 | StriaTrace: Efficient Tracing and Diagnosis for Online LLM Inference (OSDI '26) | [osdi26-wu-haonan](https://www.usenix.org/system/files/osdi26-wu-haonan.pdf) | 2026-11-07 | Always-on CPU and GPU tracing inside the serving engine that keeps only the telemetry needed to diagnose latency anomalies. Its median per-token overhead falls from 5.2% at concurrency 8 to 0.6% at 256, so it costs most at the low concurrency a small fleet runs. | Partial. The router exports metadata-only OTLP spans for routing decisions (`anvil_serving/router/trace_export.py`) and tracks in-flight workloads. It has no engine-internal tracing and should not grow any. | Both ship OpenTelemetry request tracing. Neither is known to implement this paper's method. |
| S3 | FlashFormer: Whole-Model Kernels for Efficient Low-Batch Inference | [arXiv:2505.22758v2](https://arxiv.org/abs/2505.22758v2) | 2026-10-12 | Generates one fused forward kernel specialized to a model's dimensions, quantization, and GPU. Reports 2–32% more tokens per second than its baseline, but only for decode, at batch size one, on a single H100, with no output-agreement check. | No, and not gateway work. It would arrive as an engine behind a tier under [ADR-0010](../adr/0010-specialized-engine-tier.md). | Neither. Both use general kernels plus CUDA graphs. |
| S4 | CliffCompaction | [arXiv:2609.26779v1](https://arxiv.org/abs/2609.26779v1) | 2026-10-12 | Compacts agent context only at discrete events, by deletion and truncation rather than recursive summaries, and frames the cost as KV-cache reuse versus re-prefill. Its own stated trade-off is recall for precision: older compacted content is discarded. An earlier note that aggressive compaction lowers task success was not confirmed in the text checked. | Caller-side. The gateway only observes the effect: `cached_tokens` in `DecisionLog` and KV capacity and usage gauges (`anvil_serving/router/router_telemetry.py`, `anvil_serving/router/model_capacity.py`). | Both do prefix reuse (SGLang radix cache, vLLM automatic prefix caching), which is what makes the idea pay. Compaction itself is a harness concern. |
| S5 | An Architecture for Long-Horizon Agents: Levels, Ticks and Cascaded Intelligence | [arXiv:2609.19519v1](https://arxiv.org/abs/2609.19519v1) | 2026-10-12 | Runs a long campaign across heterogeneous model tiers with checkpoints, reviewer gates, and escalation. Evidence is one deployment (211 ticks, 47 resets, 25 escalations), not a comparison. | Excluded inside the gateway: no fallback chain and no substitution ([ADR-0039](../adr/0039-capability-meta-router.md)). A caller may still escalate between aliases, because the caller owns the alias. | Not applicable. |
| S6 | Inference-systems learning entry: SGLang and vLLM source study | Operator-private notes; no public link | 2026-10-12 | A source-traced walkthrough of both engines pinned to specific commits, ending in a proposed staged path toward a custom engine. Documentation only. | Not applicable. | It is a study of both. |
| S7 | Hugging Face Grout and NVIDIA's Rust CUDA tooling | Link not retained | 2026-10-12 | A small Rust inference testbed combining tile kernels, cuBLAS, KV-cache operations, and CUDA-graph decode replay. NVIDIA describes the tooling as early-stage research. | Not applicable. | Not applicable. |

Two further items were ingested but are leads on recipes, not serving ideas: a
review of eleven Local Inference Lab daily summaries (ingested 2026-10-15, link
not retained), and a reusable research method distilled from it. The durable
lesson recorded there is about reading benchmark claims: check for extrapolated
throughput in footnotes, buffered streaming that inflates token rates, and
changed variables that block causal attribution.

## B. Agentic-workload and harness sources

These describe how agents load a serving stack rather than how to serve. All
were ingested on 2026-10-12 as one collection, except the last two rows.

| # | Source | Link | Idea, as retained |
| --- | --- | --- | --- |
| H1 | The Complexity Trap | [arXiv:2508.21433v3](https://arxiv.org/abs/2508.21433v3) | Cheap masking or elision of old observations is worth testing against summarization. No universal context-policy winner. |
| H2 | Agentic Context Engineering (ACE) | [arXiv:2510.04618v3](https://arxiv.org/abs/2510.04618v3) | Curates lessons into context over time rather than compacting. |
| H3 | Hindsight is 20/20 | [arXiv:2512.12818v1](https://arxiv.org/abs/2512.12818v1) | Persistent conversational memory. Should be evaluated separately from task-context compaction. |
| H4 | Scaling Test-Time Compute for Agentic Coding | [arXiv:2604.16529v1](https://arxiv.org/abs/2604.16529v1) | Motivates separating execution cost from search, feedback, and judging cost. |
| H5 | Meta-Harness: End-to-End Optimization of Model Harnesses | [arXiv:2603.28052v1](https://arxiv.org/abs/2603.28052v1) | An automatic harness-editing method. |
| H6 | Harness Learning Enables Generalizable Test-Time Adaptation | [arXiv:2609.35738v1](https://arxiv.org/abs/2609.35738v1) | A second automatic harness-editing method. |
| H7 | HarnessOpt-Bench | [arXiv:2608.06301v1](https://arxiv.org/abs/2608.06301v1) | A benchmark and protocol for harness optimization. Leaves optimizer inference uncapped. |
| H8 | Rethinking the Evaluation of Harness Evolution for Agents | [arXiv:2607.12227v4](https://arxiv.org/abs/2607.12227v4) | Challenges whether harness evolution gives consistent coding gains. Excludes editing cost. |
| H9 | An Empirical Study of Harness Design for Coding Agents | [arXiv:2609.20804v1](https://arxiv.org/abs/2609.20804v1) | Supports qualifying the model and harness as a pair on representative tasks. |
| H10 | From Question Answering to Task Completion: A Survey on Agent System and Harness Design | [arXiv:2606.20683v1](https://arxiv.org/abs/2606.20683v1) | A taxonomy of six harness responsibilities. |
| H11 | Agent Evaluation Reliability | [arXiv:2610.00651v1](https://arxiv.org/abs/2610.00651v1) | Same pair-qualification conclusion as H9, from the evaluation side. |
| H12 | SkillsBench | [arXiv:2602.12670v4](https://arxiv.org/abs/2602.12670v4) | Curated skills help in aggregate; the result does not show which skills help. |
| H13 | "Anatomy" (cited as arXiv:2609.00006) | No record retrieved | A seven-subsystem harness taxonomy, referenced only in a cross-paper note. |
| H14 | ShardMemo (EMNLP Findings 2026) | [findings-emnlp.1806](https://aclanthology.org/2026.findings-emnlp.1806.pdf) (ingested 2026-11-07) | Scoping memory retrieval before search beats filtering afterward: F1 54.21 at 76 ms p95 against 51.68 at 100 ms. |
| H15 | EffectMem (EMNLP Findings 2026) | [findings-emnlp.859](https://aclanthology.org/2026.findings-emnlp.859.pdf) (ingested 2026-11-07) | Reports 73.1% task success against 57.0% for its baseline, but not at equal model or token budget. |

None of these is implemented in the gateway, and none belongs there. They bear
on Anvil Serving in one way: the pair-qualification conclusion (H9, H11) matches
the existing evidence boundary, where `eval preflight` and benchmark artifacts
qualify an endpoint and a route mapping never does.

## Operator-private notes

The bank also holds dated operator notes on speculative-decoding trial method,
KV-cache precision and persistence, and one cold-prefix latency incident. Their
evidence lives in the private operator repository and is not reproduced here.
The only public record of speculative decoding in this repository is
[ADR-0008](../adr/0008-heavy-tier-speculative-decoding.md), which enables
self-speculation on the heavy tier as engine configuration.

## Fit against the current contract

The scan's stated goal was ideas for routing across hosts by fit and capacity.
That goal conflicts with accepted decisions, and this should be settled before
any of it is built:

- [ADR-0034](../adr/0034-fleet-control-plane-and-node-runtime-classes.md) states
  that the fleet requires no cross-host scheduler.
- [ADR-0039](../adr/0039-capability-meta-router.md) states that replica
  selection never creates a second tier candidate or a cross-host scheduler.
  Capacity-aware choice exists only among qualified members of one already
  selected tier on one host
  (`anvil_serving/router/replica_scheduler.py`).
- The gateway has no classifier, fallback chain, or model substitution. The
  caller owns the alias. (True at the time of the scan; see the update note at
  the top for the one redirect added since.)

Sorted by that line:

| Idea | Where it could live | Needs a new ADR? |
| --- | --- | --- |
| Routing by fit | Caller-side lane classification outside the gateway, already drafted as [ADR-0037](../adr/0037-caller-side-prompt-lane-classification.md) (Proposed) | No; that ADR needs a decision |
| Routing by capacity, caller-chosen | Callers read `GET /v1/models/capacity` and pick an alias themselves | No |
| Routing by capacity, gateway-chosen across hosts | Nowhere today | Yes; it reverses ADR-0034 and ADR-0039 |
| Cache-reuse guidance for agent compaction (S4) | Harness side, using `cached_tokens` the gateway already records | No |
| Low-concurrency tracing-overhead measurement (S2) | A benchmark finding, not a feature | No |
| Gateway-side cascade or escalation (S5) | Nowhere today | Yes; it reverses ADR-0039 |
| Pipeline parallelism (S1), fused kernels (S3) | Engine configuration behind a tier | No, but each needs its own qualification |

## Gaps worth filling before the next scan

No source in the bank covers prefill/decode disaggregation, cache-aware
routing, SLO-aware scheduling, or learned routing. If cross-host routing is
still the goal, those are the literatures to ingest first, since the current
bank cannot support a build decision on any of them.
