# Gemma 4 E4B Fast

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** historical RTX 5090 Fast control with retained
      promotion-era and strict-quality evidence; it is not presented as the
      current Fast model or a live route assignment.
    - **Selected or best-qualified configuration:**
      `leon-se/gemma-4-E4B-it-FP8-Dynamic` at the full pinned revision, vLLM
      0.25.1, 32,768 configured tokens, and thinking disabled.
    - **Measured hardware:** one NVIDIA GeForce RTX 5090 on Fakoli Dark.
    - **Evidence:** functional preflight, 30K retrieval, tools 20/20, repeated
      strict quality, and bounded c1/c2 capacity and latency measurements.
    - **Decision:** retain the exact FP8-Dynamic profile as a historical Fast
      control; current dossier decision `no-promotion`.
    - **Important limitation:** the earlier GGUF lane lacks an immutable model
      revision and immutable llama.cpp image identity, so exact equivalence is
      **not retained** for that candidate.
    - **Review dates:** retained evidence through 2026-07-16; dossier-format
      review 2026-08-31.

### Review narrative

#### 2026-07-10–11 — GGUF latency candidate

The Unsloth Gemma 4 E4B QAT UD-Q4_K_XL candidate passed tools 20/20, session
recall, and the 64K context window, while only one of two intelligence checks
passed. Its 61 ms warm TTFT and approximately 97 tok/s result made it a useful
low-latency specialist, but it was not promoted.

#### 2026-07-13 — Fast promotion-era decision

The exact FP8-Dynamic control became the human-approved Fast profile in its
dated campaign after the managed reservation and functional checks passed.
That record remains historical and does not describe current live routing.

#### 2026-07-16 — strict-quality retention

The FP8-Dynamic control passed every repeated quality gate and remained the
bounded Fast control while newer E2B, E4B, and 12B candidates failed timeout
triage or did not improve the selected role. No present-day promotion is
implied.

## Immutable identity

The historical FP8 control is
`leon-se/gemma-4-E4B-it-FP8-Dynamic@56e30bf603d18a4972caffafa1bb4a4f9a841dee`.
The pinned vLLM image digest is
`sha256:e4f88a835143cd22aee2397a26ec6bb80b3a4a6fe0c882bcbc63822904766089`.

The earlier GGUF candidate used `unsloth/gemma-4-E4B-it-qat-GGUF`
UD-Q4_K_XL. Its exact immutable repository revision and llama.cpp image digest
were **not retained**, so it cannot support a new equivalence claim.

## Tested hardware and topology

The retained measurements used one RTX 5090 on Fakoli Dark as the former Fast
tier. The promotion-era reservation preserved the co-resident voice sidecars;
that historical topology is not a claim about present live occupancy.

## Engine, quantization, KV, context, and concurrency recipe

### Pinned FP8-Dynamic control

The exact control used the digest-pinned vLLM 0.25.1 Gemma multimodal path,
compressed-tensors FP8-Dynamic weights, the Gemma reasoning and tool parsers,
a 32,768-token window, and thinking disabled for the qualified Fast contract.
The retained recipe, including full revision, environment, memory fraction,
served name, and flags, is in
[`configs/serve-recipes.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml).

### Unverified GGUF lane

The GGUF latency candidate used llama.cpp, UD-Q4_K_XL QAT weights, a 65,536-
token window, and model sampling defaults with thinking disabled for visible
answers. The registry retains its measured shape, but the mutable
`ghcr.io/ggml-org/llama.cpp:server-cuda` tag and missing model revision mean
the exact historical container cannot be reconstructed.

## Evidence by measurement class

### FP8 functional, quality, and capacity

`functional`, `capacity`, and `quality` evidence covers promotion-era router
checks, template controls, and low-latency measurements. The control passed a
30K retrieval and repeated chat/context/tool/session/intelligence gates. At a
32K fixed-context shape it measured 0.46 s TTFT and 49 aggregate tok/s at c1,
and 0.58 s TTFT and 79 aggregate tok/s at c2.

### GGUF compatibility and latency

The GGUF candidate passed tools 20/20, session recall, and the 64K window; one
of two intelligence checks failed. Its retained measurements report about 97
tok/s and 61 ms warm TTFT. Because immutable model and image identities are
missing, this is historical compatibility/performance evidence rather than an
exactly reproducible qualification.

## Decision and promotion state

### Historical Fast control

The FP8-Dynamic configuration is retained as a historical control only;
`no-promotion` under the current product comparison. Its dated human-approved
promotion does not establish a current live route.

### GGUF candidate

The GGUF lane remains an unverified low-latency specialist and
`no-promotion`. Missing immutable identity blocks an equivalence claim.

## Failures and gotchas

### Evidence boundaries

Do not treat uncalibrated router seed rows as benchmark results. The July 27
official Gemma access probe failed authorization and loaded no weights.

### Reproduction boundary

The FP8-Dynamic control has a full retained revision and image digest. The
GGUF lane does not: its exact model revision, llama.cpp commit, and image
digest were **not recorded**. Do not substitute a current mutable image and
label it as the measured run.

## Dated run history

- [2026-07-16 template bakeoff](../../findings/2026-07-16-gemma4-chat-template-bakeoff.md)
- [2026-07-13 router promotion-era record](../../findings/2026-07-13-e4b-fast-router-promotion.md)
- [2026-07-10 GGUF bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
