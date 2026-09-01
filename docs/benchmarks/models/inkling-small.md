# Inkling Small

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** qualified exclusive TP=2 challenger retained as
      published evidence; this dossier does not claim a current live route.
    - **Selected or best-qualified configuration:**
      `thinkingmachines/Inkling-Small-NVFP4` on SGLang, native ModelOpt NVFP4,
      BF16 KV/SWA, 32,768 tokens, concurrency one, and low reasoning.
    - **Measured hardware:** two RTX PRO 6000 Blackwell Max-Q cards over PCIe
      without NVLink, in an isolated exclusive TP=2 lane.
    - **Evidence:** `functional`, `capacity`, and `quality`; the low-reasoning
      lane passed the declared gates and completed 12/12 capacity requests.
    - **Decision:** `no-promotion`; the campaign changed no production alias
      or router profile.
    - **Important limitation:** the reasoning-off Responses subset still
      exposed internal reasoning, and the compatibility changes have no
      matched performance A/B.
    - **Review dates:** retained evidence through 2026-08-01; dossier-format
      review 2026-08-31.

[Open the tracked TP=2 campaign recipe](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-08-01 — Exclusive TP=2 qualification

The exact checkpoint qualified as an exclusive TP=2 candidate with
`no-promotion`. With `reasoning_effort=low`, smoke, JSON, 30K retrieval, tools
20/20, repeated intelligence 6/6, session 3/3, and tools 3/3 all passed. The
low-reasoning 32K capacity lane completed 12/12 with TTFO p50 2.79 seconds,
first-visible TTFT p50 4.63 seconds, effective prefill 7,844 tok/s, and
combined reasoning/visible decode 73.5 tok/s. A separate reasoning-off lane
completed 12/12 with 2.84-second TTFT and 74.6 tok/s visible decode.

The core reasoning-off preflight passed, but the extended Responses subset
still emitted internal reasoning with `reasoning_effort=none` and therefore
failed the stricter forbidden-reasoning evidence policy. The final runtime
also required revision-aware ModelOpt lookup, a missing loader dependency, an
SM120 two-stage grouped-GEMM fallback, the existing Triton activation fallback
where no SM120 Helion configuration was shipped, and the WSL2 logits-only
symmetric-memory guard. These compatibility fixes do not prove a performance
improvement. No genuine NVFP8-labeled Inkling artifact was found; the tested
checkpoint is the publisher's NVFP4 release.

**Outcome:** the low-reasoning 32K contract qualified as a measured challenger,
while reasoning-off policy compliance and any broader context or promotion
claim remain closed.

## Immutable identity

- **Model:** `thinkingmachines/Inkling-Small-NVFP4` revision
  `b6a99534467840620d411e4cd4ad5819b2610d9c`.
- **Served name:** `inkling-small-nvfp4-tp2`.
- **Runtime:** SGLang revision `b7252cc6b`, derived image
  `sha256:6a8afc5ca0036c1be8810443636d6f835702d1e2ae5a1d717990b0baf8e70a2f`.
- **License/use restriction:** Not recorded in the retained public evidence;
  do not infer one from the checkpoint name.

## Tested hardware and topology

- **Measured:** two RTX PRO 6000 Blackwell Max-Q cards on Fakoli Dark.
- **Execution mode:** exclusive TP=2 over PCIe without NVLink; one admitted
  request.
- **Protected or co-resident:** every other inference workload was offline.
- **Comparability boundary:** the campaign did not provide a clean TP=1 versus
  TP=2 or compatibility-fix performance A/B.

## Engine, quantization, KV, context, and concurrency recipe

### Qualified low-reasoning lane

- **Engine and image:** SGLang `b7252cc6b`, pinned derived image above.
- **Weights and KV:** native ModelOpt NVFP4, Marlin FP4/MoE, Triton attention,
  and BF16 KV/SWA.
- **Contract:** 32,768 tokens, one admitted request,
  `reasoning_effort=low`, speculative decode off.
- **Compatibility controls:** narrowly gated SM120/WSL2 loader, grouped-GEMM,
  activation, and logits-gather fixes; these are not a claimed kernel tune.
- **Recipe:** [tracked campaign configuration](https://github.com/fakoli/anvil-serving/blob/main/configs/tp2-model-campaign-recipes.toml).

## Evidence by measurement class

### Low-reasoning qualification

- **Status:** `functional`, `capacity`, and `quality`; declared contract pass.
- **Measured:** functional gates passed; repeated quality passed 6/6
  intelligence, 3/3 session, and 3/3 tools. The 32K lane completed 12/12 at
  2.79-second TTFO p50, 4.63-second first-visible TTFT p50, 7,844 tok/s
  effective prefill, and 73.5 tok/s combined reasoning/visible decode.
- **Evidence:** [campaign finding](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md),
  [functional artifact](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/inkling-functional-low.json),
  [quality artifact](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/inkling-quality-low.json),
  and [capacity artifact](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/inkling-capacity-32k-low.json).

### Reasoning-off control

- **Status:** bounded functional/capacity control with a retained policy
  failure.
- **Measured:** 12/12 capacity requests, 2.84-second TTFT, and 74.6 tok/s
  visible decode.
- **Limit:** the extended Responses subset emitted forbidden internal
  reasoning, so this is not the selected contract.
- **Evidence:** [core preflight](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/inkling-functional-none.json),
  [extended failure](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/inkling-extended-none.json),
  and [capacity artifact](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/inkling-capacity-32k-none.json).

## Decision and promotion state

### Retained

- **Low-reasoning TP=2 lane:** `no-promotion`; retain as a qualified,
  reproducible challenger and compatibility record.

### Incomplete

- **Reasoning-off lane:** not selected because the stricter Responses policy
  failed.
- **Longer context:** Not tested beyond the 32,768-token served contract.

## Failures and gotchas

### Evidence and interpretation limits

- **Reasoning-off behavior:** internal reasoning still appeared in the
  extended Responses subset.
- **Performance attribution:** the compatibility patches have no matched A/B
  and therefore do not establish a speedup.
- **Format boundary:** no genuine NVFP8-labeled Inkling artifact was found;
  the measured checkpoint is NVFP4.

### Runtime and topology limits

- **SM120/WSL2 bring-up:** the retained startup chain required loader,
  revision lookup, grouped-GEMM, activation, and logits-gather fixes.
- **Topology:** aggregate PCIe VRAM is not unified memory, and no TP=1 control
  was measured in this campaign.

## Dated run history

- [2026-08-01 — dual-PRO TP=2 campaign](../../findings/2026-08-01-dual-pro-tp2-model-campaign.md)
- [2026-08-01 — raw startup compatibility chain](../../findings/2026-08-01-dual-pro-tp2-campaign-evidence/inkling-startup-compatibility.json)
