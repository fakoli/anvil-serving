# Gemma 4 RTX PRO 6000 variants

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical strict-quality controls across official and
      Unsloth Gemma 4 variants; no variant is presented as current or as a
      live route assignment.
    - **Selected or best-qualified configuration:** official 12B QAT W4A16
      with its separately pinned tokenizer, vLLM 0.25.1, FP8 KV, 262,144
      configured tokens, and five admitted sequences.
    - **Measured hardware:** one NVIDIA RTX PRO 6000 Blackwell Max-Q; matched
      RTX 5090 rows in the findings are a separate hardware lane.
    - **Evidence:** the official 12B passed repeated strict quality and a 240K
      retrieval; 26B variants failed timeout triage, while 31B variants passed
      quality but remained materially slower.
    - **Decision:** retain the official 12B as the correctness control;
      classify the current dossier decision as `no-promotion` for 12B and
      `rejected` for the 26B timeout and 31B latency outcomes.
    - **Important limitation:** exact standalone launch commands were **not
      retained** for the Unsloth NVFP4 arms, and Unsloth did not publish its
      exact calibration or important-layer selection algorithm.
    - **Review dates:** retained evidence through 2026-07-17; dossier-format
      review 2026-08-31.

### Review narrative

#### 2026-07-16 — official template and context sweep

The July 15 tokenizer refresh was tested with model and tokenizer revisions
pinned independently. Official 12B QAT W4A16 matched the strict-quality
control, passed the 240K context gate, and improved context latency. Official
26B BF16 failed timeout triage despite strong capacity results. Official 31B
QAT W4A16 passed the quality gate but remained materially slower than 12B.

#### 2026-07-16 — Unsloth NVFP4 follow-up

Unsloth 12B and 26B-A4B NVFP4 passed functional and context gates but failed
the repeated quality threshold. Unsloth 31B NVFP4 passed repeated quality and
reported substantial KV capacity, but its latency was operationally too high.
No router profile, production recipe, or tier recommendation changed.

#### 2026-07-17 — 31B optimization probe

The warmed official 31B single-request long-generation probe measured 62.3
tok/s across two equal 512-token responses. Native MTP was blocked for this
target, and the measured improvement did not overcome the 31B latency decision.

## Immutable identity

Retained PRO configurations:

- official 12B QAT W4A16 `5d8bb23cdbff01e89d2a1a47f3b3d29b877bca76`,
  tokenizer `12ace6d648d72bd41519e140f1185f34d38c7e3d`;
- official 26B-A4B BF16 `01e5b3ee840d3a9e0b0b493c593e85398a30ef75`;
- official 31B QAT W4A16 `a766e9afa44931dfa9ff5de90af9494ca193e74c`,
  tokenizer `b9ea41a2887d8607f594846523f94c6cc75ac8a4`;
- Unsloth 12B NVFP4 `b1f649734b34aa5575b03d186abd1b9be3d0d5c4`;
- Unsloth 26B-A4B NVFP4 `20df0542b1a86ce19f495ac2eca2c7c12bce82f9`;
- Unsloth 31B NVFP4 `373c00b5ecb0a8ee43942b5ca08b93805de8eee4`.

The common vLLM 0.25.1 image resolved to
`sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089`.

## Tested hardware and topology

The measurements summarized here used one RTX PRO 6000 Blackwell Max-Q on
Fakoli Dark. Matched RTX 5090 rows are kept in the same findings but are not
PRO 6000 measurements and should not be merged into the hardware claim.

## Engine, quantization, KV, context, and concurrency recipe

### Official retained controls

The official arms used vLLM 0.25.1, compressed-tensors W4A16 or BF16 weights,
FP8 KV, and the Gemma reasoning/tool parsers. The 12B control served 262,144
tokens with five admitted sequences and passed a 240K needle. Its full pinned
recipe remains in
[`configs/serve-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml).

The retained official 26B and 31B artifacts include the exact model revisions,
262,144-token window, FP8 KV, and direct `vllm serve` source commands. They do
not have separate durable recipe files.

### Unsloth NVFP4 arms

The Unsloth arms used the same digest-pinned vLLM 0.25.1 image, the V1 WSL2
compatibility runner, native `FlashInferCutlassNvFp4LinearKernel`, Triton
attention, FP8 KV, and a 262,144-token Heavy window. Exact checkpoint metadata
is retained in
[`checkpoint-metadata.json`](../../findings/2026-07-16-gemma4-nvfp4-evidence/checkpoint-metadata.json)
and runtime identity in
[`runtime-observations.json`](../../findings/2026-07-16-gemma4-nvfp4-evidence/runtime-observations.json).

**Not retained:** the Unsloth NVFP4 benchmark artifacts record
`source_recipe.serve_command = null`; do not manufacture a complete standalone
launch command from the surrounding prose.

## Evidence by measurement class

### Official 12B correctness control

`functional`, `capacity`, and `quality` evidence includes repeated chat,
context, tool, session, and intelligence checks; 20/20 tools; 240K retrieval;
and 32K c1/c2 capacity. The strict quality result passed with thinking enabled.

### 26B and 31B outcomes

Official 26B BF16 and Unsloth 26B-A4B NVFP4 passed functional and long-context
gates but failed timeout triage at the repeated 100% quality threshold.
Official 31B W4A16 and Unsloth 31B NVFP4 passed strict quality but remained
materially slower than the 12B control. The warmed official 31B optimization
probe measured 62.3 tok/s for two equal 512-token responses.

### Quantized comparison boundary

The local NVFP4 runs are measurements of the pinned artifacts, not proof of
how Unsloth generated them. The publisher did not disclose the exact Gemma
calibration or important-layer selection algorithm, so that conversion step is
**not reproducible** from the retained repository evidence.

## Decision and promotion state

### Retained historical control

The official 12B QAT W4A16 configuration is the historical strict-quality
control. It is retained as `no-promotion` in the current comparison rather
than described as a current deployment.

### Rejected alternatives

The 26B timeout behavior and 31B operational latency remain `rejected` for the
tested roles. A quality pass does not override the 31B latency result.

## Failures and gotchas

### Identity and reproduction

Pin model and tokenizer revisions separately. The exact standalone source
commands were not retained for the Unsloth NVFP4 arms, and their publisher's
quantization calibration details remain unavailable.

### Quality and latency

The 26B variants failed timeout triage. The 31B variants passed quality but
were rejected for latency. Cold vLLM 0.25.1 Gemma startup on this WSL2 and
Blackwell host includes several minutes of graph compilation; warm request
latency is reported separately. Do not infer current status from older
promotion-era findings.

## Dated run history

- [2026-07-17 31B optimization](../../findings/2026-07-17-gemma4-31b-optimization.md)
- [2026-07-16 vLLM 0.25.1 sweep](../../findings/2026-07-16-gemma4-vllm0251-wsl2-c128.md)
- [2026-07-16 Unsloth follow-up](../../findings/2026-07-16-gemma4-unsloth-nvfp4-follow-up.md)
- [2026-07-16 template bakeoff](../../findings/2026-07-16-gemma4-chat-template-bakeoff.md)
