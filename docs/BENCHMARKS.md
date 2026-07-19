# Benchmark results

> **Looking for the maintained decision view?** Start with the
> [RTX PRO 6000 benchmark guide](benchmarks/index.md), then use the
> [model recipes and gotchas](benchmarks/models.md) and
> [methodology](benchmarks/methodology.md). This page remains the chronological
> result archive, including Fast-tier, voice, and historical rounds.

This page is the public, searchable summary of the model and end-to-end benchmarks that currently inform anvil-serving's reference deployment. It is deliberately a summary, not a generic model leaderboard: every number depends on the recorded model revision, engine, quantization, context limit, hardware, workload, and topology.

The dated [findings](findings/README.md) contain the full commands, raw artifacts, failure cases, and decision history. Results below were last updated **2026-07-16**.

## Read these results correctly

- Treat a row as evidence for its exact tested configuration, not for every variant of that model family.
- Compare rows only when their workload and topology are comparable. A faster inference run does not establish coding quality, tool reliability, or routing eligibility.
- Quality-profile and production changes remain human-gated. A benchmark can recommend a change; it never promotes a model by itself.
- External benchmark data is an advisory prior, not a local result. See [External benchmarks](EXTERNAL-BENCHMARKS.md) for its import and comparison workflow.

## Current Fast-tier result

The reference Fast tier on Fakoli Dark's RTX 5090 is **`leon-se/gemma-4-E4B-it-FP8-Dynamic`**, served as `gemma4-e4b-it` with FP8 KV cache and a 32K context limit. The July 16 official-checkpoint/template rerun retained this control: it passed all repeated quality gates, while the new-template E2B, E4B, and 12B Fast candidates each failed the strict timeout-triage check with thinking disabled.

| Candidate / tested configuration | Measured voice total / LLM stage | Loaded-endpoint TTFT / end-to-end | Approx. decode rate | Outcome |
|---|---:|---:|---:|---|
| Gemma 4 E4B FP8-Dynamic control, legacy embedded template, 32K | — | 460 ms TTFT p50 at 30K, c1; 580 ms at c2 | 49 tok/s c1; 79 tok/s c2 aggregate | **Current Fast tier**; repeated chat/context/tool/session/intelligence gates passed. |
| Official Gemma 4 E2B/E4B/12B W4A16, July 15 template | — | 430 / 630 / 1430 ms TTFT p50 at 32K, c1 | 96 / 41 / 22 tok/s aggregate | Protocol and long-context gates passed; all three rejected for Fast by the strict thinking-disabled quality gate. |
| Qwen3.6-35B-A3B, vLLM NVFP4, 32K | 377.52 ms / 165.40 ms | 1489.36 ms / 2302.37 ms | 236.16 tok/s | Former Fast tier; all historical bakeoff hard gates passed. |
| Qwen3.6-27B control, vLLM NVFP4, 32K | 1130.21 ms / 814.83 ms | 6203.94 ms / 9041.91 ms | 67.65 tok/s | Former Fast-tier control. |
| Devstral Small 2, vLLM FP8, 8K | 923.98 ms / 433.12 ms | 742.46 ms / 3755.56 ms | 57.75 tok/s | Promising coding/agent fallback, but the successful run required an 8K context limit. |
| GLM-4.7-Flash, llama.cpp `UD-Q4_K_XL`, 32K | 2376.21 ms / 961.49 ms | 6196.05 ms / 7417.46 ms | 157.20 tok/s | Tool and session checks passed, but it was not competitive for the Fast voice role. |
| Gemma-4-31B, vLLM NVFP4, 32K then 8K | — | — | — | Rejected for this RTX 5090 recipe; no viable loaded endpoint. |

### Bakeoff notebook (repeatable comparison)

The hand-assembled fast-tier report above is now repeatable. Record each
candidate run and render the comparison:

```bash
# append a bakeoff run (alongside --evidence-out)
anvil-serving eval benchmark quality --candidate-id C --config-id CFG \
  --notebook .anvil/benchmarks.sqlite --notebook-task fast-tier --notebook-hardware rtx4090

# render the candidate matrix + rubric + win/lose/hold determination
anvil-serving eval benchmark external notebook render --task fast-tier --hardware rtx4090 --baseline current
```

