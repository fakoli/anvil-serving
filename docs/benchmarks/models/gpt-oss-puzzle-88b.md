# GPT-OSS Puzzle 88B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical human-approved Heavy profile and pinned
      rollback in its dated campaign; retained as reproducible evidence, not a
      claim about the current live rollback chain.
    - **Selected or best-qualified configuration:** exact Puzzle checkpoint
      and custom Anvil vLLM commit, Marlin MXFP4 MoE, FP8 KV, 131,072 context,
      eight sequences, and 8,192 batched tokens.
    - **Measured hardware:** one NVIDIA RTX PRO 6000 Blackwell Max-Q on
      Fakoli Dark.
    - **Evidence:** full functional and router qualification, long-context
      retrieval, tools 20/20, capacity 40/40 at c8, and repeated quality with
      one retained unified-diff failure.
    - **Decision:** retain as a dated `rollback` configuration and historical
      Heavy compatibility result; no present-day route or promotion claim.
    - **Important limitation:** strict unified-diff formatting passed only 2/3,
      and the earlier 65.72% GPQA result was **not rerun** on the final image.
    - **Review dates:** retained evidence through 2026-07-18; dossier-format
      review 2026-08-31.

### Review narrative

#### 2026-07-17 — custom-engine qualification

The exact Puzzle checkpoint required a custom vLLM branch for heterogeneous
expert counts, attention windows, and the missing Harmony `<|call|>` EOS
behavior. The pinned image passed model identity, long-context retrieval,
tools, Responses, bounded capacity, and the initial quality checks on one RTX
PRO 6000.

#### 2026-07-18 — final-image Heavy enablement

The final source-built image passed the managed functional, router, interface,
capacity, and repeated quality gates and became the human-approved Heavy
profile in its dated campaign. The strict quality suite retained one 2/3
unified-diff failure. That campaign later treated the profile as a pinned
rollback; neither decision describes current live state.

## Immutable identity

- Model: `nvidia/gpt-oss-puzzle-88B` revision
  `9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2`.
- Engine: `fakoli/anvil-vllm` commit
  `485463b3498ed3ffcf0c8fcb52c1670a21be5d82`.
- Qualified image ID:
  `sha256:470f7b7e39c4363696d5a79fd041d6a45253229a9ba1c055d089ddbdc0ed120c`.
- Served name: `gpt-oss-puzzle-88b`.

## Tested hardware and topology

One RTX PRO 6000 Blackwell Max-Q on Fakoli Dark through an isolated serve and
then managed Heavy/router qualification. The dated transaction preserved the
Gemma 4 rollback configuration; it is not a current live-topology claim.

## Engine, quantization, KV, context, and concurrency recipe

### Pinned serving shape

The qualified profile uses the native Puzzle architecture, Harmony template,
OpenAI tool parser, automatic tool choice, Marlin MXFP4 MoE, FP8 KV, 131,072
context, eight sequences, and 8,192 batched tokens. Preserve the explicit EOS
override and V2-runner disable.

### Complete reproduction procedure

The [canonical operator recipe](../gpt-oss-puzzle-88b-recipe.md) retains the
exact engine build, checkpoint pull, image verification, GPU selection,
managed start, functional/capacity/quality gates, router verification, and
rollback procedure. Do not substitute stock vLLM or silently retag another
build.

## Evidence by measurement class

### Functional and long-context

`functional`, `capacity`, and `quality` evidence covers coding, JSON,
long-context retrieval, tools 20/20, streaming, Responses, session behavior,
and timeout triage. The exact final image also closed the earlier tool-parser
HTTP 500.

### Capacity and latency

The production-shaped 8K capacity run completed 40/40 at concurrency eight.
Its 0.766/1.075 s TTFT p50/p95 and 0.906/1.148 s E2E p50/p95 are bounded
request-completion measurements. The reported 17.85 aggregate tok/s used only
86 output tokens and is not a controlled decode rate.

### Quality

The repeated quality run passed context, tools, session recall, and timeout
triage. Strict unified-diff formatting passed 2/3 because one answer included
an extra leading space after the diff marker. The separate 65.72% GPQA
artifact used the pre-final image and is supporting history, not a final-image
rerun.

## Decision and promotion state

The exact configuration was human-approved as Heavy and later declared a
pinned `rollback` in its dated campaign. Retain that historical decision and
the complete recipe without presenting it as the current rollback chain or a
live route assignment.

## Failures and gotchas

### Engine and EOS coupling

Do not substitute a stock engine or silently retag. The checkpoint varies
expert counts and attention windows by layer, and its generation config omits
the Harmony `<|call|>` EOS token required by the working tool path. Preserve
the exact fork commit, full generation-config override, Marlin backend, and
V2-runner disable.

### Quality boundary

Strict unified-diff formatting passed only 2/3. The earlier 65.72% GPQA
artifact used the pre-final image and was **not rerun** on the final qualified
image. No general superiority over Gemma is claimed.

## Dated run history

- [2026-07-18 Heavy enablement](../../findings/2026-07-18-gpt-oss-puzzle-heavy-promotion.md)
- [2026-07-17 qualification](../../findings/2026-07-17-gpt-oss-puzzle-qualification.md)
- [Canonical operator recipe](../gpt-oss-puzzle-88b-recipe.md)
