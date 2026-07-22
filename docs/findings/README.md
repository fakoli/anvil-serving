# Findings index

Dated evidence snapshots — benchmarks, live validations, and lab notebooks — that ground the
decisions recorded in `docs/adr/` and the PRD task history. Each file is a **point-in-time
record**, accurate as of its date and not maintained afterwards; treat the ADRs and the main
docs as the current source of truth. `.json` files are the machine-readable raw evidence
backing a companion `.md` narrative. For the current user-facing conclusions and comparable
configurations, start with [Benchmark results](../BENCHMARKS.md). Newest first.

## Policy

These findings remain public as durable evidence under
[ADR-0027](../adr/0027-public-findings-are-durable-evidence.md). A later benchmark or ADR can
supersede a recommendation, but it does not erase the historical observation or move its
load-bearing evidence into a private repository.

- Add and index a sanitized dated narrative with the exact revision/configuration, topology,
  method, evidence type, result, failures, and caveats.
- Keep only the bounded raw JSON/CSV/text needed to audit the claim. Each new raw file must be at
  most 1 MiB, and all checked-in raw evidence from one experiment, qualification, promotion, or
  evidence packet must total at most 5 MiB. Splitting paths or narratives does not reset the limit.
  Exceptions must identify the files/total bytes, justify why bounded or external evidence is
  insufficient, and name the approving reviewer.
- Absent an approved exception, put larger, binary, or high-volume evidence at an anonymously
  downloadable, immutable, non-expiring versioned/content-addressed HTTPS URL retained at least as
  long as the citation.
  Record its retention owner/term, byte size, SHA-256 digest, and provenance. Expiring CI artifacts,
  private buckets, and mutable `latest` URLs do not qualify.
- Sanitize both checked-in and externally stored evidence before publication. Never include secrets,
  credentials, private prompts, personal data, machine-local tokens, or unrelated logs in either
  location.
- Retain evidence while any public doc, ADR, benchmark table, or release note depends on it; there
  is no age-based pruning. Publish a linked erratum or superseding finding instead of silently
  overwriting a merged measurement. Corrections use a new artifact path; sensitive/legal removals
  leave a public tombstone with nonsensitive provenance when safe and lawful.
- Private notes may preserve planning history, but private-only citations cannot ground a public
  claim. Restate the claim and its auditable support publicly.

