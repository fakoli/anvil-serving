# MiniMax M2.7 REAP

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical measured Heavy challenger retained for
      comparison; not a current recommendation or live role.
    - **Selected or best-qualified configuration:** community MiniMax M2.7
      REAP NVFP4 checkpoint on NGC vLLM 0.19, FP8 KV, 65,536 tokens,
      concurrency one, and thinking disabled.
    - **Measured hardware:** one RTX PRO 6000 Blackwell Max-Q card.
    - **Evidence:** `functional`, `capacity`, and bounded `quality`, with a
      `historical-invalid` immutable-identity limitation; 97.2 tok/s and the
      campaign's only 2/2 intelligence match to the Heavy baseline.
    - **Decision:** `no-promotion`; it was the best measured Heavy candidate at
      base-round close and was superseded in the campaign extension.
    - **Important limitation:** exact checkpoint revision and immutable image
      digest were not retained; the community prune's provenance and
      long-tail quality remain unaudited.
    - **Review dates:** retained evidence through 2026-07-11; dossier-format
      review 2026-08-31.

[Open the retained recipe registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-07-10–11 — Bakeoff result and supersession

The community REAP checkpoint ran through NGC vLLM 0.19 on one RTX PRO 6000
with compressed-tensors NVFP4, FP8 KV, 65,536 context, one sequence, the
`minimax_m2` tool parser, and thinking disabled. It passed context, tools,
session recall, and both deterministic intelligence checks, the only candidate
in the base round to match the Heavy baseline's 2/2. The warm 8K/256-output
probe measured 97.2 tok/s and 86 ms TTFT.

The lane used 94.3 GB at 64K and had no headroom for a 131K test. Default
thinking leaked think-text because no reasoning parser was configured. The
community checkpoint had no retained exact revision, and its provenance and
long-tail quality were not audited. Nemotron Puzzle later superseded it in the
campaign extension with official provenance, 131K evidence, and a stronger
controlled long-generation result.

**Outcome:** retain the measured result as historical comparison evidence with
`no-promotion`; it is not an exact-rerun or current recommendation claim.

## Immutable identity

- **Model repository:**
  `dervig/m51Lab-MiniMax-M2.7-REAP-139B-A10B-NVFP4`.
- **Served name:** `minimax-m27-reap-139b-nvfp4`.
- **Checkpoint revision:** Not retained. The snapshot is
  `historical-invalid` for exact reruns.
- **Runtime:** NGC vLLM 0.19 / image reference
  `nvcr.io/nvidia/vllm:26.04-py3`.
- **Immutable image digest:** Not retained.
- **Community provenance/license:** Not recorded as audited evidence.

## Tested hardware and topology

- **Measured:** one RTX PRO 6000 Blackwell Max-Q card on Fakoli Dark.
- **Execution mode:** isolated single-card candidate endpoint, one admitted
  sequence.
- **Capacity boundary:** 94.3 GB observed at the 65,536-token configuration;
  131K was not tested because no safe headroom remained.

## Engine, quantization, KV, context, and concurrency recipe

### Historical single-card lane

- **Engine:** NGC vLLM 0.19.
- **Weights and KV:** compressed-tensors NVFP4 and FP8 KV.
- **Contract:** 65,536 tokens, one sequence, `minimax_m2` tool parser,
  thinking disabled.
- **Recipe:** [registry entry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml)
  and [retained experiment Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml).

## Evidence by measurement class

### Functional and bounded quality

- **Status:** `functional` and bounded `quality` with
  `historical-invalid` immutable identity.
- **Measured:** context, tools, and session checks passed; intelligence 2/2.
- **Evidence:** [campaign finding](../../findings/2026-07-10-blackwell-local-model-bakeoff.md).

### Capacity and performance

- **Status:** bounded `capacity` at 65,536 tokens.
- **Measured:** 64K retrieval passed with 14.3-second TTFT; the warm 8K lane
  measured 97.2 tok/s and 86 ms TTFT.
- **Limit:** the result is not a 131K or controlled cross-model quality claim.
- **Evidence:** [campaign finding and evidence index](../../findings/2026-07-10-blackwell-local-model-bakeoff.md#evidence-index).

## Decision and promotion state

### Retained

- **Historical result:** `no-promotion`; retain as the base-round Heavy
  comparison and a record of the community checkpoint's measured behavior.

### Superseded or incomplete

- **Campaign recommendation:** superseded by Nemotron Puzzle in the extension.
- **Exact rerun:** blocked by the missing checkpoint revision and image digest.

## Failures and gotchas

### Evidence and provenance limits

- **Immutable identity:** the checkpoint revision and image digest were not
  retained.
- **Community checkpoint:** prune provenance, licensing, and long-tail quality
  were not audited.

### Runtime and capacity limits

- **VRAM ceiling:** 94.3 GB at 64K left no safe 131K path on the measured card.
- **Reasoning parser:** none was configured; default-thinking responses leaked
  think-text, so the measured contract kept thinking disabled.
- **Comparison boundary:** 97.2 tok/s remained roughly half the dated
  production Heavy baseline and did not authorize promotion.

## Dated run history

- [2026-07-10–11 — Blackwell local-model bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
