# Gemma 4 E2B W4A16

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Historical RTX 5090 Fast-lane challenger.
    - **Selected or best-qualified configuration:**
      `google/gemma-4-E2B-it-qat-w4a16-ct` with the July 15 tokenizer,
      vLLM 0.25.1, FP8 KV, 131,072 configured tokens, and thinking disabled.
    - **Measured hardware:** One NVIDIA RTX 5090, 32 GB, one LLM serve at a time.
    - **Evidence:** 30K, 60K, and 120K preflight passes; 0.43-second TTFT and
      96 aggregate tok/s at 32K/c1; 0.21 seconds and 204 aggregate tok/s at c2.
    - **Decision:** `no-promotion`; the Fast control remained selected.
    - **Important limitation:** Repeated timeout triage failed 0/3. The capacity
      rows are mixed short-generation aggregates, not controlled decode rates.
    - **Review dates:** Evidence through 2026-07-16; dossier reviewed 2026-09-17.

### Review narrative

#### 2026-07-16 — July 15 template candidate

The official E2B checkpoint and updated tokenizer fit the 32 GB Fast lane and
passed the retained context preflights. Its faster short-workload capacity did
not clear the repeated strict timeout-triage gate, so the existing E4B control
remained selected. This retained result is not a current route claim.

## Immutable identity

### Official E2B lane

- **Model:** `google/gemma-4-E2B-it-qat-w4a16-ct` at
  `93c069399e6574553f7b59cec1d1ddf1c332a916`.
- **Tokenizer:** `google/gemma-4-E2B-it` at
  `179516f0c449474fdc46f08f30ead5b11e178497`.
- **Runtime:** vLLM 0.25.1 image
  `sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089`.

## Tested hardware and topology

- **Measured:** One RTX 5090, 32 GB, in the Fast lane.
- **Execution mode:** One LLM serve on the card during measurement.
- **Comparability boundary:** The same dated finding includes a separate
  dual-card lane; its result is not part of this single-card decision.

## Engine, quantization, KV, context, and concurrency recipe

### Official July 15 template lane

- **Engine and image:** vLLM 0.25.1, digest pinned above.
- **Weights and KV:** W4A16 and FP8 KV.
- **Topology:** One RTX 5090; c1 and c2 short-workload cells.
- **Contract:** 131,072 configured tokens and thinking disabled.
- **Recipe:** [dated Fast matrix](../../findings/2026-07-16-gemma4-chat-template-bakeoff.md#fast-matrix--rtx-5090-32-gb) and
  [retained quality artifact](../../findings/2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-e2b-w4a16-128k-quality-r3.json).

## Evidence by measurement class

### Functional, context, and capacity

- **Status:** `functional` and bounded `capacity`.
- **Measured:** Preflight passed at 30K, 60K, and 120K. The 32K/c1 cell
  recorded 0.43-second TTFT and 96 aggregate tok/s; c2 recorded 0.21 seconds
  and 204 aggregate tok/s.
- **Limits:** These are fixed-context, short-generation aggregates and do not
  establish controlled decode or full-window concurrency.
- **Evidence:** [dated finding](../../findings/2026-07-16-gemma4-chat-template-bakeoff.md) and
  [120K capacity artifact](../../findings/2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-e2b-w4a16-128k-ctx128k-capacity-c1.json).

### Strict quality gate

- **Status:** bounded `quality` failure.
- **Measured:** Timeout triage passed 0/3 under the repeated strict gate.
- **Limits:** This is a narrow deterministic gate, not a general intelligence score.
- **Evidence:** [quality artifact](../../findings/2026-07-16-gemma4-chat-template-bakeoff-evidence/fast-e2b-w4a16-128k-quality-r3.json).

## Decision and promotion state

### Rejected, superseded, or incomplete

- **E2B W4A16:** `no-promotion`; the existing E4B FP8-Dynamic Fast control was
  retained because E2B did not pass the strict quality gate.

## Failures and gotchas

### Evidence and interpretation limits

- **Timeout triage:** The 0/3 result blocks Fast-lane selection despite the
  retained preflight and capacity observations.
- **Rate meaning:** Aggregate output rates from the short workload are not
  decode-only performance claims.

## Dated run history

- 2026-07-16 — [Gemma 4 July 15 chat-template bakeoff](../../findings/2026-07-16-gemma4-chat-template-bakeoff.md).
