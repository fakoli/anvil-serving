# STT Model Benchmark And Candidate Recommendation

> **Status: executed on Fakoli Dark, 2026-07-08.** This report covers
> speech-to-text candidates for the OpenClaw/Anvil Voice path. Fakoli Mini was
> kept model-free. Heavy was not disrupted. Fast was stopped only while the
> RTX 5090 hosted STT candidates, then restored to healthy.

## Scope

Goal: find the best local STT model for the speech-to-speech path, with latency
weighted above accuracy once a candidate clears a basic transcript correctness
gate.

Command/resource topology:

- Command host and model host: Fakoli Dark.
- Candidate GPU: RTX 5090, UUID
  `GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1`.
- Heavy GPU: RTX PRO 6000, left running and untouched.
- Mini: OpenClaw Gateway / Anvil Voice Realtime role only; no STT/TTS/LLM model
  serve was started on Mini.
- Test audio: `.anvil/evidence/voice-samples/quick-brown-fox-kokoro-16k.wav`,
  16 kHz mono PCM, reference `the quick brown fox jumps over the lazy dog`.

## Research Inputs

Primary references used before candidate selection:

- Qwen3-ASR model family and vLLM/OpenAI transcription support:
  <https://github.com/QwenLM/Qwen3-ASR> and
  <https://huggingface.co/Qwen/Qwen3-ASR-1.7B>.
- vLLM speech-to-text API:
  <https://docs.vllm.ai/en/latest/serving/online_serving/speech_to_text/>.
- SGLang ASR/Qwen3-ASR tracking:
  <https://github.com/sgl-project/sglang/issues/22025> and
  <https://github.com/sgl-project/sglang/issues/22474>.
- NVIDIA Parakeet:
  <https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3>.
- Whisper Turbo:
  <https://huggingface.co/openai/whisper-large-v3-turbo> and
  <https://huggingface.co/RedHatAI/whisper-large-v3-turbo-FP8-dynamic>.
- Other candidates for follow-up:
  <https://huggingface.co/distil-whisper/distil-large-v3.5>,
  <https://github.com/FunAudioLLM/SenseVoice>,
  <https://huggingface.co/docs/transformers/model_doc/moonshine>, and
  <https://huggingface.co/nvidia/canary-qwen-2.5b>.
- Reddit/community priors were used only as advisory signals. They generally
  agree that Parakeet is the latency favorite and Qwen3-ASR is the main open
  accuracy challenger, but those claims were not treated as evidence without
  local runs.

No credible Gemma-branded STT candidate surfaced in this research pass. Gemma
belongs in the LLM leg of the voice cascade, not the STT model slot.

## Rubric

Hard gates:

- Must run from the anvil-serving operational surface: `models pull`,
  `serves`, and `voice stt-benchmark`.
- Must keep Mini model-free and must not use the Heavy GPU.
- Must expose an OpenAI-compatible `/v1/audio/transcriptions` endpoint or have a
  clear adapter path to one.
- Must avoid transcript hallucination/repetition on the smoke sample.
- Must reach `wer_normalized <= 0.05` on the smoke sample before being considered
  a viable Talk candidate.

Scoring priorities after the hard gates:

| Dimension | Weight | Notes |
|---|---:|---|
| Warm STT latency | 40% | Target `< 200 ms`; acceptable experimental band `< 300 ms`. |
| Accuracy | 30% | Use normalized WER for ASR content; track exact WER only for punctuation/case drift. |
| Operational fit | 20% | Managed compose entry, predictable health, no custom scripts, low startup surprises. |
| Resource/cost | 10% | 5090 VRAM/opportunity cost, cold-start tax, and whether Fast must be displaced. |

## Managed Additions

This run added a repo-native STT benchmark path:

```bash
anvil-serving voice stt-benchmark \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --sample .anvil/evidence/voice-samples/quick-brown-fox-kokoro-16k.wav \
  --reference-text "the quick brown fox jumps over the lazy dog"
```

It also added candidate overlays under `examples/voice/candidates/` and managed
serve entries in `examples/fakoli-dark/serves.toml` / compose for:

- `voice-stt-qwen3-asr-0-6b`
- `voice-stt-qwen3-asr-1-7b`
- `voice-stt-whisper-large-v3-turbo-fp8`
- `voice-stt-whisper-large-v3-turbo`

Qwen3-ASR required post-processing because the served output can include
`language English<asr_text>...`. The STT client now supports provider-neutral
`voice.stt.request_fields` for OpenAI/vLLM transcription form fields such as
`language` and `max_completion_tokens`.

## Live Results

Warm latency excludes the first request after a cold model start when a clear
warm repeat exists.

