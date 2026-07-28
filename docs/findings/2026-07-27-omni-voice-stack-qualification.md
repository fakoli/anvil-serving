# Small Omni plus voice stack qualification

**Point-in-time record, 2026-07-27.** The Fakoli Dark RTX 5090 can run a
smaller multimodal model together with the dedicated Parakeet STT and Kokoro
TTS services. This is an operator-selectable alternative to the exclusive
30B Nemotron Omni stack. It does not promote the smaller model into the router.

## Selection and model identity

`omni-voice-stack` resolves to `omni-small`, `stt`, and `tts`.
`omni-small-stack` selects only the smaller model, while `voice` retains the
independent audio lifecycle. The optional embeddings/reranker stack remains
separate and is not pulled into either Omni choice.

| Field | Value |
|---|---|
| Checkpoint | `Qwen/Qwen2.5-Omni-3B` |
| Revision | `f75b40e3da2003cdd6e1829b1f420ca70797c34e` |
| Served name | `qwen25-omni-3b` |
| Managed serve | `omni-small`, container `vllm-qwen25-omni-3b`, port `30013` |
| Image | `anvil-vllm:omni-small-audio-a65f93fb2` |
| Hardware | NVIDIA GeForce RTX 5090, 32,607 MiB |
| Declared reservation | 24,576 MiB |
| Voice reservations | 2,048 MiB STT + 2,048 MiB TTS |
| System/display reserve | 3,072 MiB |
| Admission headroom | 863 MiB |

The local image derives from the same pinned vLLM nightly digest as the large
Omni recipe and adds pinned audio runtime packages. The stock image passed
text, image, and OCR but returned HTTP 500 with
`Please install vllm[audio] for audio support` for audio input; rebuilding
through the managed Compose service corrected that packaging gap.

The post-fix managed container logs exposed two additional caveats. vLLM
reported that the external `flash_attn` package was unavailable and warned that
audio-tower results might not exactly match Transformers. Transformers also
reported several audio, vision, and TTS token IDs outside a validated
vocabulary range. The installed image contains the pinned audio I/O packages
but no `flash-attn` distribution. These warnings did not prevent the bounded
gates from passing, but they are a plausible contributor to the noisy audio
response and block any stronger audio-quality or promotion claim. Installing
FlashAttention blindly is not an accepted fix: its published CUDA support
matrix must first be reconciled with the RTX 5090/Blackwell runtime.

## Live results

With Qwen and both voice services resident, `nvidia-smi` reported 28,743 MiB
used and 3,864 MiB free. The measured display baseline was 1,229 MiB; display
plus the warmed voice pair was 4,295 MiB. Subtracting that baseline gives
approximately 24,448 MiB for the Qwen allocation, rounded up to the declared
24,576 MiB reservation. Container system-memory use at observation time was
4.759 GiB for Qwen, 1.546 GiB for STT, and 1.764 GiB for TTS.

Text smoke, JSON, and 4K retrieval passed. Image understanding and OCR passed
against the existing bounded screenshot fixture. The capacity probe completed
6/6 requests at concurrency two with a 2,048-token prompt and 128-token output
cap: TTFT p50/p95 was 0.04/0.06 seconds, end-to-end p50/p95 was 0.32/0.33
seconds, and aggregate output throughput was 243 tok/s.

An OpenAI-compatible `audio_url` request returned HTTP 200 and recognized
“Mary” and “lamb,” establishing that the Omni input path works. Its response
was repetitive and not suitable as a transcription-quality claim. The
co-resident dedicated voice round trip passed with 0.0 WER: 710.68 ms total,
421.41 ms STT, 289.27 ms TTS, and TTS RTF 0.1006. Parakeet and Kokoro therefore
remain the production speech endpoints.

Raw evidence:

- [Text preflight](2026-07-27-omni-stack-evidence/qwen25-omni-small-text-preflight.json)
- [Image and OCR preflight](2026-07-27-omni-stack-evidence/qwen25-omni-small-multimodal-preflight.json)
- [Audio input probe](2026-07-27-omni-stack-evidence/qwen25-omni-small-audio-input.json)
- [Runtime diagnostics](2026-07-27-omni-stack-evidence/qwen25-omni-small-runtime-diagnostics.json)
- [Capacity probe](2026-07-27-omni-stack-evidence/qwen25-omni-small-capacity.json)
- [Dedicated voice round trip](2026-07-27-omni-stack-evidence/qwen25-omni-small-voice-audio.json)

## Gemma result and decision

Gemma 3n E2B was the preferred smaller candidate because Google documents
text, image, audio, and video inputs with text output. The managed pull failed
before model load with Hugging Face `403 GatedRepoError`: the authenticated
account was not in the checkpoint's authorized list. No Gemma weights were
loaded and no Gemma RAM or performance claim is made. The pinned Gemma recipe
is retained as `unverified` so it can be retried after accepting the license.

A second bounded pull requested only the pinned revision's `config.json`, read
the user-local credential file by path, and forwarded the credential to the
download container by environment reference. The product space gate passed,
then the Hugging Face CLI returned exactly
`Error: Access denied. This repository requires approval.` This confirms an
account authorization/license blocker rather than a missing credential,
container startup defect, GPU problem, or full-checkpoint storage failure.

- [Gemma access diagnostic](2026-07-27-omni-stack-evidence/gemma3n-access-diagnostic.json)

Qwen2.5-Omni-3B is the currently runnable small-Omni choice. Its official model
card documents text, image, audio, and video input, while vLLM documents the
Thinker path used here. This local run qualifies only the bounded text,
image/OCR, audio-input, capacity, and co-resident voice behaviors recorded
above. The runtime warnings require upstream compatibility review. Router
promotion and general model-quality claims remain human-gated.

## Operator boundary

Use `serves up --group omni-voice-stack` to select the co-resident stack.
Use `serves down --group omni-voice-stack` before selecting the exclusive
`omni-stack`. The global reservation ledger now reads every colocated
`serves*.toml`, so main serving, voice, and ComfyUI lifecycle commands cannot
admit against incomplete GPU state.
