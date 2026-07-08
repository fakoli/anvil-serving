# OpenClaw Talk voice latency A/B report (2026-07-08)

> Status: updated after the corrected dark-audio candidate rerun. This report
> records the final state of the voice-latency model A/B workstream for
> 2026-07-08. It does not promote a model or change router policy.

## Executive Determination

Keep the current production fast Talk model in place.

The corrected A/B run proved that Gemma 4 candidates can run on the RTX 5090
fast lane when served with the Gemma-specific vLLM image and bounded text-only
memory settings. The measured Gemma 4 12B row did not beat the production
baseline LLM latency. Gemma 4 E4B produced the fastest single end-to-end sample,
but the reply was shorter and still needs live Talk/tool validation before it
can be considered for promotion.

The RTX PRO 6000 heavy card was not used for these voice candidates and should
remain reserved for the heavy tier. Fakoli Mini remains model-free for reference
OpenClaw Talk validation.

## Current Reference Topology

| Host | Owns |
|---|---|
| Fakoli Mini | OpenClaw Gateway and Anvil Voice Realtime/proxy only |
| Fakoli Dark | Router at `http://100.87.34.66:8000/v1`, candidate LLM serves, STT/TTS endpoints, and Dark audio bridge |
| Fast GPU | RTX 5090 for production fast tier and voice LLM candidates |
| Heavy GPU | RTX PRO 6000 for heavy tier only |

`mini-audio` is still an explicit optional same-host/local-audio mode, but it is
not the reference OpenClaw Talk or candidate benchmark topology.

## What Changed During The Investigation

The first candidate matrix contained failed rows from a non-gateway checkout
that called its own `127.0.0.1` audio ports. Those rows were topology negative
controls, not model results.

The corrected run used `dark-audio`: Mini remained model-free, STT/TTS stayed on
Fakoli Dark, and only the LLM candidate changed via direct endpoint overlays.

Gemma 4 12B initially failed under a generic vLLM nightly path. The working
configuration used:

- `vllm/vllm-openai:gemma4-unified`
- `google/gemma-4-12B-it`
- `--enable-auto-tool-choice`
- `--reasoning-parser gemma4`
- `--tool-call-parser gemma4`
- `--chat-template examples/tool_chat_template_gemma4.jinja`
- `--limit-mm-per-prompt '{"image": 0, "video": 0, "audio": 0}'`
- `--kv-cache-dtype fp8`
- `--max-model-len 16384`
- `--gpu-memory-utilization 0.90`

The Gemma vLLM image also required `CUDA_VISIBLE_DEVICES=0` on Fakoli Dark
rather than a GPU UUID for its capability-inspection subprocess. The compose
file now keeps Gemma 12B on the 5090 by default instead of defaulting its device
reservation to the heavy card.

## Timing Results

| Candidate | Model | Provider | TTFA ms | Turn ms | STT ms | LLM ms | TTS ms | TTS RTF | Chunks |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| `baseline-qwen36-27b` | `fast-local` | `fast-local` | 544.21 | 629.88 | 101.99 | 272.62 | 255.26 | 0.1091 | 2 |
| `qwen3-32b-fp4-sglang` | `qwen3-32b-fp4-sglang` | `direct-sglang` | 791.82 | 874.18 | 73.28 | 584.59 | 216.32 | 0.0534 | 2 |
| `gemma4-12b-it` | `gemma4-12b-it` | `direct-vllm` | 633.99 | 760.33 | 86.68 | 439.62 | 234.02 | 0.0542 | 3 |
| `gemma4-e4b-it` | `gemma4-e4b-it` | `direct-vllm` | 506.94 | 507.50 | 68.80 | 341.96 | 96.74 | 0.0709 | 1 |

## Pass / Fail

| Gate | Verdict | Reason |
|---|---|---|
| Correct topology | Pass | Candidate rerun used Dark-host audio with Mini model-free |
| Heavy-card guardrail | Pass | Candidate services default to RTX 5090 after compose correction |
| Gemma 4 12B serve viability | Pass | Completed a voice turn on the 5090 with Gemma-specific vLLM config |
| Current baseline latency | Pass | Baseline LLM stage remains best measured at `272.62 ms` |
| Candidate promotion readiness | Fail / not ready | No candidate has repeated Talk/tool/session validation plus better comparable latency |
| Cost-control gate | Pass | No cloud path or metered promotion was introduced |

## Final Decision

Do not promote a voice LLM candidate from this run.

Recommended next order:

1. Keep `baseline-qwen36-27b` in production.
2. Repeat Gemma 4 E4B and Gemma 4 12B with the final checked-in 5090 defaults.
3. If E4B remains fastest on a broader prompt set, run live OpenClaw Talk
   validation for tool calls, session memory, transcript delivery, hidden prompt
   pollution, and duplicate-message behavior.
4. Only promote through a human-approved `router_promote` workflow.

## Source Evidence

- `.anvil/evidence/voice-baseline-qwen36-27b-dark-audio-20260708-run1.json`
- `.anvil/evidence/voice-qwen3-32b-fp4-sglang-dark-audio-20260708-run1.json`
- `.anvil/evidence/voice-gemma4-12b-it-dark-audio-20260708-run1.json`
- `.anvil/evidence/voice-gemma4-e4b-it-dark-audio-20260708-run1.json`
- `docs/findings/2026-07-08-voice-latency-candidate-matrix.md`
- `docs/findings/2026-07-08-voice-latency-final-recommendation.md`
