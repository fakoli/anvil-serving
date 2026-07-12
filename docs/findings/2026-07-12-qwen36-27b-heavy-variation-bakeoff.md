# Qwen3.6-27B Heavy variation bakeoff

**Point-in-time record, 2026-07-12.** Three Qwen3.6-27B variants were served
one at a time on Fakoli Dark's RTX PRO 6000, validated with the same Heavy
correctness gates, and tested at one and five concurrent sessions. The selected
resident candidate is `bottlecapai/ThinkingCap-Qwen3.6-27B-FP8`, served directly
as `thinkingcap-qwen36-27b-fp8`. This is an experiment-serve selection, not a
router-profile promotion.

Raw artifacts are under
[2026-07-12-qwen36-27b-heavy-bakeoff-evidence/](2026-07-12-qwen36-27b-heavy-bakeoff-evidence/).

## Common topology and recipe

| Field | Tested value |
|---|---|
| Host / GPU | Fakoli Dark; one RTX PRO 6000 Blackwell 96 GB, sm_120 |
| Engine | vLLM nightly `0.23.1rc1.dev531+ga65f93fb2` |
| Mode | text-only; Qwen reasoning parser and `qwen3_coder` tool parser |
| MTP | self-speculative decoding, three speculative tokens |
| KV / context / admission cap | FP8 KV; 262,144 tokens; five sequences |
| Prefix cache | disabled for the MTP comparison |
| Endpoints | NVFP4 `:39027`; official FP8 `:39030`; ThinkingCap FP8 `:39031` |

The official and ThinkingCap FP8 checkpoints needed
`VLLM_USE_DEEP_GEMM=0`. Without it, this vLLM nightly correctly selected a
CUTLASS FP8 kernel for Qwen3.6 but still entered a generic DeepGEMM warmup and
crashed on an sm_120 `Unknown recipe` assertion. This was an engine warmup bug,
not an out-of-memory result.

## Heavy correctness and context

Every candidate passed short coding, structured JSON, a 131,072-token needle,
and a batch of 20/20 valid tool calls with thinking disabled. Every candidate
also passed the current built-in 131K context/tool/session/intelligence eval
with zero failures.

| Candidate | Format | 131K needle | Engine KV capacity | Current eval |
|---|---|---:|---:|---|
| `sakamakismile/Qwen3.6-27B-Text-NVFP4-MTP` | ModelOpt NVFP4 | 26.5 s | 1,940,759 tokens; 7.40 full-native-window equivalents | pass |
| `Qwen/Qwen3.6-27B-FP8` | official FP8 | 32.9 s | 1,624,994 tokens; 6.20 equivalents | pass |
| `bottlecapai/ThinkingCap-Qwen3.6-27B-FP8` | compressed-tensors FP8 | 32.3 s | 1,616,058 tokens; 6.16 equivalents | pass |

The engine-capacity figures show why five sessions fit: vLLM uses a shared KV
block pool rather than reserving five complete 262K windows up front. They do
not mean five simultaneous requests can each grow without contention to an
arbitrary length. Qwen3.6 is native at 262,144 and the model card describes a
YaRN extension to 1,010,000; that extension was not enabled or validated here.

## Independent Hugging Face sanity eval

The short independent suite is a pinned ten-row slice of
`allenai/ai2_arc`, `ARC-Challenge`, test rows 0-9. It uses exact final-answer
markers, no model judge, and thinking disabled. The fixture is
`tests/fixtures/eval-data/hf-arc-challenge-10.suite.json` in the repository.

| Candidate | ARC-Challenge result |
|---|---:|
| NVFP4 base + MTP | 9/10 |
| official FP8 base + MTP | 10/10 |
| ThinkingCap FP8 + MTP | 10/10 |

This ten-question slice is a smoke-quality discriminator, not a capability
leaderboard or promotion gate. The NVFP4 miss was the lunar-energy item.

## Five-session capacity result

Each run used five independent requests with an 8,192-token prompt, thinking
disabled, and the same benchmark prompt generator. The generator elicited very
short answers, so aggregate tok/s is retained in the raw artifacts but should
not be interpreted as a stable long-generation decode rate.

| Candidate | Concurrency 1: TTFT / E2E p50 | Concurrency 5: TTFT / E2E p50 | Completed |
|---|---:|---:|---:|
| NVFP4 base | 0.63 / 0.96 s | 3.22 / 3.75 s | 5/5, 5/5 |
| official FP8 base | 1.59 / 1.96 s | 5.68 / 6.31 s | 5/5, 5/5 |
| ThinkingCap FP8 | 1.01 / 1.33 s | 4.66 / 5.22 s | 5/5, 5/5 |

Five sessions are supported by all three recipes. The cost is queueing and
batched-prefill latency, not a hard admission failure. RadixAttention or prefix
caching can help workloads with shared prefixes, but these independent prompts
do not share a prefix and the MTP recipes deliberately disabled prefix caching.
SGLang was therefore not substituted for this comparison.

## Thinking-mode tie-break and selection

A second pinned ARC slice enabled thinking and allowed 1,024 completion tokens
per question. This is explicitly a bounded token-efficiency test: a blank
visible answer means reasoning consumed the budget before producing a final
answer, not necessarily that the model could never solve the item.

| Candidate | Visible correct finals | Median request latency | Wall time |
|---|---:|---:|---:|
| NVFP4 base + MTP | 1/5 | 9.14 s | 47.44 s |
| ThinkingCap FP8 + MTP | 4/5 | 6.69 s | 33.49 s |

ThinkingCap is the selected Qwen3.6 Heavy candidate because it retained the
10/10 no-thinking sanity score and materially reduced reasoning starvation and
task latency. NVFP4 remains the throughput/capacity winner when thinking is
disabled; official FP8 had neither NVFP4's speed nor ThinkingCap's reasoning
efficiency in this local run.

## External recipe prior

The `llmrequirements.com` database observed 2026-07-12 (database date
2026-07-11) describes Qwen3.6-27B dense as a strong local coding/reasoning fit
and estimates the single-PRO-6000 Q4 bucket at 85 decode tok/s, 4,100 prefill
tok/s, and 28 seconds TTFT at 100K. Those are coarse editorial build-bucket
estimates rather than per-checkpoint measurements. An Anvil source adapter now
imports `https://llmrequirements.com/data/db.json`, preserves its model ratings
and benchmark claims in raw metadata, and labels every row advisory-only.

## Decision boundary

The ThinkingCap serve is left healthy as the best candidate in this bounded
Qwen3.6 comparison. The production router Heavy tier, quality profile, and
promotion state remain unchanged and still require a separate human gate.
