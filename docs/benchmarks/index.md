# Benchmarks

Anvil Serving benchmarks measure local models by useful context, quality,
and serving performance on named hardware. Every result is a dated finding
with its exact configuration, raw artifacts, failures, and caveats. A result
describes what was measured on that date; it is not live deployment status
and never promotes a model by itself.

## Where to start

<div class="bench-paths" markdown="1">

<div class="bench-path" markdown="1">

**See the numbers**

[Benchmark results](results.md) puts every finding in one record format,
with interactive throughput-versus-interactivity and time-to-first-token
charts and a downloadable index.

</div>

<div class="bench-path" markdown="1">

**Run a measured configuration**

[Serve recipes](../recipes/index.md) lists every published recipe by
hardware, with a download and the findings that measured it. The
[measured recipe finder](recipe-results.md) lists strengths, limits, and
rejection reasons.

</div>

<div class="bench-path" markdown="1">

**Compare models**

The [model comparison table](comparison.md) groups TTFT, throughput,
context, and reasoning mode by card and workload. The
[selection guide](selection.md) narrows a choice for one workload, and the
[model dossiers](models/index.md) cover quality, tools, and media.

</div>

<div class="bench-path" markdown="1">

**Speech and voice**

[Voice model results](voice-models.md) cover speech recognition, speech
generation, and spoken answers, with every failed gate retained.

</div>

</div>

## Choose by hardware

| Hardware | Published evidence | Start here |
|---|---|---|
| 2× NVIDIA RTX PRO 6000 Blackwell Max-Q, 192 GB aggregate | Dual-card reference configuration: split workloads or exclusive TP=2 | [RTX PRO 6000](hardware/rtx-pro-6000.md) |
| NVIDIA GeForce RTX 5090, 32 GB | Single-card qualification configuration and historical results | [RTX 5090](hardware/rtx-5090.md) |
| Apple M4 Max, 48 GB unified memory | Same-host voice candidate evidence; no LLM promotion | [Apple M4 Max](hardware/apple-m4-max.md) |

GPU count, interconnect, operating system, and runtime are part of every
result. The dual-PRO cards communicate over PCIe without NVLink, and their
aggregate VRAM is not unified memory. Exclusive TP=2 results must show that
both devices were selected and all other inference was offline.

## Match the metric to the workload

- **Interactive use:** time to first token (TTFT) and end-to-end latency.
- **Sustained serving:** aggregate output tokens per second, TPOT/ITL, and
  tail latency at the tested concurrency.
- **Long context:** actual prompt depth reached, not the configured window.
- **Quality, tools, and media:** the declared quality workload and client
  acceptance in the model dossier, not one speed number.

A copied configuration is not a reproduced result until the same workload
and independent gates pass. The [methodology](methodology.md) defines the
comparison rules and instruments.

## Latest decisions

The newest dated entries. The [decision log](decisions.md) keeps the full
text of every earlier decision, newest first.

- **2026-11-09 — TensorFold reasoning budget:**
  [Max/Low Go controls](../findings/2026-11-09-tensorfold-reasoning-budget.md)
  and a [source and rollout follow-up](../findings/2026-11-09-tensorfold-reasoning-budget.md#source-and-rollout-follow-up-2026-11-09).
  No default, model-policy change, or promotion follows.
- **2026-11-08 — TensorFold Go validation:**
  [one selected Go task](../findings/2026-11-08-tensorfold-go-validation.md)
  passed at 32K and failed at 16K. No broad coding score or promotion.
- **2026-11-03 — RTX 5090 candidates:**
  [bounded coding and synthetic results](../findings/2026-11-03-rtx5090-campaign.md)
  with no deployment change.
- **2026-11-01 — Swift TP1 and runtime scouts:**
  [Swift TP1 qualification accepted](../findings/2026-11-01-swift-tp1-gpu0.md)
  on one RTX PRO 6000. The [cache-layout and DFlash scouts](../findings/2026-11-01-runtime-scouts.md)
  did not clear the 10% median-latency gate; the original configuration was
  restored.
- **2026-10-17 — GLM-5.3-Flash NVIDIA FP4:**
  [operator-selected promotion](../findings/2026-10-17-glm53-nvidia-fp4-promotion.md)
  with the [full serving comparison](../findings/2026-10-17-glm53-serving-performance.md).
  Historical recommendation; not a statement of today's selected checkpoint.

## How to read the evidence

Evidence labels describe what was observed:

| Label | Meaning |
|---|---|
| `external-prior` | Official or community research used to choose a recipe; not local proof. |
| `compatibility-only` | Loaded or answered bounded probes; no qualification claim. |
| `functional` | Independent behavior gates passed for the stated contract. |
| `capacity` | Context, concurrency, throughput, latency, or residency was measured. |
| `quality` | A declared quality workload was measured with retained results. |
| `historical-invalid` | Retained run is incomplete, incomparable, or missing identity needed for reuse. |

Decision labels are separate: `current`, `rollback`, `challenger`,
`no-promotion`, and `rejected`. They describe the latest published evidence
decision for a reference configuration, not live deployment state. A quality
result can still be `no-promotion`; a failed load can be `rejected` while
remaining useful compatibility evidence.

## Run and publish benchmarks

- [Context, agentic, and SWE jobs](context-agentic-swe.md) — registered-worker
  workflow, bounded profiles through 640K, and independent scoring.
- [Repeatable campaigns](repeatable-campaigns.md) — frozen plans and
  resumable multi-cell matrices.
- [Finding format](finding-format.md) — the finding layout, the benchmark
  record, result cards, and the claim ledger.
- [Engine testing](engine-testing.md) — closed engine diagnostics and their
  failure boundaries.

## Complete history

- [Decision log](decisions.md) — every earlier dated decision and control.
- [Run catalog](runs.md) — every retained, decision-relevant run by hardware
  and date.
- [Findings index](../findings/README.md) — the dated reports with full
  commands, raw artifacts, and failure cases.
- [External benchmarks](../EXTERNAL-BENCHMARKS.md) — advisory priors from
  official and community sources, kept separate from local results.
- [Chronological campaign archive](../BENCHMARKS.md) — the stable-URL summary
  of historical rounds.
