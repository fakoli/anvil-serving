# Nemotron Nano and Omni 30B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** retained 2026-07-27 exclusive RTX 5090 Omni topology
      for auxiliary text, image understanding, and OCR; not a claim about
      current live state.
    - **Selected or best-qualified configuration:** Nemotron Nano Omni 30B
      NVFP4 on digest-pinned vLLM, 65,536 tokens, two sequences, with thinking
      disabled for text gates.
    - **Measured hardware:** one RTX 5090 on Fakoli Dark; the separate RTX PRO
      6000 Primary was protected rather than benchmarked.
    - **Evidence:** `functional` and bounded `capacity`; text, JSON, 4K
      retrieval, tools, image understanding, OCR, and c2 residency passed.
    - **Decision:** `current` in the dated published topology for auxiliary
      text/image/OCR; not a Primary promotion.
    - **Important limitation:** audio input was not tested, and the exact
      pinned nightly architecture/runtime must be requalified if changed.
    - **Review dates:** retained evidence through 2026-07-27; dossier-format
      review 2026-08-31.

[Open the retained Omni Compose configuration](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.yml)
or jump to the [decision](#decision-and-promotion-state),
[known limitations](#failures-and-gotchas), or
[dated evidence](#dated-run-history).

### Review narrative

#### 2026-07-10 — Runtime compatibility bakeoff

The 30B Omni checkpoint was unservable on vLLM 0.19 because the architecture
was unsupported and an attempted model-family masquerade did not match the
NVFP4 scale layout. On vLLM nightly `0.23.1rc1`, native support loaded the
model with the `nemotron_v3` reasoning parser and `qwen3_coder` tools. The
historical text-role bakeoff passed tools 20/20, session recall, and 64K
context, and measured 27.3 tok/s with 675 ms warm TTFT. Image and audio quality
were not tested in that text-role round.

**Outcome:** retain the older run as runtime-compatibility and text-path
evidence, not the final multimodal qualification.

#### 2026-07-27 — Exclusive Omni qualification

The pinned checkpoint and nightly digest were qualified on the RTX 5090 as an
exclusive `omni` stack. Thinking-disabled text smoke, JSON, 4K retrieval, and
three tools passed. Bounded image understanding and OCR passed against the
content-addressed screenshot fixture. A six-request c2 capacity probe measured
122/164 ms TTFT p50/p95, 423/515 ms end-to-end p50/p95, and 224.08 aggregate
output tok/s. The retained router admission covered auxiliary text,
`vision.general`, and `vision.ocr`; it did not replace Primary.

**Outcome:** `current` in the published 2026-07-27 exclusive Omni topology;
this dossier does not assert a current live deployment.

## Immutable identity

- **Model:** `nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4` revision
  `dc5f0b0bfddf8b6e0f5891475be9af05b80126fe`.
- **Served name:** `nemotron3-omni-30b-a3b-nvfp4`.
- **Runtime:** vLLM `0.23.1rc1.dev531+ga65f93fb2`.
- **Image:**
  `sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.
- **License/use restriction:** Not recorded in the retained dossier evidence.

## Tested hardware and topology

- **Measured:** one RTX 5090 on Fakoli Dark.
- **Execution mode:** exclusive Omni stack; the 27,999 MiB usable reservation
  intentionally prevented purpose-model co-residency.
- **Protected:** the separate RTX PRO 6000 Primary was not benchmarked by this
  result.
- **Mutually exclusive stacks:** embeddings/reranking, voice audio, and
  ComfyUI remained separate lifecycle choices.

## Engine, quantization, KV, context, and concurrency recipe

### Qualified exclusive Omni lane

- **Engine and image:** vLLM version and pinned digest above.
- **Weights and cache:** ModelOpt NVFP4; the retained recipe does not state a
  separate KV dtype.
- **Runtime controls:** `nemotron_v3` reasoning parser, `qwen3_coder` tool
  parser, PIECEWISE CUDA graphs, float16 Mamba SSM cache.
- **Contract:** 65,536 tokens, two sequences, thinking disabled for text
  gates, auxiliary text/image/OCR.
- **Recipe:** [serve registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml)
  and [exact public Compose service](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.yml).

## Evidence by measurement class

### Qualified 2026-07-27 topology

- **Status:** `functional` and bounded `capacity`.
- **Measured:** smoke, JSON, 4K retrieval, three tools, bounded image
  understanding, OCR, and 6/6 c2 requests passed. Capacity measured 122/164 ms
  TTFT p50/p95 and 224.08 aggregate tok/s.
- **Evidence:** [Omni qualification](../../findings/2026-07-27-omni-stack-qualification.md),
  [multimodal preflight](../../findings/2026-07-27-omni-stack-evidence/omni-multimodal-preflight.json),
  and [capacity artifact](../../findings/2026-07-27-omni-stack-evidence/omni-capacity.json).

### Historical runtime-compatibility lane

- **Status:** bounded text `functional`/`capacity` plus compatibility history.
- **Measured:** nightly runtime passed 20/20 tools, session recall, and 64K
  context; 27.3 tok/s and 675 ms warm TTFT in the earlier workload.
- **Limit:** image/audio quality was not tested in that bakeoff.
- **Evidence:** [Blackwell bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md).

## Decision and promotion state

### Retained dated role

- **Exclusive Omni:** `current` in the published 2026-07-27 topology for
  auxiliary text, image understanding, and OCR. This is not a live-state claim.

### Promotion boundary

- **Primary:** unchanged; Omni qualification did not promote or replace the
  text Primary.
- **Audio input:** `no-promotion`; Not tested in this 30B qualification.

## Failures and gotchas

### Evidence and interpretation limits

- **Audio:** Not tested. “Omni” in the model name is not local audio
  qualification.
- **Capacity:** the six-request c2 probe is operational evidence, not a broad
  quality comparison.

### Runtime and topology limits

- **Engine dependency:** older vLLM/NGC images rejected the architecture; the
  exact nightly digest is part of the qualified recipe.
- **Requalification:** changing the image, runtime, or model revision requires
  text, tools, image, OCR, context, and capacity gates again.
- **Exclusivity:** the reservation did not prove safe co-residency with the
  other mutually exclusive RTX 5090 stacks.

## Dated run history

- [2026-07-27 — exclusive Omni qualification](../../findings/2026-07-27-omni-stack-qualification.md)
- [2026-07-10 — Nano/Omni bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
