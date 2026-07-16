# RTX PRO 6000 benchmark guide

This is the maintained front page for local-model results on one **NVIDIA RTX
PRO 6000 Blackwell 96 GB (sm_120)**. It answers three practical questions:

1. Which tested configuration best fits a Heavy workload today?
2. What latency, concurrency, context, and quality trade-offs did we measure?
3. Which recipe and failure notes should an operator read before loading it?

The tables are a decision aid, not a universal leaderboard. Every result belongs
to its recorded checkpoint revision, engine build, quantization, KV-cache format,
context limit, request shape, and evaluation protocol. Follow a model name to the
[recipe and gotcha guide](models.md), or follow an evidence link to the dated raw
record. The chronological [result archive](../BENCHMARKS.md) also covers RTX 5090,
voice, and earlier rounds.

**Last evidence refresh: 2026-07-16.** No result on this page automatically
changes a router profile or production deployment; promotion remains human-gated.

## Current decision snapshot

| Need | Best measured fit | Why | Important limit |
|---|---|---|---|
| Current Heavy quality and long context | [Gemma 4 12B IT QAT W4A16](models.md#gemma-4-12b-it-qat-w4a16) | Passed repeated protocol-v3 quality, 240K context, 20/20 tools, and guarded live promotion; faster than the prior control at all quality-context targets | FP8 KV and several-minute cold compile; exact pinned July 15 tokenizer required |
| Historical tuned Qwen quality | [ThinkingCap Qwen3.6-27B FP8](models.md#thinkingcap-qwen36-27b-fp8) | 9/10 stable MMLU-Pro items at 4K reasoning headroom in the July 12 slice | Now the immediate rollback; MTP disabled in rollback recipe |
| Heavy quality with a tight 1K reasoning budget | [Nemotron 3 Super 120B NVFP4](models.md#nemotron-3-super-120b-nvfp4) | 8/10 stable MMLU-Pro and 5/5 stable ARC at 1K; strong latency/quality balance | Served and tested at 131K, not its advertised 1M maximum |
| Qwen throughput and large validated context | [Qwen3.6-27B community NVFP4 + MTP](models.md#qwen36-27b-community-nvfp4-mtp) | Fastest short-request Qwen variant tested here; 262K needle and five sessions passed | Needed 8K reasoning headroom for its best quality result |
| Controlled long-generation throughput | [GPT-OSS-120B](models.md#gpt-oss-120b) | Established 183.2 tok/s control | No valid comparison-grade protocol-v3 quality result yet |
| Low short-request latency | [Mistral Small 4 119B NVFP4](models.md#mistral-small-4-119b-nvfp4) | 0.30 s TTFT at concurrency 1 and 1.85 s at concurrency 5 | Only 5/10 stable MMLU-Pro items at its tuned 2K point |

The current routed **Heavy tier** is **Gemma 4 12B IT QAT W4A16** at
`http://127.0.0.1:30002/v1`, served as `gemma4-12b-it-w4a16-ct` with a 256K
window. ThinkingCap is the immediate managed rollback. The promotion and rollback
were both exercised through the guarded transaction; see the
[July 16 finding](../findings/2026-07-16-gemma4-chat-template-bakeoff.md).

### Current protocol-v3 promotion comparison

| Candidate | Repeated built-in quality | 32K quality TTFT | 128K quality TTFT | 240K quality TTFT | Decision |
|---|---|---:|---:|---:|---|
| ThinkingCap Qwen3.6-27B FP8 | pass | 7.83 s | 57.60 s | 124.70 s | Rollback control |
| Gemma 4 12B W4A16 | **pass** | **6.96 s** | **44.61 s** | **97.33 s** | **Promoted Heavy** |
| Gemma 4 26B BF16 | fail timeout triage 0/3 | — | — | — | Rejected despite capacity speed |
| Gemma 4 31B W4A16 | pass | 15.44 s | 112.30 s | 248.57 s | Rejected for latency |

## Bake-off: repeated quality

These small slices are useful for detecting reasoning starvation and recipe
regressions. They are not claims of general model intelligence. Every run used
a separate 256-token visible-answer allocation; the listed reasoning headroom
was added to it. For example, 4,096 headroom meant a 4,352-token completion cap.

### Matched 1,024-headroom repeated baseline

Each item in this table was attempted three times. “Stable” means all three
visible finals were correct. Wall time belongs to that exact suite and budget.

| Candidate | ARC-Challenge stable / attempts / wall | MMLU-Pro stable / attempts / wall | Interpretation |
|---|---:|---:|---|
| ThinkingCap Qwen3.6-27B FP8 | **5/5; 15/15; 104.36 s** | 7/10; 21/30; 300.24 s | Strongest Qwen at the matched budget |
| Nemotron 3 Super 120B NVFP4 | **5/5; 15/15; 69.91 s** | **8/10; 23/30; 260.91 s** | Best matched-budget quality and latency |
| Qwen3.6-27B community NVFP4 + MTP | 3/5; 9/15; 154.17 s | 0/10; 0/30; 350.33 s | MMLU dominated by budget exhaustion |
| Qwen3.6-27B official FP8 | 2/5; 6/15; 195.05 s | 1/10; 3/30; 419.18 s | MMLU dominated by budget exhaustion |
| Unsloth Qwen3.6-27B NVFP4 | 4/5; 12/15; 152.63 s | 1/10; 3/30; 418.81 s | MMLU dominated by budget exhaustion |

### Selected model-specific calibrations

These rows deliberately use different budgets. Repetition and wall time are
shown explicitly, so one-pass calibration is not ranked as stable evidence.

| Candidate | Headroom | ARC result | MMLU-Pro result / wall | Evidence strength |
|---|---:|---:|---:|---|
| ThinkingCap Qwen3.6-27B FP8 | 4,096 | 5/5 stable at the separate 1K run | **9/10 stable; 27/30; 458.78 s** | Repeated confirmation |
| Nemotron 3 Super 120B NVFP4 | 1,024 | 5/5 stable; 15/15; 69.91 s | 8/10 stable; 23/30; 260.91 s | Repeated confirmation |
| Mistral Small 4 119B NVFP4 | 2,048 | 5/5 stable; 15/15; 97.46 s | 5/10 stable; 14/30; 384.74 s | Repeated confirmation |
| Qwen3.6-27B community NVFP4 + MTP | 8,192 | 5/5 one pass | 8/10 one pass; 2 truncated; 310.20 s | Calibration only |
| Qwen3.6-27B official FP8 | 4,096 | 5/5 one pass | 8/10 one pass; 2 truncated; 278.64 s | Calibration only |
| Unsloth Qwen3.6-27B NVFP4 | 8,192 | Not rerun; 4/5 stable at 1K | 9/10 one pass; 1 truncated; 371.61 s | Calibration only |

See the [protocol-v2 Qwen comparison and raw artifacts](../findings/2026-07-12-qwen36-protocol-v2-comparison.md)
and the [cross-family protocol-v2 finding](../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md).

## Bake-off: five independent sessions

Each run used independent 8,192-token prompts with prefix caching disabled.
All candidates completed five of five requests at concurrency five. Aggregate
output tok/s here is a **short-output batch-capacity measure**, not a controlled
decode rate.

| Candidate | C1 TTFT p50 | C1 E2E p50 | C5 TTFT p50 | C5 E2E p50 | Aggregate output tok/s C1 / C5 |
|---|---:|---:|---:|---:|---:|
| Mistral Small 4 119B NVFP4 | **0.30 s** | **0.58 s** | **1.85 s** | **2.46 s** | **57.82 / 67.04** |
| Nemotron 3 Super 120B NVFP4 | 0.62 s | 1.02 s | 2.52 s | 3.63 s | 33.19 / 45.90 |
| Qwen3.6-27B community NVFP4 + MTP | 0.63 s | 0.96 s | 3.22 s | 3.75 s | 10.81 / 15.74 |
| Unsloth Qwen3.6-27B NVFP4 | 0.97 s | 1.33 s | 3.68 s | 4.21 s | 10.50 / 15.21 |
| ThinkingCap Qwen3.6-27B FP8 | 1.01 s | 1.33 s | 4.66 s | 5.22 s | 6.66 / 7.92 |
| Qwen3.6-27B official FP8 | 1.59 s | 1.96 s | 5.68 s | 6.31 s | 5.63 / 8.31 |

Five sessions fit because the engine schedules requests against a shared KV
block pool. `max_num_seqs=5` is admission control; it does not reserve five
maximum-length contexts. RadixAttention or prefix caching may help prompts with
a shared prefix, but it cannot create KV memory or make a million simultaneous
full-window sessions fit. This comparison deliberately removed prefix reuse.

Evidence and raw-artifact links: [Qwen variation bake-off](../findings/2026-07-12-qwen36-27b-heavy-variation-bakeoff.md),
[Qwen and Unsloth protocol-v2 comparison](../findings/2026-07-12-qwen36-protocol-v2-comparison.md),
and [Mistral/Nemotron challengers](../findings/2026-07-12-heavy-intelligence-challengers.md).

## Bake-off: controlled generation

Long-generation runs are the appropriate decode comparison because short
answers make setup and scheduling dominate the apparent token rate.

| Candidate / exact path | Served context | Controlled decode | Speculative-decoding gain | Evidence status |
|---|---:|---:|---:|---|
| GPT-OSS-120B production control | 131K | **183.2 tok/s** | — | Established local control |
| Nemotron 3 Puzzle 75B NVFP4, MTP 3 | 131K | 137.0 tok/s | **1.50×**, 91.4 → 137.0 | Reproducible historical candidate |
| Qwen3.6-27B community NVFP4, MTP 3 | **262K** | 95.0 tok/s | 1.36×, 69.9 → 95.0 | Reproducible historical candidate |

Do not compare these figures with the aggregate output tok/s in the concurrency
table. The [methodology guide](methodology.md) defines both metrics and the
minimum evidence needed to publish them.

## Context and prefill view

| Candidate | Advertised context | Served limit | Long-context validation | Long-context TTFT | What is actually established |
|---|---:|---:|---|---:|---|
| Gemma 4 12B IT QAT W4A16 | 262,144 | 262,144 | 240K promotion needle passed | 97.33 s at 240K in quality run | Current Heavy; exact model/tokenizer revisions pinned |
| Qwen3.6-27B community NVFP4 + MTP | 262,144 | 262,144 | 262K needle passed | 26.5 s at 131K | Native window validated; five full windows are not reserved |
| Qwen3.6-27B official FP8 | 262,144 | 262,144 | 131K preflight passed | 32.9 s at 131K | 131K functional; 262K not retained in this round |
| ThinkingCap Qwen3.6-27B FP8 | 262,144 | 262,144 | 131K preflight passed | 32.3 s at 131K | 131K functional; 262K not retained in this round |
| Mistral Small 4 119B NVFP4 | 256K | 131,072 | 131K needle passed | 51.90 s | 131K tested, not 256K |
| Nemotron 3 Super 120B NVFP4 | 1M advertised | 131,072 | 131K needle passed | **16.76 s** | 131K tested, **not 1M** |

Long-context needle TTFT is a practical prefill-latency proxy, not a standalone
prefill-throughput measurement. Prompt-token counts and isolated prefill rates
were not retained consistently enough to publish a comparable prefill tok/s
column. Future runs should record both; missing data stays missing rather than
being inferred.

## What to read next

- [Model recipes and gotchas](models.md) — exact managed services, context and
  reasoning settings, engine-specific failures, and when to choose each model.
- [Methodology and evidence rules](methodology.md) — metric definitions,
  comparability labels, test pipeline, publication schema, and contribution flow.
- [Chronological benchmark archive](../BENCHMARKS.md) — Fast tier, voice,
  historical candidates, and decision history.
- [Dated findings index](../findings/README.md) — narratives and linked raw JSON.
- [External benchmark workflow](../EXTERNAL-BENCHMARKS.md) — advisory imports;
  external data never becomes a local result.
