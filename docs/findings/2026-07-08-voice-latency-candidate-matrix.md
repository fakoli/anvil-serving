# Voice latency candidate benchmark matrix (2026-07-08)

> Status: updated after the dark-audio rerun. This matrix records comparable
> voice benchmark rows for the production baseline and three candidate LLM
> serves. It is not promotion evidence by itself.

## Scope

The matrix uses `examples/voice/openclaw-anvil-voice.toml` with the
`dark-audio` profile so Fakoli Mini remains model-free. Candidate rows change
only the LLM endpoint by overlay; STT/TTS stay on Fakoli Dark through the same
audio topology.

The older non-gateway loopback rows remain useful as topology negative controls,
but they are superseded for model comparison by the successful dark-audio rows
below.

## Serve Matrix

| Candidate | Serve | Endpoint | Engine | GPU target | Status |
|---|---|---|---|---|---|
| `baseline-qwen36-27b` | `fast` | `http://100.87.34.66:8000/v1` | Router to vLLM fast tier | RTX 5090 | Measured |
| `qwen3-32b-fp4-sglang` | `voice-qwen3-32b-sglang` | `http://100.87.34.66:39003/v1` | SGLang | RTX 5090 | Measured |
| `gemma4-12b-it` | `voice-gemma4-12b` | `http://100.87.34.66:39001/v1` | vLLM Gemma image | RTX 5090 | Measured |
| `gemma4-e4b-it` | `voice-gemma4-e4b` | `http://100.87.34.66:39002/v1` | vLLM Gemma image | RTX 5090 | Measured |

The `voice-qwen3-32b` vLLM NVFP4 service remains a retained failed serve probe:
it failed with a vLLM loader error before producing voice latency data. Do not
count it as a measured row.

## Timing Results

| Candidate | Model | Provider | TTFA ms | Turn ms | STT ms | LLM ms | TTS ms | TTS RTF | Chunks | Reply |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `baseline-qwen36-27b` | `fast-local` | `fast-local` | 544.21 | 629.88 | 101.99 | 272.62 | 255.26 | 0.1091 | 2 | Hello! How can I help you today? |
| `qwen3-32b-fp4-sglang` | `qwen3-32b-fp4-sglang` | `direct-sglang` | 791.82 | 874.18 | 73.28 | 584.59 | 216.32 | 0.0534 | 2 | Hello, I'm Anvil Voice from OpenClaw. How can I assist you? |
| `gemma4-12b-it` | `gemma4-12b-it` | `direct-vllm` | 633.99 | 760.33 | 86.68 | 439.62 | 234.02 | 0.0542 | 3 | System online. I am Anvil Voice. How can I assist you? |
| `gemma4-e4b-it` | `gemma4-e4b-it` | `direct-vllm` | 506.94 | 507.50 | 68.80 | 341.96 | 96.74 | 0.0709 | 1 | How can I help you? |

## Interpretation

The current production baseline remains the safest default because it has the
lowest measured LLM-stage latency and it is already integrated into the Talk
tool/session path.

Gemma 4 12B should be treated as viable on the 5090, not failed. The corrected
serving recipe loaded and completed a voice turn. It did not beat baseline in
the measured LLM stage.

Gemma 4 E4B is the best latency scout from this sample. Its reply was much
shorter, so the lower TTS stage is not directly comparable to the longer
baseline and 12B replies. It needs a repeated prompt set plus live Talk tool
validation before promotion.

Qwen3 32B FP4 under SGLang is operational but slower in this sample. It remains
useful as a serving-engine comparison and a path for future Qwen FP4/FP8 tests.

## Evidence

| Candidate | Evidence file |
|---|---|
| Baseline | `.anvil/evidence/voice-baseline-qwen36-27b-dark-audio-20260708-run1.json` |
| Qwen3 SGLang | `.anvil/evidence/voice-qwen3-32b-fp4-sglang-dark-audio-20260708-run1.json` |
| Gemma 4 12B | `.anvil/evidence/voice-gemma4-12b-it-dark-audio-20260708-run1.json` |
| Gemma 4 E4B | `.anvil/evidence/voice-gemma4-e4b-it-dark-audio-20260708-run1.json` |

## Promotion Gate

Do not promote from this matrix alone. A promotion candidate needs:

1. Repeated comparable voice benchmark rows on the same `dark-audio` or
   `mini-dark-audio-proxy` topology.
2. Live OpenClaw Talk validation for memory, tool calls, transcript delivery,
   hidden prompt text, and duplicate-message behavior.
3. A human-approved `router_promote` packet.