Only existing artifacts' size and format are grandfathered; sanitization, correction, and public
citation requirements still apply. Any future size cleanup is a separate reviewed migration that
must preserve public access, provenance, and content hashes.
The legacy corpus is not retroactively certified: private-only grounding is tracked by
[issue #175](https://github.com/fakoli/anvil-serving/issues/175), and machine-local paths/public
artifact access are tracked by [issue #290](https://github.com/fakoli/anvil-serving/issues/290).

| Date | File | Subject |
|------|------|---------|
| 2026-07-18 | [2026-07-18-lifecycle-aware-wsl-cache-reclaim.md](2026-07-18-lifecycle-aware-wsl-cache-reclaim.md) | Fakoli Dark managed Puzzle Heavy load: 49.9 GiB cache-growth attribution, page-cache-only reclaim, retained VRAM/health/identity/inference, and exact stopped-state restoration |
| 2026-07-18 | [2026-07-18-gpt-oss-puzzle-heavy-promotion.md](2026-07-18-gpt-oss-puzzle-heavy-promotion.md) | Pinned GPT-OSS Puzzle 88B Anvil vLLM fix, RTX PRO 6000 functional and benchmark evidence, default Heavy transition, and Gemma 4 rollback |
| 2026-07-17 | [2026-07-17-gemma4-31b-optimization.md](2026-07-17-gemma4-31b-optimization.md) | Current Google 31B QAT template, 128K baseline, native-MTP compatibility failure, and WSL2 implications |
| 2026-07-17 | [2026-07-17-gpt-oss-puzzle-qualification.md](2026-07-17-gpt-oss-puzzle-qualification.md) | GPT-OSS Puzzle 88B Anvil vLLM port and RTX PRO 6000 qualification evidence without promotion |
| 2026-07-16 | [2026-07-16-gemma4-vllm0251-wsl2-c128.md](2026-07-16-gemma4-vllm0251-wsl2-c128.md) | vLLM 0.25.1 WSL2 pinned-memory upgrade, V1/V2 Gemma 4 c1/c8/c128 retest, larger-model sweep, and corrected high-concurrency NVFP4 conclusion |
| 2026-07-16 | [2026-07-16-gemma4-unsloth-nvfp4-follow-up.md](2026-07-16-gemma4-unsloth-nvfp4-follow-up.md) | Unsloth Gemma 4 NVFP4 12B/26B-A4B/31B Fast/Heavy matrix, direct QAT speed A/B, template/tool regression, and no-promotion result |
| 2026-07-16 | [2026-07-16-gemma4-chat-template-bakeoff.md](2026-07-16-gemma4-chat-template-bakeoff.md) | July 15 Gemma 4 template matrix on RTX 5090 and PRO 6000, Fast hold, Heavy 12B W4A16 promotion, rollback proof, and raw evidence |
| 2026-07-13 | [2026-07-13-q36-pro6000-container-recipe.md](2026-07-13-q36-pro6000-container-recipe.md) | First physical RTX PRO 6000 build and characterization of the q36 engine: pinned container recipe, context matrix, MTP A/B, smoke, reasoning, and repeated MMLU-Pro evidence |
| 2026-07-13 | [2026-07-13-e4b-fast-router-promotion.md](2026-07-13-e4b-fast-router-promotion.md) | Gemma 4 E4B fast-tier router promotion, profile reseed (calibration pending), and OpenClaw harness lockstep (gpu-reservations:T007) |
| 2026-07-13 | [Gemma 4 E4B promotion evidence README](2026-07-13-gemma4-e4b-fast-promotion-evidence/README.md) | Live RTX 5090 preflight, reservation sizing, and promotion evidence inventory |
| 2026-07-13 | [2026-07-13-e4b-voice-consult-benchmark.md](2026-07-13-e4b-voice-consult-benchmark.md) | E4B-backed chat-fast voice-consult latency regression that blocked retiring the 35B baseline |
| 2026-07-13 | [2026-07-13-t011-ocr-rebalance.md](2026-07-13-t011-ocr-rebalance.md) | OCR bring-up and RTX 5090 resident-set rebalance with routed validation |
| 2026-07-13 | [2026-07-13-t013-vision.md](2026-07-13-t013-vision.md) | Vision serve/preset bring-up, first evictable reservation, routed proof, and eviction validation |
| 2026-07-13 | [2026-07-13-t015-resident-set.md](2026-07-13-t015-resident-set.md) | Live RTX 5090 full resident-set, ledger, health, and eviction-drain validation |
| 2026-07-12 | [2026-07-12-thinkingcap-heavy-promotion.md](2026-07-12-thinkingcap-heavy-promotion.md) | ThinkingCap FP8 model-aware functional/quality gates and guarded Heavy promotion with GPT-OSS rollback |
| 2026-07-12 | [2026-07-12-green-context-mps-capability.md](2026-07-12-green-context-mps-capability.md) | Read-only Green Context/MPS inspector, successful Docker Desktop prerequisite probe on the RTX 5090, and unexecuted creation plan |
| 2026-07-12 | [docker-desktop-rtx5090-prerequisite.json](2026-07-12-green-context-mps-capability-evidence/docker-desktop-rtx5090-prerequisite.json) | Raw Docker Desktop CUDA 13.1 prerequisite evidence for the UUID-selected RTX 5090; no context or workload created |
| 2026-07-12 | [2026-07-12-qwen36-protocol-v2-comparison.md](2026-07-12-qwen36-protocol-v2-comparison.md) | Repeated protocol-v2 Qwen3.6 comparison, budget audit, Unsloth NVFP4 v0.25 recipe, five-session validation, and selected resident Heavy quality challenger |
| 2026-07-12 | [2026-07-12-rtx-pro-6000-heavy-eval-v2.md](2026-07-12-rtx-pro-6000-heavy-eval-v2.md) | Repaired repeated ARC/MMLU-Pro Heavy comparison and Laguna NVFP4 vLLM/SGLang sm_120 rejection |
| 2026-07-12 | [2026-07-12-heavy-intelligence-challengers.md](2026-07-12-heavy-intelligence-challengers.md) | Mistral Small 4 and Nemotron 3 Super single-PRO-6000 Heavy gates, five-session comparison, and selected resident experiment |
| 2026-07-12 | [2026-07-12-qwen36-27b-heavy-variation-bakeoff.md](2026-07-12-qwen36-27b-heavy-variation-bakeoff.md) | Qwen3.6-27B NVFP4, official FP8, and ThinkingCap FP8 Heavy validation, five-session capacity, and selected resident candidate |
| 2026-07-12 | [2026-07-12-qwen36-27b-eval-baseline.md](2026-07-12-qwen36-27b-eval-baseline.md) | Qwen3.6-27B NVFP4+MTP current built-in eval baseline and invalid-for-ranking session-derived suite control |
| 2026-07-12 | [2026-07-12-gpt-oss-120b-deterministic-recheck.md](2026-07-12-gpt-oss-120b-deterministic-recheck.md) | GPT-OSS-120B conventional benchmark and deterministic-eval token-budget control |
| 2026-07-12 | [2026-07-12-nemotron-puzzle-recheck.md](2026-07-12-nemotron-puzzle-recheck.md) | Nemotron Puzzle 75B Heavy-candidate preflight, standard benchmark, and deterministic session-eval recheck |
| 2026-07-12 | [2026-07-12-qwen35-122b-mxfp4-benchmark.md](2026-07-12-qwen35-122b-mxfp4-benchmark.md) | Single-RTX-PRO-6000 Qwen3.5-122B MXFP4/Marlin throughput and deterministic session-eval result (do not promote) |
| 2026-07-11 | [2026-07-11-system-observability-overhead.md](2026-07-11-system-observability-overhead.md) | Strict observability overhead and benchmark-effect gate |
| 2026-07-11 | [2026-07-11-system-observability-artifact-contract.md](2026-07-11-system-observability-artifact-contract.md) | Synthetic contract validation for external raw telemetry and a sanitized manifest |
| 2026-07-10 | [2026-07-10-blackwell-local-model-bakeoff.md](2026-07-10-blackwell-local-model-bakeoff.md) | RTX PRO 6000 and RTX 5090 local-model bakeoff vs production baselines: Nemotron text/Omni, Gemma 4 31B, Ornith 35B, MiniMax M2.7 REAP, DeepSeek V4 Flash — plus the 2026-07-11 extension (Nemotron Puzzle 75B + Qwen3.6-27B with verified MTP speedups, Qwen3.5-35B and Gemma E4B on llama.cpp) |
| 2026-07-10 | [scorecard.csv](2026-07-10-blackwell-local-model-bakeoff-evidence/scorecard.csv) | Machine-readable bakeoff scorecard (per-candidate config, gates, throughput, verdict) |
| 2026-07-10 | [2026-07-10-qwen35-122b-heavy-candidate.md](2026-07-10-qwen35-122b-heavy-candidate.md) | Qwen3.5-122B-A10B-NVFP4 heavy-tier candidate (fakoli-dark) |
| 2026-07-10 | [heavy-tier-bakeoff-evidence/qwen35-122b-a10b-vllm-nvfp4-131k.bakeoff.json](heavy-tier-bakeoff-evidence/qwen35-122b-a10b-vllm-nvfp4-131k.bakeoff.json) | Raw heavy-tier bakeoff evidence — Qwen3.5-122B-A10B-NVFP4 |
| 2026-07-08 | [2026-07-08-voice-latency-final-recommendation.md](2026-07-08-voice-latency-final-recommendation.md) | Voice latency final recommendation (voice-latency-model-ab:T007) |
| 2026-07-08 | [2026-07-08-fast-tier-llm-bakeoff.md](2026-07-08-fast-tier-llm-bakeoff.md) | RTX 5090 Fast-tier candidate registry, source-backed priors, scoring rubric, and local-gate plan |
| 2026-07-08 | [2026-07-08-fast-tier-promotion.md](2026-07-08-fast-tier-promotion.md) | Human-gated Qwen3.6-35B-A3B-NVFP4 Fast-tier promotion and validation record |
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
