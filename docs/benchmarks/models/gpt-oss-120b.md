# GPT-OSS 120B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical capacity and throughput control;
      `no-promotion`, with no claim about current live routing.
    - **Selected or best-qualified configuration:** observed checkpoint
      revision with vLLM 0.23.1, native MXFP4, Marlin MoE, FP8 KV, and 131,072
      configured tokens.
    - **Measured hardware:** one NVIDIA RTX PRO 6000 Blackwell Max-Q on
      Fakoli Dark.
    - **Evidence:** functional preflight, 128K retrieval, tools 20/20, and a
      historical 183.2 tok/s controlled long-generation result.
    - **Decision:** retain as a historical throughput control only;
      `no-promotion`.
    - **Important limitation:** the contemporaneous 183.2 tok/s artifact does
      not retain a comparison-grade sequence cap, and the later deterministic
      quality recheck is `historical-invalid` because reasoning budgets were
      not comparable.
    - **Review dates:** retained evidence through 2026-07-12; dossier-format
      review 2026-08-31.

### Review narrative

#### 2026-07-10 — baseline throughput and capacity

The baseline passed the functional, long-context, tool, session, and bounded
intelligence checks at a 131,072-token window. Its established controlled
long-generation result was 183.2 tok/s, making it useful as a historical
throughput reference rather than a current deployment recommendation.

#### 2026-07-12 — deterministic protocol invalidation

The deterministic suite first produced empty visible answers because native
GPT-OSS reasoning consumed the configured response budget. Raising only that
budget changed the score, making the cross-model quality comparison invalid.
The retained result therefore documents an evaluation-protocol failure, not a
zero-quality model result.

## Immutable identity

`openai/gpt-oss-120b`, observed revision
`b5c939de8f754692c1647ca79fbf85e8c1e70f8a`. The tested vLLM image resolved
to `sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`
and reported engine version `0.23.1rc1.dev531+ga65f93fb2`.

The oldest launch recipe did not pin `--revision`. Treat the observed revision
as retained identity evidence, not proof that a future mutable pull is equal.

## Tested hardware and topology

One RTX PRO 6000 Blackwell Max-Q on Fakoli Dark as the earlier Heavy baseline,
under Windows 11, Docker Desktop, and WSL2.

## Engine, quantization, KV, context, and concurrency recipe

### Historical measured shape

The measured service used vLLM 0.23.1, native MXFP4, Marlin MoE, FP8 KV, CUDA
graphs, the OpenAI tool parser, and a 131,072-token served context. The
comparison-grade maximum sequence setting for the 183.2 tok/s run was **not
retained**.

### Pinned reconstruction recipe

The current registry entry in
[`configs/serve-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml) pins the
observed checkpoint revision, exact image digest, eight sequences, 8,192
batched tokens, and the retained serving flags. It is a reproducible recipe
pinned to the observed identity; do not retroactively label every field in it
as proven input to the earlier 183.2 tok/s artifact.

## Evidence by measurement class

### Functional and capacity

`functional` and `capacity` evidence includes coding, structured JSON, a 128K
needle, and 20/20 shared-prefix tool calls. The July 12 conventional run
completed 10/10 sequential 8K requests at c1.

### Performance

The retained controlled long-generation result is 183.2 tok/s. A separate
short mixed-prompt run measured 29.87 aggregate output tok/s, but that value is
not a controlled decode rate.

### Historical-invalid quality comparison

The deterministic recheck is not a valid protocol-v3 quality comparison.
Native reasoning exhausted the original visible-answer budget, and raising
only the response cap changed the result. Classify those cross-model scores as
`historical-invalid`, not as model quality evidence.

## Decision and promotion state

Retain GPT-OSS 120B as a historical throughput and compatibility control;
`no-promotion`. The pinned reconstruction recipe supports controlled reuse but
does not convert the invalid deterministic quality run into qualification.

## Failures and gotchas

### Historical launch identity

The oldest service did not pin `--revision`, and its 183.2 tok/s artifact did
not retain a comparison-grade maximum sequence cap. The observed revision and
later pinned recipe narrow reuse but do not erase those provenance gaps.

### Reasoning-budget confounder

GPT-OSS ignores the Qwen-style `enable_thinking=false` control and continues
to use its native reasoning channel. The deterministic suite's original token
budget could end before visible content appeared. A valid cross-model
protocol-v3 quality result was **not recorded**.

## Dated run history

- [2026-07-12 deterministic recheck](../../findings/2026-07-12-gpt-oss-120b-deterministic-recheck.md)
- [2026-07-10 baseline](../../findings/2026-07-10-blackwell-local-model-bakeoff.md#current-baselines)
