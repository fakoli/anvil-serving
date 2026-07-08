# Voice latency final recommendation (2026-07-08)

> Status: updated after the dark-audio candidate rerun. This report supersedes
> the earlier loopback-negative-control interpretation for model-candidate
> evidence.

## Recommendation

Keep the production OpenClaw Talk fast path on the current `baseline-qwen36-27b`
model for now.

Gemma 4 is viable on the RTX 5090 fast lane with the Gemma-specific vLLM recipe,
but the measured Gemma 4 12B LLM stage did not beat the current baseline in
this single-turn voice benchmark. Gemma 4 E4B had the lowest end-to-end latency
in the sample, but it produced a much shorter reply and still needs live Talk
validation for tool use, memory, and transcript behavior before it can be a
promotion candidate.

No router policy, `[router].profile_path`, OpenClaw production model selection,
or cloud setting should change from this evidence alone. Promotion remains
explicitly human-gated through `router_promote` / `anvil-serving router promote`.

## Topology

The valid rerun used the reference model-free Mini topology:

| Host | Role |
|---|---|
| Fakoli Mini | OpenClaw Gateway plus Anvil Voice Realtime/proxy only |
| Fakoli Dark | STT/TTS endpoints, Dark audio bridge, candidate LLM serves, and router |
| Audio profile | `dark-audio` |
| Heavy card | Not used for candidate voice serves |
| Fast card | RTX 5090, voice candidate target |

Fakoli Mini must remain model-free for normal OpenClaw Talk validation and
candidate A/B. The RTX PRO 6000 heavy card is reserved for the heavy tier unless
an operator creates a separate heavy-card experiment.

## Results

Durable evidence artifacts:

| Candidate | Artifact |
|---|---|
| Baseline | `.anvil/evidence/voice-baseline-qwen36-27b-dark-audio-20260708-run1.json` |
| Qwen3 SGLang | `.anvil/evidence/voice-qwen3-32b-fp4-sglang-dark-audio-20260708-run1.json` |
| Gemma 4 12B | `.anvil/evidence/voice-gemma4-12b-it-dark-audio-20260708-run1.json` |
| Gemma 4 E4B | `.anvil/evidence/voice-gemma4-e4b-it-dark-audio-20260708-run1.json` |

| Candidate | Model | Provider | TTFA ms | Turn ms | STT ms | LLM ms | TTS ms | TTS RTF | Chunks |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline-qwen36-27b` | `fast-local` | `fast-local` | 544.21 | 629.88 | 101.99 | 272.62 | 255.26 | 0.1091 | 2 |
| `qwen3-32b-fp4-sglang` | `qwen3-32b-fp4-sglang` | `direct-sglang` | 791.82 | 874.18 | 73.28 | 584.59 | 216.32 | 0.0534 | 2 |
| `gemma4-12b-it` | `gemma4-12b-it` | `direct-vllm` | 633.99 | 760.33 | 86.68 | 439.62 | 234.02 | 0.0542 | 3 |
| `gemma4-e4b-it` | `gemma4-e4b-it` | `direct-vllm` | 506.94 | 507.50 | 68.80 | 341.96 | 96.74 | 0.0709 | 1 |

Interpretation:

- Baseline remains the best measured LLM-stage latency at `272.62 ms`.
- Gemma 4 12B is runnable on the RTX 5090 with corrected config, but its
  measured LLM stage was `439.62 ms`.
- Gemma 4 E4B had the best single end-to-end row, but the much shorter reply
  reduced TTS work; treat it as a promising scout, not a promotion result.
- Qwen3 32B FP4 under SGLang served successfully, but the measured LLM stage
  was slower than baseline.

## Gemma Configuration Finding

The earlier Gemma failure was not evidence that Gemma cannot run on the 5090.
It was a serving/config problem:

- Generic vLLM nightly failed the Gemma 4 12B model path.
- The Gemma-specific image `vllm/vllm-openai:gemma4-unified` loaded the model.
- The Gemma image's capability inspection expected `CUDA_VISIBLE_DEVICES` to be
  an integer ordinal, so Fakoli Dark uses ordinal `0` for the 5090 Gemma
  candidates and keeps the Docker device reservation on the 5090 UUID.
- `google/gemma-4-12B-it` fit on the 32 GB RTX 5090 with text-only multimodal
  limits, FP8 KV cache, `--max-model-len 16384`, and
  `--gpu-memory-utilization 0.90`.

Checked-in Gemma 4 12B flags:

```text
vllm/vllm-openai:gemma4-unified
serve google/gemma-4-12B-it
--served-model-name gemma4-12b-it
--enable-auto-tool-choice
--reasoning-parser gemma4
--tool-call-parser gemma4
--chat-template examples/tool_chat_template_gemma4.jinja
--limit-mm-per-prompt '{"image": 0, "video": 0, "audio": 0}'
--kv-cache-dtype fp8
--max-model-len 16384
--gpu-memory-utilization 0.90
```

Sources reviewed:

- vLLM Gemma 4 recipe:
  https://docs.vllm.ai/projects/recipes/en/stable/Google/Gemma4.html
- vLLM Gemma 4 12B recipe:
  https://recipes.vllm.ai/Google/gemma-4-12B-it
- Google Gemma 4 model overview:
  https://ai.google.dev/gemma/docs/core
- Google Gemma 4 12B announcement:
  https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemma-4-12b/
- vLLM issue documenting Gemma 4 prefill risks on Blackwell:
  https://github.com/vllm-project/vllm/issues/39914
- vLLM issue documenting Gemma 4 FlashInfer backend incompatibility on
  Blackwell:
  https://github.com/vllm-project/vllm/issues/40677

## Next Gates

1. Keep baseline in production.
2. If continuing Gemma, rerun `gemma4-e4b-it` and `gemma4-12b-it` with the
   final checked-in compose after the 5090 default correction.
3. Add live Talk validation for any candidate that beats baseline: tool call,
   session memory, transcript delivery, hidden prompt text, and duplicate spam
   scan.
4. Only promote through a human-approved `router_promote` packet.
