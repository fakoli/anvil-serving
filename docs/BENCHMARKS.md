# Benchmark results

This page is the public, searchable summary of the model and end-to-end benchmarks that currently inform anvil-serving's reference deployment. It is deliberately a summary, not a generic model leaderboard: every number depends on the recorded model revision, engine, quantization, context limit, hardware, workload, and topology.

The dated [findings](findings/README.md) contain the full commands, raw artifacts, failure cases, and decision history. Results below were last updated **2026-07-12**.

## Read these results correctly

- Treat a row as evidence for its exact tested configuration, not for every variant of that model family.
- Compare rows only when their workload and topology are comparable. A faster inference run does not establish coding quality, tool reliability, or routing eligibility.
- Quality-profile and production changes remain human-gated. A benchmark can recommend a change; it never promotes a model by itself.
- External benchmark data is an advisory prior, not a local result. See [External benchmarks](EXTERNAL-BENCHMARKS.md) for its import and comparison workflow.

## Current Fast-tier result

The reference Fast tier on Fakoli Dark's RTX 5090 is **`nvidia/Qwen3.6-35B-A3B-NVFP4`**, served as `qwen36-35b-a3b-nvfp4` through vLLM nightly with `modelopt_fp4`, FP8 KV cache, a 32K context limit, and two sequences. It was promoted after a human gate, direct-endpoint preflight, route proof, and OpenClaw sync; it is eligible for low-latency chat and bounded edits, not newly recalibrated planning or review work.

| Candidate / tested configuration | Measured voice total / LLM stage | Loaded-endpoint TTFT / end-to-end | Approx. decode rate | Outcome |
|---|---:|---:|---:|---|
| Qwen3.6-35B-A3B, vLLM NVFP4, 32K | 377.52 ms / 165.40 ms | 1489.36 ms / 2302.37 ms | 236.16 tok/s | **Current Fast tier**; all bakeoff hard gates passed. |
| Qwen3.6-27B control, vLLM NVFP4, 32K | 1130.21 ms / 814.83 ms | 6203.94 ms / 9041.91 ms | 67.65 tok/s | Former Fast-tier control. |
| Devstral Small 2, vLLM FP8, 8K | 923.98 ms / 433.12 ms | 742.46 ms / 3755.56 ms | 57.75 tok/s | Promising coding/agent fallback, but the successful run required an 8K context limit. |
| GLM-4.7-Flash, llama.cpp `UD-Q4_K_XL`, 32K | 2376.21 ms / 961.49 ms | 6196.05 ms / 7417.46 ms | 157.20 tok/s | Tool and session checks passed, but it was not competitive for the Fast voice role. |
| Gemma-4-31B, vLLM NVFP4, 32K then 8K | — | — | — | Rejected for this RTX 5090 recipe; no viable loaded endpoint. |

### Bakeoff notebook (repeatable comparison)

The hand-assembled fast-tier report above is now repeatable. Record each
candidate run and render the comparison:

```bash
# append a bakeoff run (alongside --evidence-out)
anvil-serving eval benchmark run --bakeoff --candidate-id C --config-id CFG \n  --notebook .anvil/benchmarks.sqlite --notebook-task fast-tier --notebook-hardware rtx4090

# render the candidate matrix + rubric + win/lose/hold determination
anvil-serving eval benchmark external notebook render --task fast-tier --hardware rtx4090 --baseline current
```

The rubric weights and hard gates live in
`anvil_serving/external_benchmarks/notebook.py` (pure, self-checked). Runs
are append-only; the view is latest-per-(candidate, config, task, hardware).

Externally-authored eval suites (e.g. a session-evals `suite.json`) run through the
same deterministic check engine with `--suite-file`:

```bash
anvil-serving eval benchmark run --bakeoff --candidate-id C --config-id CFG \
  --suite-file ~/.anvil-serving/eval-data/2026-07-11-planning-regression/suite.json \
  --evidence-out evidence.json
```

The spec shape is `{suite, date, work_class, evals: [{id, prompt|messages, max_tokens?,
tools?, expect_tool?, checks?}]}`; `checks` use the deterministic text-check semantics and
`expect_tool` the tool-call validator. Per-eval results land in the evidence JSON under
`suites.<suite name>`, with failed checks recorded in the top-level `failures` list.
`--suite-file` alone runs only the external suite; add `--suite chat,tool,...` to run
built-in suites in the same evidence artifact. Malformed specs — including vacuous
checks that could never fail (typo'd assertion keys, empty needles) — are rejected
before any request is sent.

These rows are from the [Fast-tier LLM bakeoff](findings/2026-07-08-fast-tier-llm-bakeoff.md) and its [human-gated promotion record](findings/2026-07-08-fast-tier-promotion.md). The voice artifacts in that bakeoff measure STT, LLM, and TTS stage timing, but their STT hypothesis is empty with WER `1.0`; they are **not** semantic speech-recognition accuracy results. The displayed decode rate is derived from the recorded evidence as `output_tokens * 1000 / (e2e_ms - ttft_ms)`.

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
| Parakeet `tdt_ctc-110m`, existing Dark endpoint | 82.62, 89.30 ms | 0.0 | **Current default**; fastest passing configuration. |
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