| Candidate | Endpoint model | Runtime | First request ms | Warm ms | Normalized WER | Result |
|---|---|---|---:|---:|---:|---|
| Baseline Parakeet | `tdt_ctc-110m` | existing Dark audio endpoint | 82.25 | 82.62, 89.30 | 0.0 | Pass; fastest and simplest |
| Qwen3-ASR 0.6B | `qwen3-asr-0.6b` | `qwenllm/qwen3-asr:latest` / vLLM | 33210.86 | 196.36, 278.98 | 0.0 after postprocess | Pass; viable fallback |
| Qwen3-ASR 1.7B | `qwen3-asr-1.7b` | `qwenllm/qwen3-asr:latest` / vLLM | 34538.35 | 290.59, 223.96 | 0.0 | Pass; not justified over 0.6B on this smoke |
| Whisper Turbo FP8 | `whisper-large-v3-turbo-fp8` | vLLM in Qwen ASR image | 44007.92 | 730.69, 669.57 | 1.0 | Fail; repeated hallucinated phrase |
| Whisper Turbo BF16 | `whisper-large-v3-turbo` | vLLM in Qwen ASR image | 45784.58 | 642.42, 643.63 | 1.0 | Fail; repeated hallucinated phrase |
| Whisper Turbo BF16 bounded | same | same, with `max_completion_tokens=64` | n/a | 235.39, 141.52 | 1.0 | Fail; faster but still wrong |

Evidence artifacts:

- `.anvil/evidence/stt-baseline-parakeet-tdt-ctc-110m-20260708-run1.json`
  through `run3.json`
- `.anvil/evidence/stt-qwen3-asr-0-6b-20260708-run1.json` through `run3.json`
- `.anvil/evidence/stt-qwen3-asr-1-7b-20260708-run1.json` through `run3.json`
- `.anvil/evidence/stt-whisper-large-v3-turbo-fp8-20260708-run1.json` through
  `run3.json`
- `.anvil/evidence/stt-whisper-large-v3-turbo-20260708-run1.json` through
  `run3.json`
- `.anvil/evidence/stt-whisper-large-v3-turbo-bounded-20260708-run1.json` and
  `run2.json`

## Candidate Notes

Parakeet remains the production default. It won the local latency test by a
wide margin and had zero normalized WER on the smoke sample. It also keeps the
operational surface simple because it is already the Dark-host audio endpoint
used by OpenClaw Talk.

Qwen3-ASR 0.6B is the best second choice. It cleared the smoke accuracy gate and
stays within the experimental latency band when warm. Its drawbacks are the
cold-start tax, the need to displace Fast on the 5090 during isolated tests, and
the Qwen image quirk where `CUDA_VISIBLE_DEVICES` had to use ordinal `0` even
while the Docker device reservation remains pinned to the 5090 UUID.

Qwen3-ASR 1.7B is not rejected, but it did not show a reason to prefer it over
0.6B on this sample. Keep it for a larger accuracy corpus with noisy audio,
numbers, accents, and tool-call phrases.

Whisper Turbo is rejected for the current vLLM/OpenAI transcription path. Both
the FP8 and base variants reached health and exposed `/v1/audio/transcriptions`,
but they produced repeated hallucinated text. Adding bounded decode fields
reduced latency but did not fix correctness. This does not prove Whisper itself
is bad; it means this specific vLLM serve path is not acceptable for OpenClaw
Talk without further debugging or a different adapter such as faster-whisper.

SenseVoice, Moonshine, Distil-Whisper, and Canary-Qwen remain research
candidates. They need an adapter or runtime decision before they can be compared
fairly through the same OpenAI-compatible benchmark command.

## Recommendation

Keep the current Parakeet/`tdt_ctc-110m` STT endpoint as the OpenClaw Talk
default.

Keep Qwen3-ASR 0.6B as the next managed alternative to test against real voice
samples. It is the only new candidate that cleared the correctness gate while
staying close enough to the latency target to justify more work.

Do not promote Qwen3-ASR 1.7B or Whisper Turbo from this smoke. Qwen3-ASR 1.7B
needs a harder accuracy corpus before its larger runtime footprint is justified.
Whisper Turbo needs a separate compatibility/debug task before it can be judged
as an ASR model rather than as a failed serving recipe.

Next benchmark step: build a small local corpus with at least 20 short samples:
clean speech, conversational filler, zip codes/numbers, weather/tool requests,
fast speech, mild background noise, and one long utterance. Run Parakeet,
Qwen3-ASR 0.6B, and Qwen3-ASR 1.7B through `voice stt-benchmark` and report
p50/p95 latency plus normalized WER by category.
