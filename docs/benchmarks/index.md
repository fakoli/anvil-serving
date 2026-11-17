# Benchmarks

Anvil Serving benchmarks measure local models by useful context, quality,
and serving performance on named hardware. Every result is a dated finding
with its exact configuration, raw artifacts, failures, and caveats. A result
describes what was measured on that date; it is not live deployment status
and never promotes a model by itself.

## Where to start

<div class="av-grid">
<a class="av-card" href="results/"><span class="av-card__kicker">Explore</span><span class="av-card__title">See the numbers</span><span class="av-card__body">Every finding in one record format, with interactive throughput and time-to-first-token charts.</span><span class="av-card__go">Benchmark results →</span></a>
<a class="av-card" href="../recipes/"><span class="av-card__kicker">Run</span><span class="av-card__title">Take a measured recipe</span><span class="av-card__body">Every published recipe by hardware, with a download and the findings that measured it.</span><span class="av-card__go">Serve recipes →</span></a>
<a class="av-card" href="comparison/"><span class="av-card__kicker">Decide</span><span class="av-card__title">Compare models</span><span class="av-card__body">TTFT, throughput, context, and reasoning mode grouped by card and workload.</span><span class="av-card__go">Comparison table →</span></a>
<a class="av-card" href="voice-models/"><span class="av-card__kicker">Speech</span><span class="av-card__title">Voice model results</span><span class="av-card__body">Speech recognition, speech generation, and spoken answers, with every failed gate retained.</span><span class="av-card__go">Voice results →</span></a>
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

## Latest results

The newest findings, generated from their retained evidence at every site
build. Each card opens the dated finding, which starts with the same record
header: evidence type, decision, model, engine, quantization, hardware,
context, and concurrency. Fields the evidence does not record say so.

<!-- benchmark-records:latest:6 -->

[Explore all results](results.md){ .md-button .md-button--primary }
[Decision log](decisions.md){ .md-button }

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

- [Decision log](decisions.md) — every dated decision and control, in full.
- [Run catalog](runs.md) — every retained, decision-relevant run by hardware
  and date.
- [Findings index](../findings/README.md) — the dated reports with full
  commands, raw artifacts, and failure cases.
- [External benchmarks](../EXTERNAL-BENCHMARKS.md) — advisory priors from
  official and community sources, kept separate from local results.
- [Chronological campaign archive](../BENCHMARKS.md) — the stable-URL summary
  of historical rounds.
