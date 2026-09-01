# Ornith 1.0 35B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Historical agentic-coding specialist lead.
    - **Selected or best-qualified configuration:** NGC vLLM 0.19 with
      compressed-tensors FP8 weights, FP8 KV cache, 131,072 served tokens,
      Qwen reasoning and tool parsers, and thinking disabled.
    - **Measured hardware:** One RTX PRO 6000 on Fakoli Dark.
    - **Evidence:** Functional, capacity, and bounded quality evidence includes
      a 131K needle pass, 20/20 tool calls, a session pass, intelligence 1/2,
      and 29.2 tok/s.
    - **Decision:** `no-promotion`; retain as historical specialist evidence.
    - **Important limitation:** The checkpoint commit, executed image digest,
      and complete source recipe were not retained, so the run is
      `historical-invalid` for exact reproduction.
    - **Review dates:** Retained evidence cutoff: 2026-07-10. Dossier-format
      review: 2026-08-31.

### Review narrative

#### 2026-07-10 — Specialist lead retained without promotion

Ornith passed the long-context needle and tool/session checks on one RTX PRO
6000, but its bounded intelligence slice passed only one of two prompts. The
result remains useful as an agentic-coding specialist lead rather than a
promotion result. Default thinking also exhausted small output budgets, so the
tested lane disabled thinking. Vendor quality claims were not locally verified
and played no role in the decision.

## Immutable identity

### Model

- Repository: `deepreinforce-ai/Ornith-1.0-35B-FP8`
- Checkpoint commit: **Not retained.**

### Runtime

- Engine release: NGC vLLM 0.19.
- Executed image digest and engine commit: **Not retained.**

The missing checkpoint and runtime pins make the exact-rerun identity
`historical-invalid`.

## Tested hardware and topology

### Measured lane

- Host label: Fakoli Dark.
- Hardware: one RTX PRO 6000.
- Topology: single-card candidate serve.

No second hardware lane was tested.

## Engine, quantization, KV, context, and concurrency recipe

### Retained run settings

- Engine: NGC vLLM 0.19.
- Weight format: compressed-tensors FP8.
- KV cache: FP8.
- Served context: 131,072 tokens.
- Parsers: Qwen reasoning and tool parsers.
- Thinking: disabled.
- Concurrency: **Not recorded in the dossier evidence.**

### Reconstruction boundary

The tracked
[experiment Compose file](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml)
is the closest public reconstruction aid. The dated artifact did not retain a
source recipe or launch command, so the Compose service must not be presented
as proof of the exact command that produced the measurement.

## Evidence by measurement class

### Functional and capacity

- 131K needle: pass.
- Tool calls: 20/20.
- Session gate: pass.

### Bounded quality and performance

- Intelligence slice: 1/2.
- Decode performance: 29.2 tok/s.

### Evidence boundary

The result carries `historical-invalid` identity because the immutable model
and runtime pins are incomplete. Raw request and response artifacts were
**not retained** in the public dossier bundle.

## Decision and promotion state

### Retained

- Historical agentic-coding specialist lead.

### Not authorized

- `no-promotion`.
- No route or deployment change is implied by this historical record.

## Failures and gotchas

### Serving behavior

- Default thinking exhausted small output budgets; the measured lane disabled
  thinking.

### Evidence limitations

- Checkpoint commit, image digest, engine commit, source recipe, and exact
  launch command: **Not retained.**
- Vendor quality claims: **Not tested locally.**

## Dated run history

- [2026-07-10 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
