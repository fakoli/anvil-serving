# Qwen2.5-Omni 3B

<!-- benchmark-dossier/v2 -->

## Current status and review date

!!! info "Decision snapshot"

    - **Product role:** Operator-selectable smaller multimodal shape that can
      co-reside with dedicated speech services.
    - **Selected or best-qualified configuration:** Pinned vLLM nightly-derived
      audio image, 32,768 model length, concurrency two in the retained
      capacity probe, and a 24,576 MiB model reservation alongside 2,048 MiB
      each for STT and TTS.
    - **Measured hardware:** One RTX 5090 on Fakoli Dark, co-resident with
      Parakeet STT and Kokoro TTS; the RTX PRO 6000 was not measured.
    - **Evidence:** Text, JSON, 4K retrieval, image/OCR, basic audio input, a
      6/6 concurrency-two capacity probe, and a dedicated voice round trip.
    - **Decision:** `challenger`, `no-promotion`; retain as an
      operator-selectable co-resident shape.
    - **Important limitation:** The audio response was noisy, and the retained
      runtime reported `gpu_memory_utilization=0.72` while the later public
      Compose reconstruction uses `0.2511`; that configuration drift is
      unresolved and must not be silently blended.
    - **Review dates:** Retained evidence cutoff: 2026-07-27. Dossier-format
      review: 2026-08-31.

### Review narrative

#### 2026-07-27 — Audio packaging repair

The stock image passed text, image, and OCR but returned HTTP 500 for audio
input because `vllm[audio]` was missing. Rebuilding through the managed Compose
service with pinned audio packages corrected that packaging gap. The rebuilt
path accepted audio and recognized the bounded sample, but its repetitive,
noisy response is not an STT-quality qualification.

#### 2026-07-27 — Co-resident qualification

Qwen2.5-Omni ran on the RTX 5090 with Parakeet and Kokoro resident. Text smoke,
JSON, 4K retrieval, image understanding, and OCR passed. The capacity probe
completed 6/6 requests at concurrency two, and the dedicated speech round trip
passed with 0.0 WER. The result retains a smaller operator-selectable shape but
does not authorize router promotion.

## Immutable identity

### Model

- Checkpoint: `Qwen/Qwen2.5-Omni-3B`.
- Revision: `f75b40e3da2003cdd6e1829b1f420ca70797c34e`.
- Served name: `qwen25-omni-3b`.

### Runtime

- Derived image tag: `anvil-vllm:omni-small-audio-a65f93fb2`.
- Derived image digest: **Not retained.**
- Base image digest:
  `sha256:907377dddef392f6b679d9c071e1c33c3935b4dc993b61d0352e391a5319ff3e`.
- vLLM: `0.23.1rc1.dev531+ga65f93fb2`.
- Transformers: `5.12.1`.
- Audio packages: `av==18.0.0`, `soundfile==0.14.0`, and `soxr==1.1.0`;
  `flash-attn` was not installed.

## Tested hardware and topology

### Co-resident lane

- Host label: Fakoli Dark.
- Measured device: one RTX 5090 with 32,607 MiB.
- Co-resident services: Parakeet STT and Kokoro TTS.
- Declared reservations: 24,576 MiB for Qwen and 2,048 MiB each for STT and
  TTS.

The RTX PRO 6000 and multi-GPU execution were **not tested**.

## Engine, quantization, KV, context, and concurrency recipe

### Measured 2026-07-27 runtime

- Model length: 32,768 tokens.
- Capacity probe: 2,048-token prompts, 128-token output cap, concurrency two.
- Runtime diagnostic: `gpu_memory_utilization=0.72`.
- Startup-reported KV cache: 13.63 GiB.
- Weight quantization and KV dtype: **Not recorded** in the retained dossier
  evidence.

### Public reconstruction and conflict

The public [Compose service](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.yml)
and [serve-recipe registry](https://github.com/fakoli/anvil-serving/blob/main/configs/serve-recipes.toml) reconstruct the
managed image and reservations. The Compose service now specifies
`--gpu-memory-utilization 0.2511`, which does not match the retained runtime
diagnostic's `0.72`. Until a dated record reconciles those values, use `0.72`
only to describe the measured run and `0.2511` only to describe the later
tracked reconstruction.

## Evidence by measurement class

### Functional

- Text smoke, JSON, and 4K retrieval: pass.
- Image understanding and OCR: pass.
- OpenAI-compatible audio input: HTTP 200 and bounded word recognition; not an
  STT-quality result.
- Dedicated Parakeet/Kokoro round trip: 0.0 WER, 710.68 ms total.

### Capacity and performance

- Capacity probe: 6/6 at concurrency two.
- TTFT p50/p95: 0.04/0.06 seconds.
- End-to-end p50/p95: 0.32/0.33 seconds.
- Aggregate output throughput: 243 tok/s.

### Evidence

The [dated finding](../../findings/2026-07-27-omni-voice-stack-qualification.md)
links the text, multimodal, audio, runtime, capacity, and voice artifacts.

## Decision and promotion state

### Retained

- `challenger` and operator-selectable smaller co-resident shape.

### Not authorized

- `no-promotion`.
- The bounded run does not establish general model or audio quality and does
  not imply a current live route.

## Failures and gotchas

### Resolved packaging failure

The stock image lacked `vllm[audio]`; the managed derivative added pinned
audio packages and passed the same audio-input request.

### Audio and runtime limitations

- The audio response was repetitive and noisy; STT quality was **not tested**.
- vLLM warned that the missing external `flash_attn` package could make the
  audio tower differ from Transformers.
- Transformers reported audio, vision, and TTS token IDs outside its validated
  vocabulary range.
- The derived image digest is **not retained**.
- The measured-versus-tracked GPU-memory-utilization conflict remains
  unresolved.

## Dated run history

- [2026-07-27 small Omni plus voice](../../findings/2026-07-27-omni-voice-stack-qualification.md)
