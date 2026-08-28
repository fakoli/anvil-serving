# Qwen3.8 27B RadixArk NVFP4 on RTX 5090

**Date:** 2026-08-17
**Decision:** `challenger`, `no-promotion`
**Evidence:** `functional`, `capacity`, bounded deterministic `quality`
**Measured hardware:** one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120
**Topology:** isolated Windows 11 / Docker Desktop / WSL2 qualification lane;
no RTX PRO 6000 was measured, protected, or involved

<!-- benchmark-result-card/v1 -->
## Result card

> RadixArk Qwen3.8 27B NVFP4 fit one RTX 5090 at 65,536 context and
> concurrency one, then passed the retained long-context, tool-call, and
> direct multimodal gates without changing any route or promotion state.

| Setup | Qualified value |
|---|---|
| Model | `RadixArk/Qwen3.8-27B-NVFP4@554ebba9b5f1b79dc11246341960360e6ef05ef4`, served as `qwen38-27b-radixark-nvfp4-sglang-rtx5090-64k-mm` |
| Hardware | one NVIDIA GeForce RTX 5090, 32,607 MiB, sm_120; isolated Windows 11 / Docker Desktop / WSL2 lane |
| Runtime | SGLang `c4271c3fe1262fc2adbd162c33b25de5255251c5`; image `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`; ModelOpt NVFP4/FP8 weights, FP8 E4M3 KV, MTP disabled |
| Recipe | [managed 64K multimodal recipe at `85b21147`](https://github.com/fakoli/anvil-serving/blob/85b2114745a1a66f30252876ab528d49f12e8a73/configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-64k-mm-recipe.toml) |
| Measurement path | direct online managed endpoint; retained artifacts do not classify individual requests as cold or warm |
| Contract | 65,536 context, c1, 512-token multimodal output cap, up to four images per corpus request, one video per corpus case, thinking disabled |
| Evidence | `functional`, `capacity`, bounded deterministic `quality`; retained qualification complete |
| Decision | `challenger`, `no-promotion`; no route, deployment, or live serving state changed |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Long-context retrieval and tools | pass; 20/20 tools | approximately 60,000 prompt tokens, exact marker, `stop`; c1 |
| Deterministic multimodal corpus | 30/30 | image 12/12, mixed 4/4, video 14/14; c1 |
| Direct modality latency p50 | 0.886 / 1.548 / 1.648 s | image / mixed / video across the 30-attempt corpus |
| Untested boundary | no result | controlled decode rate, c2+, routed acceptance, or action loop |

**Why it matters:** This is a reproducible single-card local perception,
long-context, and tool-formatting challenger with an exact managed rollback
surface.

**Important caveat:** The retained run is direct and concurrency one; it does
not establish controlled decode throughput, routed media admission, broad GUI
grounding, or end-to-end computer action.

[Evidence manifest](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-evidence/README.md)
· [Publication summary](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-evidence/publication-summary.md)

## Outcome

The digest-pinned RadixArk Qwen3.8 27B ModelOpt NVFP4 checkpoint fits a single
RTX 5090 and is the preferred locally proven 5090 computer-use perception
candidate. It passed text, structured output, a 60K retrieval needle, 20/20
tool calls, direct image/OCR/video preflight, and the complete deterministic
multimodal corpus at 30/30. The corpus includes temporal order, state change,
event localization, video OCR, 120-second continuity, multi-image comparison,
mixed video-plus-image requests, and two supplementary real-world clips.

This result does not promote or route the model. It establishes a reproducible
single-card challenger and closes the need to download the weaker llama.cpp
frame-sampling fallback for this qualification round.

## Immutable identity and recipe

- Model: `RadixArk/Qwen3.8-27B-NVFP4` at
  `554ebba9b5f1b79dc11246341960360e6ef05ef4`.
- Runtime: `lmsysorg/sglang:qwen38-27b` at image digest
  `sha256:506525a5907ea22c9d445afb7c03603959b912de034d86915cf17da814f1a124`.
- SGLang source revision from the image label:
  `c4271c3fe1262fc2adbd162c33b25de5255251c5`.
- FlashInfer build: `0.6.18.dev20260807`, source revision
  `906181e3f4cf4bcc81835fb480db4011bbd80b62`.
- Recipe:
  `configs/qwen38-27b-radixark-nvfp4-sglang-rtx5090-64k-mm-recipe.toml`.
- Weight format: ModelOpt mixed precision, NVFP4 dense MLP paths, FP8
  projections/attention paths, and BF16 vision/MTP tensors.
- KV: FP8 E4M3. Context: 65,536. Concurrency: one. MTP: disabled for the
  stability baseline. Multimodal feature transport: CPU.
- Cache snapshot: 21,945,295,265 logical bytes, zero incomplete files, zero
  broken links, and only the requested revision.

The managed load reported 30.08 GB available before weights, 20.14 GB used by
the model, 9.94 GB available after weights, and 3.73 GB available after KV,
eager buffers, and the decode CUDA graph. The engine allocated 167,789 KV
tokens even though the admission profile is capped at 65,536.

## Gates and measurements

| Gate | Result |
|---|---|
| Coding and structured JSON | pass |
| Retrieval needle | pass at approximately 60,000 prompt tokens; exact marker returned with `stop` |
| Tool calls | 20/20 valid calls with `tool_calls` finishes |
| Thinking contract | explicitly disabled; no reasoning-channel output observed |
| Direct image understanding | pass with required text and `stop` |
| Direct verbatim OCR | pass with `ANVIL READY`, `42917`, and `42` |
| Direct MP4 video | pass with ordered `red` then `green` |
| Complete multimodal corpus | 30/30; image 12/12, mixed 4/4, video 14/14 |

The complete corpus ran at concurrency one with two repetitions per case, a
512-token output cap, thinking disabled, and only `stop` accepted. Aggregate
latencies were 0.886 seconds image p50 / 1.977 p95, 1.548 seconds mixed p50 /
2.232 p95, and 1.648 seconds video p50. Video p95 was 142.051 seconds because
the two supplementary real-world clips generated long descriptive answers;
the five deterministic synthetic video cases each remained at or below 2.097
seconds p95.

Raw, sanitized evidence:

- [functional, 60K, and tools preflight](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-evidence/preflight-functional-60k.json)
- [direct image/OCR/video preflight](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-evidence/preflight-multimodal.json)
- [30-request deterministic multimodal corpus](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-evidence/multimodal-c1.json)
- [source registry](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-evidence/source-registry.json)

## Failures and caveats

1. The first generic preflight used its default approximately 128K needle and
   correctly received HTTP 400 from the deliberately 65,536-token profile.
   The declared-profile reruns passed at 48K and 60K. This is retained as a
   harness/configuration mismatch, not a model failure.
2. The first direct multimodal preflight used a 256-token visible-output cap.
   The required image content was present, but the descriptive answer ended
   with `length`; the bounded 512-token rerun ended with `stop` and passed.
3. SGLang warned that FP8 KV scaling factors were absent and defaulted to
   1.0. The deterministic gates passed, but equivalence to unquantized KV is
   not established.
4. The generic `eval benchmark evidence show` command does not recognize the
   multimodal evidence schema. The artifact was inspected directly and the
   product gap is recorded in a repository ticket.
5. No controlled text decode-rate benchmark, concurrency above one, routed
   acceptance, broad GUI-grounding benchmark, or computer-action loop was run.
   The result proves perception, temporal video handling, long-context
   retrieval, and tool-call formatting—not end-to-end autonomous computer use.

## LFM2.5-VL and GGUF fallback comparison

`LiquidAI/LFM2.5-VL-1.6B@919fde3d022e3f90a4716006f993938ee8c2eb97`
is a strong small-model companion: its official card recommends it for
general vision, OCR, and document comprehension, and the official WebGPU demo
processes live video frames locally. Its 1.6B size makes it attractive as an
always-on screen watcher, frame captioner, OCR prefilter, or structured event
extractor. It was not locally benchmarked in this run.

It is not the primary recommendation for this goal. Its documented context is
32,768 tokens, its published video demo processes frames, and the reviewed
official material does not establish native temporal video-file reasoning,
GUI grounding, or computer-action reliability. Qwen3.8 NVFP4 directly accepted
MP4 input here, passed temporal and continuity assertions, and retained a 64K
served window.

The prepared fallback was `unsloth/Qwen3.8-27B-GGUF` at
`f1bfb127c64f7072bdd2cad55f258b9c8b2910fe` with Q4/UD-Q4 weights and an
F16/BF16 multimodal projector. Current llama.cpp multimodal documentation
supports image and experimental audio through `libmtmd`, not native video;
that path would require explicit frame extraction and multi-image prompting.
Because the native SGLang NVFP4 candidate passed, the fallback was not pulled
or locally measured.

## Decision boundary

Retain the exact recipe as the preferred RTX 5090 multimodal challenger and
keep the checkpoint cached. Do not route or promote it without a separate
human gate, routed admission limits, client acceptance, GUI-grounding/action
evaluation, and a decision on the FP8-KV scale warning. LFM2.5-VL remains a
promising lightweight auxiliary model, not a replacement for this native
video-capable reasoning lane.
