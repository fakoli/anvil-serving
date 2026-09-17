# Qwen3.5 35B-A3B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Historical RTX 5090 fast-tier candidate.
    - **Selected or best-qualified configuration:** `unsloth/Qwen3.5-35B-A3B-GGUF`
      Q4_K_M on llama.cpp CUDA server, a 65,536-token hint, thinking disabled,
      and concurrency one.
    - **Measured hardware:** One NVIDIA RTX 5090, 32 GB. The retained record
      does not establish a direct comparison with another hardware lane.
    - **Evidence:** `functional` within 64K: smoke, JSON, session, and 20/20
      shared-prefix tools pass; a 10-request 8K short probe measured 56.3 tok/s.
    - **Decision:** Historical candidate, `no-promotion`; no route or serve was
      changed by this evidence.
    - **Important limitation:** The immutable model revision and image digest
      were not retained, and the 128K needle was outside the tested 64K window.
    - **Review dates:** Evidence through 2026-07-11; dossier reviewed 2026-09-17.

### Review narrative

#### 2026-07-11 — llama.cpp extension candidate

The bakeoff extension loaded the GGUF candidate on one RTX 5090. It passed the
bounded functional checks with thinking disabled and a 64K context hint. The
result was retained as a fast-tier lead in that exercise, but it did not supply
the identity, comparison, or qualification evidence needed for promotion.

## Immutable identity

### Retained GGUF lane

- **Model and served name:** `unsloth/Qwen3.5-35B-A3B-GGUF` Q4_K_M, served as
  `qwen35-35b-a3b-q4km-gguf`.
- **Weights:** GGUF Q4_K_M.
- **Repository revision:** **Not recorded in retained evidence.**
- **Runtime image digest:** **Not recorded in retained evidence.**

## Tested hardware and topology

- **Measured:** One RTX 5090, 32 GB, with one candidate at a time.
- **Execution mode:** Direct local llama.cpp endpoint; concurrency one.
- **Protected or co-resident:** Other accelerator observations in the wider
  bakeoff were not measurements of this candidate.
- **Comparability boundary:** This historical single-card result is not a
  comparison with later RTX 5090 campaigns.

## Engine, quantization, KV, context, and concurrency recipe

### llama.cpp Q4_K_M candidate

- **Engine and image:** llama.cpp CUDA server; image digest **Not recorded in
  retained evidence**.
- **Weights and KV:** GGUF Q4_K_M; KV format **Not recorded in retained evidence**.
- **Topology:** One RTX 5090, concurrency one.
- **Contract:** 65,536-token max-model-length hint, thinking disabled; the
  endpoint did not advertise its context window.
- **Recipe:** [historical serve-recipe registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml)
  and [reproduction notes](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/reproduction.md).

## Evidence by measurement class

### Functional, context, and short throughput probe

- **Status:** `functional`; historical, incomplete qualification record.
- **Measured:** Smoke, structured JSON, session recall, and 20/20 shared-prefix
  tool calls passed. The configured 65,536-token window reached 59,053 actual
  prompt tokens with 8.1-second TTFT on the retained warm-cache row. The 8K,
  10-request probe measured 56.279 tok/s
  aggregate throughput and 178.272 ms median TTFT.
- **Limits:** The 128K needle returned HTTP 400 because it exceeded the 64K
  tested window. The short probe has ten requests and is not promotion-grade;
  the 147 tok/s server timing excerpt is a separate decode observation.
- **Evidence:** [dated bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md),
  [context artifact](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/candidate-qwen35-35b-llamacpp-q4km-64k-context.bakeoff.json),
  [short probe](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/candidate-qwen35-35b-llamacpp-throughput-8k.json), and
  [preflight transcript](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/preflight-transcripts.md).

## Decision and promotion state

### Retained historical candidate

- **Q4_K_M llama.cpp lane:** `no-promotion`; useful only as retained historical
  evidence from the bakeoff.

### Rejected, superseded, or incomplete

- **Promotion claim:** Incomplete because the retained artifact lacks a pinned
  model revision and runtime digest, plus an independent quality comparison and
  later client or routing acceptance.

## Failures and gotchas

### Evidence and interpretation limits

- **Identity gap:** Do not invent an immutable revision or runtime digest from
  the served-name label.
- **Window boundary:** The 128K preflight failure is expected for the 64K
  window and is not a model-quality failure.

### Runtime, topology, or integration limits

- **Endpoint metadata:** The endpoint did not advertise its window; the runner
  needed the explicit 65,536-token hint.
- **Timing scope:** Server `print_timing` and the 10-request aggregate probe
  use different measurements and must not be presented as one benchmark value.

## Dated run history

- 2026-07-11 — [Blackwell local-model bakeoff extension](../../findings/2026-07-10-blackwell-local-model-bakeoff.md) and its [reproduction record](../../findings/2026-07-10-blackwell-local-model-bakeoff-evidence/reproduction.md).
