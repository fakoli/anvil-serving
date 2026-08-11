# Benchmarks

Measured local-model results on real Blackwell hardware: exact model revision,
engine, quantization, context, concurrency, and retained artifacts for every
number. These are local decision records, not a universal leaderboard — a
passing run never changes a serve or route without a separate human gate.

**Last evidence review: 2026-08-11.**

## Start with the numbers

The **[model comparison table](comparison.md)** puts every measured
configuration — TTFT, throughput, context, reasoning mode, and recipe link —
in one place, grouped by card. If you have one of these GPUs and want to know
what runs on it and how well, start there.

## Browse by hardware

| Hardware | Current role | Start here |
|---|---|---|
| 2× NVIDIA RTX PRO 6000 Blackwell Max-Q, 192 GB aggregate, sm_120 | Current Fakoli Dark topology; split workloads or exclusive TP=2 | [RTX PRO 6000](hardware/rtx-pro-6000.md) |
| NVIDIA GeForce RTX 5090, 32 GB, sm_120 | Historical Fakoli Dark results; card removed before the TP=2 campaign | [RTX 5090](hardware/rtx-5090.md) |

Fakoli Dark now has two equal PRO 6000 cards. Aggregate VRAM is not unified
memory, and the cards communicate over PCIe without NVLink. Exclusive TP=2
runs must prove that both devices were selected and all other inference was
offline. Fakoli Mini remains model-free in the reference topology. Historical
mixed-card tests still say which card was measured and which was merely
protected or co-resident.

## Browse by model

Each **[model dossier](models/index.md)** is a stable, model-centered summary:
current status, every measured configuration, working recipes, and links to the
dated findings behind each conclusion.

## Run context, agentic, and repository evaluations

The [context, agentic, and SWE job guide](context-agentic-swe.md) describes the
registered-worker workflow, bounded profiles through 640K, independent scoring,
pinned mini-SWE-agent and official SWE-bench grading, cancellation, and the
measured-versus-prior evidence boundary. The guide defines methodology and
commands; it does not claim a live model result by itself.

## Production aliases and recent controls

1. **DeepSeek V4 Flash 0731 r33 DSpark K5** — `current` exclusive TP=2 text
   Primary at 393,216 tokens, 16 sequences, and 4,096-token batching.
2. **Qwen3.5 122B A10B NVFP4** — retained qualified recipe, but not started or
   selected by this promotion's restoration contract.
3. **Agents-A1 official FP8 plus the Omni stack** — declared managed split
   restoration group for leaving the exclusive profile.
4. **DeepSeek r16 650K** — prior Primary and retained historical capacity
   evidence. **Laguna S 2.1 NVFP4** and **GPT-OSS Puzzle 88B** are additional
   rollback-era recipes, not this profile's immediate restoration contract.
5. **Gemma 4** and **ThinkingCap Qwen3.6 27B** — historical strict-quality
   controls, not the current rollback order.
6. **Nemotron 3.5 ASR** and **Qwen3-ASR 0.6B** — historical RTX 5090
   measurements. The RTX PRO 6000 was protected, not benchmarked.

On 2026-08-11, after a separate human gate, the r33 393K profile became
Primary. It passed exact managed routing, a direct 359,900-actual-token request,
and OpenClaw/Hermes client paths after both were aligned to 393,216 context,
32,768 output, and high reasoning. The legacy routed
nominal-320K needle was conservatively estimated above the route limit and
failed 413, so routed, OpenClaw, and Hermes >300K remain open rather than inferred from
the direct result. See the
[model dossier](models/deepseek-v4-flash.md) and
[promotion record](../findings/2026-08-11-deepseek-v4-flash-0731-r33-393k-promotion.md).

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
`no-promotion`, and `rejected`. A quality result can still be `no-promotion`;
a failed load can be `rejected` while remaining useful compatibility evidence.

Full comparison rules, instrument definitions, and artifact requirements:
[Methodology and evidence rules](methodology.md).

## Complete history

- [Run catalog](runs.md) — every retained, decision-relevant run, indexed by
  hardware and date.
- [Findings index](../findings/README.md) — the dated reports with full
  commands, raw artifacts, and failure cases.
- [External benchmarks](../EXTERNAL-BENCHMARKS.md) — advisory priors imported
  from official and community sources, kept separate from local results.
- [Chronological campaign archive](../BENCHMARKS.md) — the stable-URL summary
  of historical rounds, including Fast-tier and voice campaigns.
