# Benchmark portal

Use this page to enter the evidence by **measured hardware**, then follow the
model dossier and dated finding for the exact recipe and retained artifacts.
These are local decision records, not a universal leaderboard. A configuration
or passing run never changes a serve or route without a separate human gate.

**Last evidence review: 2026-07-29.**

> **Looking for the numbers?** The
> [model comparison table](comparison.md) puts every measured configuration —
> TTFT, throughput, context, reasoning mode, and recipe link — in one place,
> grouped by card.

## Choose the measured hardware

| Hardware | Current role | Start here |
|---|---|---|
| NVIDIA RTX PRO 6000 Blackwell Max-Q, 96 GB, sm_120 | Primary LLM evaluation and serving | [RTX PRO 6000](hardware/rtx-pro-6000.md) |
| NVIDIA GeForce RTX 5090, 32 GB, sm_120 | Omni/vision and co-resident STT/TTS evaluation | [RTX 5090](hardware/rtx-5090.md) |

Both cards are installed in Fakoli Dark. Fakoli Mini is model-free in the
reference topology. Cross-card tests must say which card was measured and which
was merely protected or co-resident.

## Current decision chain

1. **Agents-A1 official FP8** — `current` multimodal Primary with thinking
   disabled.
2. **Qwen3.5 122B A10B NVFP4** — immediate managed `rollback`.
3. **Laguna S 2.1 NVFP4** — additional managed `rollback`.
4. **GPT-OSS Puzzle 88B** — additional pinned `rollback`; repeated strict
   unified-diff formatting passed only 2/3.
5. **Gemma 4** and **ThinkingCap Qwen3.6 27B** — historical strict-quality
   controls, not the current rollback order.
6. **Nemotron 3.5 ASR** and **Qwen3-ASR 0.6B** — measured on the RTX 5090.
   The RTX PRO 6000 was protected, not benchmarked.

See the [run catalog](runs.md) for the complete indexed history and the
[model dossiers](models/index.md) for stable, model-centered summaries.

## Evidence status

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

## Other views

- [Model dossiers](models/index.md)
- [Run catalog](runs.md)
- [RTX PRO 6000 mention audit](rtx-pro-6000-audit.md)
- [Methodology and comparison rules](methodology.md)
- [Chronological campaign archive](../BENCHMARKS.md)
- [Complete findings index](../findings/README.md)
- [GPT-OSS Puzzle operator recipe](gpt-oss-puzzle-88b-recipe.md)
