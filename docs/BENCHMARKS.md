# Benchmark results

This page is the public, searchable summary of the model and end-to-end benchmarks that currently inform anvil-serving's reference deployment. It is deliberately a summary, not a generic model leaderboard: every number depends on the recorded model revision, engine, quantization, context limit, hardware, workload, and topology.

The dated [findings](findings/README.md) contain the full commands, raw artifacts, failure cases, and decision history. Results below were last updated **2026-07-08**.

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
anvil-serving benchmark --bakeoff --candidate-id C --config-id CFG \n  --notebook .anvil/benchmarks.sqlite --notebook-task fast-tier --notebook-hardware rtx4090

# render the candidate matrix + rubric + win/lose/hold determination
anvil-serving benchmark external notebook render --task fast-tier --hardware rtx4090 --baseline current
```

The rubric weights and hard gates live in
`anvil_serving/external_benchmarks/notebook.py` (pure, self-checked). Runs
are append-only; the view is latest-per-(candidate, config, task, hardware).

These rows are from the [Fast-tier LLM bakeoff](findings/2026-07-08-fast-tier-llm-bakeoff.md) and its [human-gated promotion record](findings/2026-07-08-fast-tier-promotion.md). The voice artifacts in that bakeoff measure STT, LLM, and TTS stage timing, but their STT hypothesis is empty with WER `1.0`; they are **not** semantic speech-recognition accuracy results. The displayed decode rate is derived from the recorded evidence as `output_tokens * 1000 / (e2e_ms - ttft_ms)`.

## OpenClaw interaction and voice evidence

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
