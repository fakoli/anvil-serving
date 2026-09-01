# Nemotron 3 Super 120B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical single-card and TP=2 Heavy challenger,
      retained as bounded comparison evidence rather than a current live role.
    - **Selected or best-qualified configuration:** the single-card 131K/c5
      lane has the broader reasoning-quality record; the later TP=2/EP=2
      65K/c1 lane provides the fresh dual-card functional and capacity result.
    - **Measured hardware:** one RTX PRO 6000 historically, then two equal RTX
      PRO 6000 Blackwell Max-Q cards over PCIe in exclusive TP=2 plus EP=2.
    - **Evidence:** `functional`, `capacity`, and bounded `quality`; the TP=2
      refresh passed repeated gates and measured 59.5 tok/s at 32K.
    - **Decision:** `no-promotion`; retain as a strong
      1,024-reasoning-headroom control and dual-card comparison.
    - **Important limitation:** the advertised 1M context was not tested, and
      the single-card and TP=2 lanes are not a clean topology-only A/B.
    - **Review dates:** retained evidence through 2026-08-01; dossier-format
      review 2026-08-31.

[Open the tracked TP=2 recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-07-12 — Single-card Heavy selection and repaired quality

The historical lane used vLLM nightly, official NVFP4 weights, FP8 KV,
the official `super_v3` reasoning parser, `qwen3_coder` tools,
`mamba_cache_dtype=float16`, 131,072 context, and five admitted sequences. It
passed preflight, 131K context, five sessions, 20/20 tools, and the initial
deterministic gates. Five-session capacity completed at c1 and c5 with
0.62/2.52-second TTFT p50 and 33.19/45.90 aggregate output tok/s.

The repaired reasoning-budget protocol then showed 5/5 stable ARC items and
15/15 passing attempts at 1,024 tokens of headroom. The ten-row MMLU-Pro slice
stabilized 8/10 with 23/30 passing attempts at both 1,024 and 2,048 tokens;
doubling headroom did not improve an item and increased wall time.

**Outcome:** selected as the stronger bounded Heavy experiment in that dated
round, without router promotion.

#### 2026-08-01 — Exclusive TP=2 plus expert-parallel refresh

The same checkpoint ran on a pinned NVIDIA vLLM image with native NVFP4, FP8
KV, TP=2 plus expert parallel, 65,536 context, one admitted request, and MTP
disabled. Smoke, JSON, 30K retrieval, tools, extended tools, intelligence 6/6,
session 3/3, and tools 3/3 passed. The 32K lane measured 2.84-second TTFT,
10,025 effective prefill tok/s, and 59.5 tok/s decode. At 53,820 prompt tokens
it measured 5.58-second TTFT and 60.0 tok/s decode.

**Outcome:** qualified as a TP=2 `no-promotion` challenger; no production alias
or route changed.

## Immutable identity

- **Model:** `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` revision
  `4f0cf9daaeb7a4d5e23f80a00e7ed15f0e03caf6`.
- **Single-card served name:** `nemotron3-super-120b-a12b-nvfp4`.
- **Single-card runtime:** vLLM `0.23.1rc1.dev531+ga65f93fb2`, image
  `sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.
- **TP=2 served name:** `nemotron3-super-120b-a12b-nvfp4-tp2`.
- **TP=2 runtime image:** NVIDIA vLLM
  `sha256:bebcf9576b1720214319ee5c7ee4f7661954cbbf59ed3fcd188cd79a67f1967e`.
- **License/use restriction:** Not recorded in the retained dossier evidence.

## Tested hardware and topology

### Historical single-card lane

- **Measured:** one RTX PRO 6000 Blackwell Max-Q card.
- **Execution mode:** isolated TP=1 endpoint, five admitted sequences, prefix
  caching disabled.

### 2026-08-01 TP=2 lane

- **Measured:** two equal RTX PRO 6000 Blackwell Max-Q cards.
- **Execution mode:** exclusive TP=2 plus EP=2 over PCIe without NVLink;
  concurrency one.
- **Protected or co-resident:** other inference workloads were offline.
- **Comparability boundary:** runtime, context, admission, and workload differ
  from the single-card lane.

## Engine, quantization, KV, context, and concurrency recipe

### Historical single-card 131K/c5 lane

- **Engine and image:** vLLM nightly version and digest above.
- **Weights and cache:** official NVFP4, FP8 KV, float16 Mamba state.
- **Runtime controls:** official `super_v3` reasoning parser,
  `qwen3_coder` tool parser, text-only, prefix caching disabled.
- **Contract:** 131,072 tokens and five sequences.
- **Recipe:** [retained experiment Compose](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.experiment.yml).

### TP=2/EP=2 65K/c1 lane

- **Engine and image:** pinned NVIDIA vLLM digest above.
- **Weights and cache:** native NVFP4, FP8 KV, float32 Mamba SSM state.
- **Topology:** TP=2 plus expert parallel, MTP disabled.
- **Contract:** 65,536 tokens, one admitted request.
- **Recipe:** [TP=2 campaign registry](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml).

## Evidence by measurement class

### Historical functional and capacity evidence

- **Status:** `functional`, `capacity`, and bounded `quality`.
- **Measured:** preflight, 131K context, five sessions, and 20/20 tools passed;
  c1/c5 TTFT p50 measured 0.62/2.52 seconds with 33.19/45.90 aggregate tok/s.
- **Evidence:** [challenger comparison](../../findings/2026-07-12-heavy-intelligence-challengers.md).

### Repaired reasoning-quality slices

- **Status:** bounded `quality`, retained as a control.
- **Measured:** ARC 5/5 stable and 15/15 attempts at 1,024 headroom;
  MMLU-Pro 8/10 stable and 23/30 attempts at both 1,024 and 2,048.
- **Limit:** the protocol-v2 artifacts have `source_recipe=null`; later lineage
  binding is not generation-time provenance.
- **Evidence:** [protocol-v2 finding](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md)
  and [run lineage](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/run-lineage.json).

### TP=2 functional and capacity refresh

- **Status:** `functional`, `capacity`, and `quality`; `no-promotion`.
- **Measured:** repeated gates passed; 32K measured 2.84-second TTFT,
  10,025 prefill tok/s, and 59.5 tok/s decode; 53,820 prompt tokens measured
  5.58-second TTFT and 60.0 tok/s decode.
- **Evidence:** [campaign finding](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md),
  [32K artifact](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/nemotron3-super-capacity-32k-c1.json),
  and [60K artifact](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/nemotron3-super-capacity-60k-c1.json).

## Decision and promotion state

### Retained

- **Single-card lane:** historical 131K quality/capacity control.
- **TP=2 lane:** `no-promotion`; retain as the fresh exclusive dual-card
  qualification.

### Incomplete

- **Advertised 1M context:** Not tested.
- **Topology comparison:** no matched TP=1 versus TP=2 A/B exists.
- **Live state:** Not asserted by this dossier.

## Failures and gotchas

### Evidence and interpretation limits

- **Protocol lineage:** the repaired protocol artifacts predate embedded suite
  hashes and source recipes.
- **Shared KV:** engine-reported shared KV token capacity does not mean each
  admitted sequence can consume that maximum simultaneously.
- **Quality scope:** ARC/MMLU-Pro rows are bounded repeated slices, not full
  benchmark scores.

### Runtime, context, and topology limits

- **Context:** the TP=2 recipe deliberately serves 65,536 tokens; its 60K
  result is not evidence for the historical 131K lane or the advertised 1M.
- **Comparability:** different images, context windows, admission caps, and
  workloads prevent a topology-only performance claim.
- **Interconnect:** two PCIe cards provide aggregate sharded VRAM, not unified
  memory.

## Dated run history

- [2026-08-01 — dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-07-12 — Heavy evaluation protocol v2](../../findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md)
- [2026-07-12 — challenger comparison](../../findings/2026-07-12-heavy-intelligence-challengers.md)
