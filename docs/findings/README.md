# Findings index

Dated evidence snapshots — benchmarks, live validations, and lab notebooks — that ground the
decisions recorded in `docs/adr/` and the PRD task history. Each file is a **point-in-time
record**, accurate as of its date and not maintained afterwards; treat the ADRs and the main
docs as the current source of truth. `.json` files are the machine-readable raw evidence
backing a companion `.md` narrative. For the current user-facing conclusions and comparable
configurations, start with [Benchmark results](../BENCHMARKS.md). Newest first.

| Date | File | Subject |
|------|------|---------|
| 2026-07-11 | [2026-07-11-system-observability-overhead.md](2026-07-11-system-observability-overhead.md) | Strict observability overhead and benchmark-effect gate |
| 2026-07-11 | [2026-07-11-system-observability-artifact-contract.md](2026-07-11-system-observability-artifact-contract.md) | Synthetic contract validation for external raw telemetry and a sanitized manifest |
| 2026-07-10 | [2026-07-10-blackwell-local-model-bakeoff.md](2026-07-10-blackwell-local-model-bakeoff.md) | RTX PRO 6000 and RTX 5090 local-model bakeoff vs production baselines: Nemotron text/Omni, Gemma 4 31B, Ornith 35B, MiniMax M2.7 REAP, DeepSeek V4 Flash — plus the 2026-07-11 extension (Nemotron Puzzle 75B + Qwen3.6-27B with verified MTP speedups, Qwen3.5-35B and Gemma E4B on llama.cpp) |
| 2026-07-10 | [scorecard.csv](2026-07-10-blackwell-local-model-bakeoff-evidence/scorecard.csv) | Machine-readable bakeoff scorecard (per-candidate config, gates, throughput, verdict) |
| 2026-07-08 | [2026-07-08-voice-latency-final-recommendation.md](2026-07-08-voice-latency-final-recommendation.md) | Voice latency final recommendation (voice-latency-model-ab:T007) |
| 2026-07-08 | [2026-07-08-stt-model-benchmark.md](2026-07-08-stt-model-benchmark.md) | Dark-host STT benchmark: Parakeet, Qwen3-ASR, and Whisper Turbo |
| 2026-07-08 | [2026-07-08-voice-latency-ab-final-report.md](2026-07-08-voice-latency-ab-final-report.md) | OpenClaw Talk voice latency candidate A/B status report (evidence synthesis) |
| 2026-07-08 | [2026-07-08-voice-latency-candidate-matrix.md](2026-07-08-voice-latency-candidate-matrix.md) | Voice latency candidate benchmark matrix (T005) |
| 2026-07-08 | [2026-07-08-openclaw-talk-live-validation.md](2026-07-08-openclaw-talk-live-validation.md) | OpenClaw Talk live validation evidence (T006) |
| 2026-07-07 | [2026-07-07-voice-latency-model-shortlist.md](2026-07-07-voice-latency-model-shortlist.md) | Voice LLM candidate shortlist for OpenClaw Talk latency (T002) |
| 2026-07-07 | [2026-07-07-voice-latency-baseline.md](2026-07-07-voice-latency-baseline.md) | Anvil Voice latency baseline for OpenClaw Talk (T001) |
| 2026-07-07 | [2026-07-07-openclaw-colo-interaction-benchmark.md](2026-07-07-openclaw-colo-interaction-benchmark.md) | OpenClaw COLO interaction benchmark — live pass from the Fakoli Mini gateway |
| 2026-07-07 | [2026-07-07-anvil-score-prd-scope-gap.md](2026-07-07-anvil-score-prd-scope-gap.md) | Anvil `score --prd` scope gap, confirmed in Anvil 0.4.2 |
| 2026-07-06 | [2026-07-06-openclaw-workbench-skill-smoke.md](2026-07-06-openclaw-workbench-skill-smoke.md) | Live Fakoli Mini smoke check for the workbench skill |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-option-live.md](2026-07-openclaw-anvil-voice-option-live.md) | OpenClaw Anvil Voice live validation |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-option-live.json](2026-07-openclaw-anvil-voice-option-live.json) | Raw T008 live-validation result record (pass, Fakoli Mini) |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-option.md](2026-07-openclaw-anvil-voice-option.md) | OpenClaw Anvil Voice option discovery |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-gateway-smoke.json](2026-07-openclaw-anvil-voice-gateway-smoke.json) | Raw T008 gateway smoke run output (temporary Mini gateway) |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-gateway-status.json](2026-07-openclaw-anvil-voice-gateway-status.json) | OpenClaw gateway/service status snapshot (Fakoli Mini) |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-mini-validation.json](2026-07-openclaw-anvil-voice-mini-validation.json) | Fakoli Mini host-identity validation snapshot |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-plugin-inspect.json](2026-07-openclaw-anvil-voice-plugin-inspect.json) | Anvil Voice plugin runtime inspect output |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-realtime-process.json](2026-07-openclaw-anvil-voice-realtime-process.json) | Mini realtime/audio server process listing |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-talk-catalog.json](2026-07-openclaw-anvil-voice-talk-catalog.json) | OpenClaw Talk modes/transports/providers capability catalog |
| 2026-07-06 | [2026-07-openclaw-anvil-voice-talk-config.json](2026-07-openclaw-anvil-voice-talk-config.json) | OpenClaw Talk config snapshot (anvil realtime provider) |
| 2026-07-06 | [2026-07-voice-tts-ab.md](2026-07-voice-tts-ab.md) | Voice TTS candidate preflight: Kokoro-82M, Orpheus-3B, Qwen3-TTS (T009) |
| 2026-07-06 | [2026-07-voice-tts-ab.json](2026-07-voice-tts-ab.json) | Raw TTS A/B measurements |
| 2026-07-06 | [tts-ab-kokoro-5090-20260706.json](tts-ab-kokoro-5090-20260706.json) | Kokoro TTS benchmark run on the RTX 5090 |
| 2026-07-06 | [tts-ab-kokoro-current-20260706.json](tts-ab-kokoro-current-20260706.json) | Kokoro TTS benchmark run, current serve config |
| 2026-07-06 | [tts-ab-orpheus-current-20260706.json](tts-ab-orpheus-current-20260706.json) | Orpheus-3B TTS benchmark run |
| 2026-07-06 | [tts-ab-qwen3-current-20260706.json](tts-ab-qwen3-current-20260706.json) | Qwen3-TTS benchmark run |
| 2026-07-05 | [2026-07-voice-stt-ab.md](2026-07-voice-stt-ab.md) | Voice STT A/B: parakeet.cpp vs vLLM-served Whisper (fakoli-dark) |
| 2026-07-05 | [stt-ab-live-20260705.json](stt-ab-live-20260705.json) | Raw STT A/B live run (cold) |
| 2026-07-05 | [stt-ab-live-warm-20260705.json](stt-ab-live-warm-20260705.json) | Raw STT A/B live run (warm) |
| 2026-07-04 | [2026-07-04-openclaw-keyless-failover.md](2026-07-04-openclaw-keyless-failover.md) | OpenClaw keyless failover: does the exhaustion-503 hand off to the native subscription? (T005) |
| 2026-07-04 | [2026-07-04-hf-speech-to-speech-review.md](2026-07-04-hf-speech-to-speech-review.md) | Architecture review of `huggingface/speech-to-speech` (voice-pipeline PRD input) |
| 2026-07-04 | [2026-07-04-voice-pipeline-v1-status.md](2026-07-04-voice-pipeline-v1-status.md) | voice-pipeline v1 build status and pre-bring-up punch list |
| 2026-07 | [2026-07-voice-independent-verification.md](2026-07-voice-independent-verification.md) | Voice pipeline independent verification gate (T017, passed) |
| 2026-07 | [2026-07-voice-local-loop-proof.md](2026-07-voice-local-loop-proof.md) | Voice local loop proof: mic → VAD → STT → anvil LLM → TTS → speakers (T010) |
| 2026-07 | [2026-07-voice-realtime-proof.md](2026-07-voice-realtime-proof.md) | Voice Realtime proof: official `openai` SDK client against the anvil Realtime server |
| 2026-07 | [2026-07-voice-16gb-mini.md](2026-07-voice-16gb-mini.md) | Voice on a 16 GB Mini: local STT+TTS with the LLM routed to fakoli-dark (T016) |
| 2026-07 | [2026-07-voice-16gb-mini.json](2026-07-voice-16gb-mini.json) | Raw evidence for the 16 GB Mini proof |
| (running) | [blackwell-sm120-lab-notebook.md](blackwell-sm120-lab-notebook.md) | Blackwell sm_120 lab notebook: which models serve (and how) on fakoli-dark |