The rubric weights and hard gates live in
`anvil_serving/external_benchmarks/notebook.py` (pure, self-checked). Runs
are append-only; the view is latest-per-(candidate, config, task, hardware).

Externally-authored eval suites (e.g. a session-evals `suite.json`) run through the
same deterministic check engine with `--suite-file`:

```bash
anvil-serving eval benchmark quality --candidate-id C --config-id CFG \
  --suite-file ~/.anvil-serving/eval-data/2026-07-11-planning-regression/suite.json \
  --evidence-out evidence.json
```

The spec shape is `{suite, date, work_class, evals: [{id, prompt|messages,
visible_answer_tokens?, reasoning_headroom_tokens?, max_tokens?, tools?,
expect_tool?, checks?}]}`. New comparisons use the two explicit allocations;
`max_tokens` is a legacy total cap and cannot be combined with them. `checks`
use deterministic case-insensitive substring or validated regular-expression semantics and
`expect_tool` the tool-call validator. Per-eval results land in the evidence JSON under
`suites.<suite name>`, with failed checks recorded in the top-level `failures` list.
`--suite-file` alone runs only the external suite; add `--suite chat,tool,...` to run
built-in suites in the same evidence artifact. Malformed specs — including vacuous
checks that could never fail (typo'd assertion keys, empty needles) — are rejected
before any request is sent.

Cross-model reasoning runs should also select the model family's actual control
(`--thinking-mode` or `--reasoning-effort`), set equal visible-answer allocations,
record explicit reasoning headroom, and use repeated attempts. Protocol-v3
artifacts retain the full visible answer, finish reason, reasoning-channel
metadata, per-attempt budgets, pass rates, and distinct classifications for
reasoning exhaustion, visible-answer exhaustion, and an ordinary wrong visible
answer. The API still enforces one combined completion cap; the allocations are
recorded intent rather than a claim of hard server-side partitioning.

These rows are from the [Fast-tier LLM bakeoff](findings/2026-07-08-fast-tier-llm-bakeoff.md) and its [human-gated promotion record](findings/2026-07-08-fast-tier-promotion.md). The voice artifacts in that bakeoff measure STT, LLM, and TTS stage timing, but their STT hypothesis is empty with WER `1.0`; they are **not** semantic speech-recognition accuracy results. The displayed decode rate is derived from the recorded evidence as `output_tokens * 1000 / (e2e_ms - ttft_ms)`.

## GPT-OSS Puzzle 88B Heavy compatibility (2026-07-18)

The current Heavy tier is **`nvidia/gpt-oss-puzzle-88B`**, served as
`gpt-oss-puzzle-88b` from an exact local Anvil vLLM image on the RTX PRO 6000.
The deployment pins checkpoint revision
`9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2`, serves a 131,072-token window
with FP8 KV cache, and uses the native Harmony template and OpenAI tool parser.
The router supplies `reasoning_effort=high` by default. Official Gemma 4 12B IT
QAT W4A16 is the immediate managed rollback. The complete reusable procedure is
the [GPT-OSS Puzzle 88B recipe](benchmarks/gpt-oss-puzzle-88b-recipe.md).

This transition is not a cross-model quality or throughput ranking. The exact
production shape passed smoke and JSON, a 120K requested needle check, 20/20
shared-prefix tool calls, the original Harmony parser regression 10/10 without a
request-level stop-token workaround, Responses API, streaming SSE, and a complete
tool-result continuation. The observed needle prompt was 99,100 tokens; the prior
exact-image qualification separately retains a 130,696-prompt-token near-limit
retrieval.

Post-promotion live measurement on the final image completed 10/10 direct Heavy
requests at concurrency one and 40/40 at concurrency eight. At 8K fixed context,
direct TTFT p50/p95 was 0.393/0.956 seconds at c1 and 0.766/1.075 seconds at c8;
E2E p50/p95 was 0.473/1.035 and 0.906/1.148 seconds. The tiny mixed completions
make their 3.85 and 17.85 aggregate tok/s capacity figures unsuitable as
controlled decode rates. The authenticated `planning` router path separately
completed 10/10 at c1 with 0.484/0.718-second TTFT p50/p95.

The repeated protocol-v3 suite passed 32K and 128K context, tool calling 3/3,
session recall 3/3, and timeout triage 3/3. Unified-diff formatting passed 2/3,
so the strict 100% quality gate failed. This is a real remaining quality caveat,
while the tool result demonstrates the intended runtime improvement over the
pre-fix image's 0/3 HTTP-500 failure. Root cause, fork/upstream relationship,
immutable revisions, router validation, commands, and raw artifacts are in the
[GPT-OSS Puzzle Heavy enablement record](findings/2026-07-18-gpt-oss-puzzle-heavy-promotion.md).

## Gemma 4 July 15 template matrix (2026-07-16)

### Current 31B optimization follow-up (2026-07-17)

The current official `google/gemma-4-31B-it-qat-w4a16-ct` checkpoint with the newly pinned Google
template ran healthily at 128K on the RTX PRO 6000 Max-Q under vLLM 0.25.1. Its warmed c1 diagnostic
decode was **62.3 tok/s** (two 512-token responses) and the 128K probe recorded **74.97 s TTFT**.
The official Q4 MTP assistant is **not compatible** with this W4A16 target: native MTP initializes,
then fails its engine profile on incompatible 6400/10752 projection dimensions. Do not deploy that
pair. The 300 W Max-Q power limit and different QAT/NVFP4 checkpoints make the approximately
46--48 s external RTX PRO 6000 NVFP4 128K TTFT reports an advisory reference, not a direct
regression comparator. Full artifacts, WSL2 scope, and failure evidence are in the
[dated optimization finding](findings/2026-07-17-gemma4-31b-optimization.md). **No Heavy
promotion changed.**

Official Gemma 4 12B IT QAT W4A16 is the immediate Heavy rollback, served as
`gemma4-12b-it-w4a16-ct` through vLLM 0.25.1 on the RTX PRO 6000 with FP8 KV,
a 256K context limit, five admitted sequences, and thinking enabled by router
default. It replaced ThinkingCap after the July 16 human-approved guarded
promotion and remained Heavy until the July 18 Puzzle compatibility transition.

| Heavy configuration | Repeated quality | 32K TTFT p50 / aggregate output | Quality context TTFT (32K / 128K / 240K) | Outcome |
|---|---|---:|---:|---|
| ThinkingCap Qwen3.6 27B FP8 control | pass | 4.84 s / 3 tok/s | 7.83 / 57.60 / 124.70 s | Valid rollback |
| Gemma 4 12B W4A16, July 15 template | **pass** | **1.52 s / 21 tok/s** | **6.96 / 44.61 / 97.33 s** | **Immediate Heavy rollback** |
| Gemma 4 26B BF16 | fail timeout triage 0/3 | 0.73 s / 36 tok/s | capacity TTFT 11.93 s at 120K, 34.07 s at 240K | Faster, strict-quality failure |
| Gemma 4 31B W4A16 | pass | 4.02 s / 7 tok/s | 15.44 / 112.30 / 248.57 s | Quality pass, materially slower |

The 12B promotion gate passed disabled-thinking smoke/JSON, a 240K needle,
20/20 tools, a separate enabled-thinking reasoning-evidence gate, router reload,
and exact post-reload identity. The first live attempt failed closed on a
256-visible-token `finish_reason=length` and automatically restored the validated
ThinkingCap rollback; the corrected 512-visible-token gate then passed without
removing any check. The Fast tier did not change. Full matrix, pinned revisions,
template hashes, failed starts, two-turn tool replay, cache cleanup, and raw
artifacts: [Gemma 4 chat-template bakeoff](findings/2026-07-16-gemma4-chat-template-bakeoff.md).

### Unsloth Gemma 4 NVFP4 follow-up (2026-07-16)

> **Concurrency-128 correction:** a later same-day vLLM 0.25.1 retest reproduced a large NVFP4
> continuous-batching gain. On the PRO 6000, 12B NVFP4 beat official QAT by 35.7% at c128/1K and
> 45.0% at c128/8K; on the 5090 with Model Runner V2 it beat QAT by 35.2% at c128/8K. The c1
> decode conclusion below remains true, but it does not describe high-concurrency serving. The
> production engine was upgraded to vLLM 0.25.1 with WSL2 pinned memory enabled; NVFP4 and V2
> remain unpromoted because the prior quality failures and V2 thinking-budget limitation remain.
> See [the c128 and WSL2 retest](findings/2026-07-16-gemma4-vllm0251-wsl2-c128.md).

The same-day Unsloth 12B, 26B-A4B, and 31B NVFP4 release was tested through
the existing vLLM 0.25.1 WSL2 recipe on both Blackwell GPUs. **No production
tier changed.** The publisher's approximately 1.5x 12B speed claim was not
reproduced locally: in matched three-attempt, 1,024-token diagnostics, NVFP4
was 7.4% slower than official QAT on the RTX 5090 and 9.3% slower on the RTX
PRO 6000.

| Candidate / tested configuration | Hardware and window | Repeated quality | Loaded capacity c1 / c2 | Equal-length diagnostic | Outcome |
|---|---|---|---:|---:|---|
| Unsloth Gemma 4 12B NVFP4 | RTX 5090, 32K | fail: timeout triage 1/3, thinking disabled | 55 / 144 tok/s at 8K fixed context | 103.82 tok/s | No Fast quality or decode-rate win |
| Unsloth Gemma 4 12B NVFP4 | RTX PRO 6000, 256K | fail: repeated tool 1/3 | 21 / 76 tok/s | 98.86 tok/s | Tool argument regression; keep official QAT Heavy |
| Unsloth Gemma 4 26B-A4B NVFP4 | RTX 5090, 32K | fail: timeout triage 1/3, thinking disabled | **121 / 233 tok/s at 8K fixed context** | **218.09 tok/s** | Fastest local Gemma variation; promotion blocked |
| Unsloth Gemma 4 26B-A4B NVFP4 | RTX PRO 6000, 256K | fail: timeout triage 1/3 | **45 / 122 tok/s** | **191.46 tok/s** | Full-window speed candidate; promotion blocked |
| Unsloth Gemma 4 31B NVFP4 | RTX PRO 6000, 256K | **pass** | 7 / 30 tok/s | 51.49 tok/s | Quality pass, materially too slow |

The 26B-A4B checkpoint is the best future speed candidate, while 31B is the
only larger checkpoint that cleared the full repeated Heavy gate. At 240K,
quality-context TTFT was 48.27 seconds for 26B-A4B and 223.32 seconds for 31B.
The Unsloth template is not byte-identical to Google's canonical July 15
template and tolerates pre-serialized string tool arguments; this is recorded
alongside the 12B tool failure. Full revisions, context matrix, functional
preflights, diagnostic caveats, runtime/kernel evidence, and raw artifacts:
[Gemma 4 Unsloth NVFP4 follow-up](findings/2026-07-16-gemma4-unsloth-nvfp4-follow-up.md).

## Blackwell candidate bakeoff (2026-07-10)

Six community-shortlisted candidates measured against the production baselines on Fakoli Dark
(RTX 5090 32 GB fast track; RTX PRO 6000 96 GB heavy track). Full narrative, failure records,
and raw evidence: [Blackwell local model bakeoff](findings/2026-07-10-blackwell-local-model-bakeoff.md).
**No production tier changed as a result of this bakeoff.**

| Candidate / tested configuration | Hardware | Context | Preflight | Tool calls | Decode rate | Long-context | Role verdict |
|---|---|---:|---|---|---:|---|---|
| MiniMax-M2.7-REAP-139B-A10B, vLLM NGC 26.04 NVFP4, 64K | PRO 6000 | 65,536 | pass (thinking disabled) | pass | 97.2 tok/s | 64K pass (TTFT 14.3 s); no 131K headroom | Best measured heavy candidate of the base round - superseded by Puzzle-75B (extension table below); not promoted (community REAP checkpoint) |
| Ornith-1.0-35B, vLLM NGC 26.04 FP8, 131K | PRO 6000 | 131,072 | pass (thinking disabled) | pass 20/20 | 29.2 tok/s | 131K pass — needle 11.9 s, fastest 131k full-prefill measured (13.1 s) | Retain as agentic/long-context specialist; not promoted |
| Nemotron-3-Nano-30B-A3B, vLLM NGC 26.04 NVFP4 + PIECEWISE graphs + nano_v3 parser, 131K | RTX 5090 | 131,072 | ALL PASS | pass | 15.0 tok/s | 131K pass (FULL graphs hang — upstream bug workaround required) | Keep experimental |
| Nemotron-3-Nano-Omni-30B, vLLM **nightly v0.23** NVFP4, 64K | RTX 5090 | 65,536 | pass in window | pass 20/20 | 27.3 tok/s | 64K pass (TTFT 3.1 s) | Keep experimental; unsupported on vLLM ≤0.19 — watch for stable release |
| Gemma-4-31B-IT NVFP4, vLLM gemma4-unified, six configs | RTX 5090 | none fit | fail (KV OOM ladder) | — | — | — | Reject under tested configuration (32 GB + WSL2 legacy runner); llama.cpp GGUF / PRO 6000 untested |
| DeepSeek-V4-Flash NVFP4, NGC + nightly attempts | PRO 6000 | not reached | — | — | — | — | Not enough evidence (engine-version reject; nightly load aborted) |

### Extension round (2026-07-11)

| Candidate / tested configuration | Hardware | Context | Preflight | Tool calls | Decode rate | MTP A/B | Role verdict |
|---|---|---:|---|---|---:|---|---|
| Nemotron-Labs-3-Puzzle-75B-A9B NVFP4, vLLM nightly, MTP n=3, 131K | PRO 6000 | 131,072 | ALL PASS | pass 20/20 | 137.0 tok/s (long-gen) | **1.50×** (91.4 → 137.0) | **Best measured candidate for the heavy role; not promoted** (official checkpoint; pin a stable engine first) |
| Qwen3.6-27B-Text-NVFP4-MTP (community), vLLM nightly, MTP n=3, 262K | PRO 6000 | **262,144 verified** | ALL PASS | pass 20/20 | 95.0 tok/s (long-gen) | 1.36× (69.9 → 95.0) | 262K big-KV experiment validated; community checkpoint; not promoted |
| Qwen3.5-35B-A3B Q4_K_M, llama.cpp, 64K | RTX 5090 | 65,536 | pass in window | pass 20/20 | ~147 tok/s decode, 178 ms TTFT | untested (draft-mtp) | Strongest fast-tier challenger (intelligence 2/2); not promoted |
| Gemma-4-E4B-it QAT UD-Q4_K_XL, llama.cpp, 64K | RTX 5090 | 65,536 | pass in window | pass 20/20 | 97.0 tok/s, 61 ms TTFT | — | Low-latency specialist; not promoted (upstream PLE gap open) |

Baselines measured in the same window: production heavy gpt-oss-120b (all gates pass, 131K,
intelligence 2/2) and production fast qwen36-35b-a3b (matches its 2026-07-08 promotion profile).

### Qwen3.6-27B Heavy variation bakeoff (2026-07-12)

Three Qwen3.6-27B checkpoints were tested on the single RTX PRO 6000 with
vLLM nightly, MTP n=3, FP8 KV, a 262K native context limit, and a five-sequence
admission cap. All three passed full preflight at 131K, the current built-in
Heavy eval, and 5/5 concurrent request completion. The independent ten-question
ARC-Challenge slice scored 9/10 for community NVFP4 and 10/10 for both FP8
variants.

ThinkingCap FP8 is the **selected resident Qwen3.6 Heavy candidate**: in a
thinking-enabled five-question tie-break it produced 4/5 correct visible finals
within a 1,024-token budget versus 1/5 for NVFP4, with 6.69 s versus 9.14 s
median latency. NVFP4 remains faster with thinking disabled (8K TTFT p50
0.63 s single / 3.22 s at concurrency five, versus ThinkingCap's 1.01 s /
4.66 s). See the
[dated finding and raw artifacts](findings/2026-07-12-qwen36-27b-heavy-variation-bakeoff.md).

This changes the recommendation within the Qwen3.6-27B comparison only. The
selected endpoint remains an unpromoted experiment serve; no production router
profile changed. The native 262K window was served and 131K was correctness-
validated. The model-card YaRN extension to 1.01M was not enabled or tested.

### Qwen3.5-122B MXFP4 follow-up (2026-07-12)

The cached `olka-fi/Qwen3.5-122B-A10B-MXFP4` checkpoint was re-served on the
single RTX PRO 6000 at 131K through vLLM's sm_120 Marlin W4A16 fallback. Full
preflight passed, but the standard 8K benchmark measured only **30.57 tok/s**
(TTFT p50 720.79 ms), below the prior local NVFP4 result of 38.8 tok/s. The new
externally-authored deterministic planning suite passed **1/5** cases. See the
[dated finding and raw artifacts](findings/2026-07-12-qwen35-122b-mxfp4-benchmark.md).

This result does not change the Heavy recommendation: Nemotron Labs 3 Puzzle
75B remains the best measured Heavy candidate, still unpromoted pending a pinned
stable engine. The Qwen MXFP4 recipe is retained only for reproducible engine and
weight comparisons; the materially different next experiment is llama.cpp with
the actual MXFP4_MOE GGUF path reported by the external single-card benchmark.

### Nemotron Puzzle deterministic-eval recheck (2026-07-12)

Nemotron Puzzle 75B was reloaded using its pinned checkpoint revision and the
same vLLM nightly image used in the extension round. Full preflight passed,
including the 128K needle and 20/20 tool calls. Its conventional 8K benchmark
reported 15.22 aggregate output tok/s and 458.93 ms TTFT p50, but the model
generated only 101 tokens across ten requests; the prior controlled 137.0 tok/s
long-generation measurement remains the useful decode result.

On the same new deterministic planning suite used for Qwen, Nemotron passed
**0/5** cases versus Qwen's 1/5. This adds no quality-promotion evidence and
does not change the recommendation: Nemotron remains the best measured Heavy
capacity candidate but stays unpromoted pending a pinned stable engine and
broader quality calibration. See the
[dated recheck and raw artifacts](findings/2026-07-12-nemotron-puzzle-recheck.md).

### GPT-OSS-120B deterministic-eval control (2026-07-12)

The production GPT-OSS-120B Heavy serve passed full preflight, including the
128K needle and 20/20 tool calls. Its conventional short 8K run measured 29.87
aggregate output tok/s and 655.67 ms TTFT p50; the established 183.2 tok/s
controlled long-generation result remains the meaningful decode baseline.

On the exact 256–384-token planning suite, GPT-OSS scored **0/5**, but four
cases returned no visible answer: native hidden reasoning consumed the entire
completion budget and ended with `finish_reason: length`. A diagnostic copy
that changed only the cap to 2,048 produced visible content for all five cases
and scored **1/5**. Therefore the exact-cap GPT-OSS score is not a valid model
quality comparison. `--suite-file` comparisons involving reasoning-channel
models need model-aware reasoning headroom or explicit reasoning-effort control
and should retain finish-reason/reasoning metadata. See the
[dated control and raw artifacts](findings/2026-07-12-gpt-oss-120b-deterministic-recheck.md).

**Historical operator verdict for these artifacts: the protocol was broken.** Do not
use the reported Qwen 1/5, Nemotron 0/5, or GPT-OSS 0/5 results for model
ranking or promotion. Protocol-v3 now adds reasoning controls, explicit
visible/reasoning allocations, finish/reasoning metadata, robust deterministic
regex checks, failure classification, and repeated runs; only new artifacts
that actually use those fields are eligible for comparison. This verdict does
not imply that deterministic checks over valid visible answers are themselves
nonfunctional.

The original built-in GPT-OSS bakeoff was rerun as a control. Its 131K context,
tool, session, and unified-diff checks passed, but the timeout-triage
intelligence case returned no visible answer after spending its full 256-token
budget in native reasoning. The older eval is therefore narrower and mostly
functional, but its intelligence score has the same missing reasoning-control
problem and is not currently stable promotion evidence.

### Heavy intelligence challengers (2026-07-12)

Two official NVFP4 checkpoints were validated one at a time on the single RTX
PRO 6000 through vLLM nightly at 131K with a five-sequence admission cap.
Mistral Small 4 119B completed 5/5 requests at concurrency five and scored 9/10
on the ARC sanity slice, but failed both built-in intelligence checks on the
final no-prefix-cache recipe. Nemotron 3 Super 120B completed 5/5, passed every
built-in Heavy check, and scored 5/5 on the thinking-enabled ARC tie-break.

Nemotron 3 Super was therefore the **best validated Heavy experiment in that round**
and the selected resident direct endpoint at capture time, superseding both Nemotron Puzzle's
capacity-only recommendation and ThinkingCap's Qwen-only selection. It is not
promoted into the production router. The short ARC slices remain sanity checks,
not general-quality or promotion evidence, and the served 131K window does not
validate Nemotron's advertised 1M maximum. See the
[dated finding and raw artifacts](findings/2026-07-12-heavy-intelligence-challengers.md).

The repaired protocol-v2 rerun strengthens that choice. Across three attempts
per item, Nemotron with 1,024 reasoning-headroom tokens scored 15/15 on the
five-item ARC sanity slice and 23/30 attempts with 8/10 stable items on a
ten-category MMLU-Pro slice. Mistral needed 2,048 headroom tokens to reach
15/15 ARC and then scored 14/30 with 5/10 stable MMLU-Pro items. Doubling
Nemotron's headroom to 2,048 did not improve its MMLU-Pro result and added 57
seconds of wall time. Poolside Laguna XS 2.1 NVFP4 was also tested through
vLLM and SGLang but rejected on this sm_120 host because neither tested recipe
produced trustworthy output. See the
[protocol-v2 finding and raw artifacts](findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md).

### Qwen3.6 protocol-v2 comparison and Unsloth NVFP4 follow-up (2026-07-12)

The same repaired repeated ARC and MMLU-Pro slices were run across the community
NVFP4+MTP checkpoint, official FP8, ThinkingCap FP8, and Unsloth's July 2026
NVFP4 checkpoint on the single RTX PRO 6000. At the matched 1,024-token
reasoning-headroom point, ThinkingCap was the strongest Qwen: 5/5 stable ARC
items and 7/10 stable MMLU-Pro items. The other three Qwen variants were
dominated by completion-budget exhaustion at that cap, so those constrained
scores are not intelligence rankings.

A model-specific headroom calibration selected 4,096 tokens for ThinkingCap.
Its three-repetition confirmation reached **9/10 stable MMLU-Pro items and
27/30 passing attempts**, while retaining its 15/15 ARC result at 1,024. This
is the highest stable quality-slice score in the current Heavy round, ahead of
Nemotron 3 Super's 8/10, but it costs materially more reasoning budget and wall
time. Nemotron remained the better matched-budget/latency result. ThinkingCap was
promoted as the routed **Heavy default** on 2026-07-12, then superseded by
Gemma 4 12B on 2026-07-16. ThinkingCap
passed a thinking-disabled functional gate (coding, JSON, 131K needle, 20/20
tools) and a separate thinking-enabled gate with 256 visible tokens plus 4,096
reasoning-headroom tokens. Both gates retained finish/reasoning evidence before
the guarded router promotion; GPT-OSS-120B was its complete rollback state.
See the [promotion finding and raw evidence](findings/2026-07-12-thinkingcap-heavy-promotion.md).

The Unsloth checkpoint used its required vLLM 0.25.0 / FlashInfer 0.6.13 /
CUTLASS DSL 4.5.2 path with native FlashInfer-CUTLASS NVFP4 and embedded MTP.
It passed full preflight and 5/5 requests at concurrency five, but needed 8,192
reasoning-headroom tokens to reach a one-pass 9/10 calibration and was slower
than ThinkingCap's 4K operating point. See the
[dated finding and raw artifacts](findings/2026-07-12-qwen36-protocol-v2-comparison.md).

Protocol-v3 external suites are fail-closed and resource-bounded: no more than
100 evals, 20 repetitions per item, 500 aggregate attempts, 65,536 completion
tokens per attempt, or 2,000,000 requested quality tokens per run. Regex checks
accept only a conservative deterministic-marker subset (literals, anchors,
boundaries, non-repeated character classes, `\s*`, and final-marker `[*]*`),
not arbitrary Python regexes.

## OpenClaw interaction and voice evidence

The focused Dark-host STT smoke benchmark used one clean 16 kHz utterance and
therefore establishes latency and basic serving correctness, not broad ASR
quality. Parakeet remained the default; Qwen3-ASR 0.6B was the only new
candidate both accurate after provider-prefix post-processing and near the
experimental warm-latency target. The tested Whisper Turbo vLLM recipes were
rejected because they repeated a hallucinated phrase. Full methodology and all
17 raw runs are in the [STT model benchmark](findings/2026-07-08-stt-model-benchmark.md).

| STT candidate / tested configuration | Warm latency | Normalized WER | Outcome |
|---|---:|---:|---|
| Parakeet `tdt_ctc-110m`, existing Dark endpoint | 82.62, 89.30 ms | 0.0 | Former default (superseded by `tdt-0.6b-v3`, 2026-07-13; numbers measured against the old model); fastest passing configuration. |
| Qwen3-ASR 0.6B, vLLM | 196.36, 278.98 ms | 0.0 after postprocess | Retain as the next candidate for a future managed evaluation on a larger corpus. |
| Qwen3-ASR 1.7B, vLLM | 290.59, 223.96 ms | 0.0 | No demonstrated advantage over 0.6B on this sample. |
| Whisper Large V3 Turbo FP8, vLLM | 730.69, 669.57 ms | 1.0 | Reject tested recipe; repeated hallucinated phrase. |
| Whisper Large V3 Turbo BF16, vLLM | 642.42, 643.63 ms | 1.0 | Reject tested recipe; bounded decode was faster but still incorrect. |

| Scenario | Scope | Measured result | Interpretation |
|---|---|---|---|
| OpenClaw COLO interaction benchmark | Mini gateway to Dark router; `chat-fast`; 10 requests | 10/10 HTTP 200; latency p50/p95 568.6 / 1259.9 ms; exact-generation throughput p50/p95 82.77 / 171.82 tok/s | Current route and interaction path was functional. The run carried a warning because it did not include `--run-generations`. |
| Optional Mini-local audio baseline | `mini-audio`; baseline Qwen3.6-27B | TTFA 611.29 ms; full turn 789.06 ms; STT / LLM / TTS 106.28 / 356.82 / 325.95 ms | Useful same-host baseline only; it is not a valid reference A/B topology. |

The interaction result is documented in the [live OpenClaw Talk validation](findings/2026-07-08-openclaw-talk-live-validation.md). The audio baseline is preserved in the [voice latency candidate matrix](findings/2026-07-08-voice-latency-candidate-matrix.md) and [final voice recommendation](findings/2026-07-08-voice-latency-final-recommendation.md). For reference OpenClaw Talk and candidate A/B testing, Fakoli Mini stays model-free: use Dark-host audio or a Mini-side proxy to Dark rather than treating the Mini-local row as a candidate comparison.

## Publish a new benchmark result

Publish every user-relevant model benchmark in the same change that records the result. This keeps the public documentation useful while preserving the evidence needed to interpret it.

1. Run the applicable correctness gate before capacity testing (`preflight` before `benchmark`; functional checks before a voice or gateway claim). Save the machine-readable artifact.
2. Add a dated narrative under `docs/findings/` and list it in [the findings index](findings/README.md). Include the tested and served model identifiers, capture date, hardware and host/topology, engine and version, quantization, context and concurrency, exact command or artifact path, metrics, gate outcomes, failures, and caveats.
3. Update this page when the result changes the current recommendation, the reference deployment, or a comparison a reader needs to make. Link the dated finding rather than duplicating raw JSON.
4. Mark external data as an advisory prior and negative or incomplete runs as such. Do not turn a capacity result into a quality claim, and do not conceal failed load, context, tool, or topology gates.
5. Do not change a router profile, a production serve, or cloud routing merely because the documentation was updated. Those changes retain their explicit human approval gates.

For the command-level workflow and artifact expectations, see [Operator playbooks](OPERATOR-PLAYBOOKS.md#playbook-d-preflight-then-benchmark). Contributors and agents must follow this publication contract; the repository guidance in `CONTRIBUTING.md` and `AGENTS.md` makes it part of every model-benchmark change.
