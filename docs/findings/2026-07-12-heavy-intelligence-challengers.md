# Heavy intelligence challengers: Mistral Small 4 and Nemotron 3 Super

**Point-in-time record, 2026-07-12.** Two current, official Hugging Face
checkpoints were served one at a time on Fakoli Dark's RTX PRO 6000 and run
through the same Heavy correctness, context, independent-sanity, and
five-session workloads. `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` is
the selected resident experiment because it passed the complete built-in gate
and the thinking-enabled tie-break. This is not a router-profile promotion.

Raw artifacts are under
[2026-07-12-heavy-intelligence-challengers-evidence/](2026-07-12-heavy-intelligence-challengers-evidence/).

## Candidate prior and source freshness

| Candidate | Official source observed 2026-07-12 | Source age | Why it entered the local gate |
|---|---|---|---|
| `mistralai/Mistral-Small-4-119B-2603-NVFP4` | [Hugging Face model card](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603-NVFP4) | current, within 60 days | Apache-2.0, 119B total / 6.5B active, 256K advertised context, unified instruct/reasoning/coding behavior, official NVFP4 |
| `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` | [Hugging Face model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4) | release was about 123 days old, stale under the 120-day research rule; model page remained current | 120B total / 12B active hybrid model, configurable thinking, up to 1M advertised context, official NVFP4 |

The external claims above were candidate-selection priors only. They did not
affect the local pass/fail result. The imported `llmrequirements.com` database,
dated 2026-07-11, was also used as an advisory fit prior; it was not treated as
hardware-matched benchmark evidence.

## Reproducible single-card recipes

Both checkpoints used pinned cache revisions, vLLM nightly
`0.23.1rc1.dev531+ga65f93fb2`, FP8 KV cache, a 131,072-token served limit, and a
five-sequence admission cap. Prefix caching was disabled so independent-prompt
concurrency results could not be inflated by reuse.

The managed recipes default `--revision` to the tested commits. The
`vllm/vllm-openai:nightly` image tag remains mutable, so the observed engine
version is recorded evidence rather than a future engine pin. That drift risk
is another reason the selection remains experimental.

| Candidate | Pinned revision | Cached checkpoint | Engine details | KV capacity at 131K |
|---|---|---:|---|---:|
| Mistral Small 4 | `d57a94c74a961e1f9b489b8b3e792923ca29149b` | 66.0 GiB | `TRITON_MLA`, Mistral reasoning/tool parsers, text-only | 2,141,968 tokens; 16.34 full-window equivalents |
| Nemotron 3 Super | `4f0cf9daaeb7a4d5e23f80a00e7ed15f0e03caf6` | 74.8 GiB | official `super_v3` reasoning parser, `qwen3_coder` tool parser, `mamba_cache_dtype=float16`, text-only | 3,999,467 tokens; 30.51 full-window equivalents |

The vendored `super_v3` parser was taken from the
[official NVIDIA checkpoint repository](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4/blob/4f0cf9daaeb7a4d5e23f80a00e7ed15f0e03caf6/super_v3_reasoning_parser.py)
at the same pinned revision.

These capacity figures describe the engine's shared KV pool. They do not mean
that every admitted session can grow to the model-card maximum simultaneously.
Neither candidate's advertised maximum was validated: this round deliberately
held the served and correctness-tested context at 131K.

## Preflight and current Heavy eval

Nemotron passed the exact final recipe's preflight: short generation,
structured JSON, a 131K needle, and 20/20 tool calls. It then passed the built-in
context, tool, session-recall, and both deterministic intelligence checks with
zero failures. Its 131K request measured 16.76 s TTFT and 17.50 s end to end.

Mistral also passed preflight without a thinking override, including 20/20 tool
calls and the 131K needle. The first attempt with the harness's Qwen-style
`chat_template_kwargs` failed closed with HTTP 400 because Mistral tokenizers do
not accept that field. The model card instead specifies the OpenAI
`reasoning_effort` request field; the current harness does not expose it. On the
final no-prefix-cache recipe, Mistral failed both built-in intelligence checks:
the edit was not a valid minimal unified diff and the timeout recommendation did
not satisfy the deterministic fix check. A prior run passed one of two, so the
default sampling behavior was also not stable enough for selection. Its 131K
request measured 51.90 s TTFT and 52.55 s end to end.

## Independent Hugging Face sanity slices

The no-thinking comparison used the pinned ten-row `allenai/ai2_arc`
ARC-Challenge fixture already documented in the Qwen bakeoff. Mistral scored
9/10; Nemotron scored 7/10. Nemotron then scored 5/5 on the separate
thinking-enabled slice with a 1,024-token completion cap, versus the earlier
ThinkingCap Qwen result of 4/5.

| Candidate / mode | Correct visible finals | Median request latency | Wall time |
|---|---:|---:|---:|
| Mistral Small 4, default non-reasoning behavior | 9/10 | - | 1.18 s |
| Nemotron 3 Super, thinking disabled | 7/10 | - | 2.62 s |
| Nemotron 3 Super, thinking enabled | 5/5 | 4.35 s | 21.26 s |
| ThinkingCap Qwen3.6-27B FP8, thinking enabled (prior comparison) | 4/5 | 6.69 s | 33.49 s |

These are small, one-shot exact-marker sanity tests. They are useful for finding
obvious regressions and reasoning starvation, not for claiming general model
quality. The artifacts still do not retain finish reason or a distinct hidden-
reasoning token budget, so the result is not promotion evidence and does not
repair the known cross-model eval protocol gap.

## Five-session result

Each concurrency run sent five independent 8,192-token prompts. All requests
completed at both admission settings. The prompts elicited short outputs, so
aggregate output tok/s is an operational batch measure rather than a controlled
long-generation decode rate.

| Candidate | Concurrency 1: TTFT / E2E p50 | Concurrency 5: TTFT / E2E p50 | Aggregate output tok/s, 1 / 5 | Completed |
|---|---:|---:|---:|---:|
| Mistral Small 4 | 0.30 / 0.58 s | 1.85 / 2.46 s | 57.82 / 67.04 | 5/5, 5/5 |
| Nemotron 3 Super | 0.62 / 1.02 s | 2.52 / 3.63 s | 33.19 / 45.90 | 5/5, 5/5 |

Five sessions fit because vLLM schedules them against a shared KV block pool;
the five-sequence cap is admission control, not five pre-reserved maximum-length
contexts. RadixAttention or prefix caching could improve workloads with shared
prefixes, but these prompts were independent and prefix caching was deliberately
disabled for a clean comparison.

## Decision

Nemotron 3 Super replaces ThinkingCap Qwen3.6-27B as the best currently
validated Heavy experiment. Mistral is the lower-latency candidate for short
independent prompts, but its current Heavy intelligence failures and unsupported
harness reasoning control outweigh that advantage. Nemotron passed the full
built-in gate, produced the best bounded thinking result measured in this round,
and remained healthy at five concurrent sessions.

The selected direct endpoint is `nemotron3-super-120b-a12b-nvfp4` on
`http://127.0.0.1:39033/v1`. The production Heavy router profile, quality
calibration, and promotion state remain unchanged pending a separate human gate.
