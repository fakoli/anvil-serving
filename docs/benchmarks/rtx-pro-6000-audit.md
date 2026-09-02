# RTX PRO 6000 mention audit

This audit classifies every tracked Markdown file containing an RTX PRO 6000
name at the 2026-09-02 review. Evidence-directory Markdown is classified with
its own file, not inherited implicitly from the parent finding. This is a
coverage control; benchmark results remain in the [run catalog](runs.md).

## `measured-on`

`docs/BENCHMARKS.md`; `docs/benchmarks/comparison.md`;
`docs/benchmarks/configurations.md`;
`docs/benchmarks/gpt-oss-puzzle-88b-recipe.md`;
`docs/benchmarks/hardware/rtx-pro-6000.md`; `docs/benchmarks/index.md`;
`docs/benchmarks/methodology.md`; `docs/benchmarks/models.md`;
`docs/benchmarks/models/agents-a1.md`;
`docs/benchmarks/models/deepseek-v4-flash.md`;
`docs/benchmarks/models/gemma-4.md`;
`docs/benchmarks/models/gpt-oss-120b.md`;
`docs/benchmarks/models/gpt-oss-puzzle-88b.md`;
`docs/benchmarks/models/glm53-flash.md`;
`docs/benchmarks/models/inkling-small.md`;
`docs/benchmarks/models/index.md`;
`docs/benchmarks/models/laguna-s-2.1.md`;
`docs/benchmarks/models/minimax-m27-reap.md`;
`docs/benchmarks/models/mistral-small-4.md`;
`docs/benchmarks/models/nemotron-puzzle-75b.md`;
`docs/benchmarks/models/nemotron3-super-120b.md`;
`docs/benchmarks/models/ornith-35b.md`;
`docs/benchmarks/models/qwen35-122b.md`;
`docs/benchmarks/models/qwen36-27b.md`;
`docs/benchmarks/models/qwen38-27b.md`;
`docs/benchmarks/models/qwen38-flash-next.md`; `docs/benchmarks/runs.md`;
`docs/findings/2026-07-10-blackwell-local-model-bakeoff.md`;
`docs/findings/2026-07-10-blackwell-local-model-bakeoff-evidence/failures.md`;
`docs/findings/2026-07-10-blackwell-local-model-bakeoff-evidence/preflight-transcripts.md`;
`docs/findings/2026-07-10-blackwell-local-model-bakeoff-evidence/reproduction.md`;
`docs/findings/2026-07-10-qwen35-122b-heavy-candidate.md`;
`docs/findings/2026-07-12-gpt-oss-120b-deterministic-recheck.md`;
`docs/findings/2026-07-12-heavy-intelligence-challengers.md`;
`docs/findings/2026-07-12-nemotron-puzzle-recheck.md`;
`docs/findings/2026-07-12-qwen35-122b-mxfp4-benchmark.md`;
`docs/findings/2026-07-12-qwen36-27b-eval-baseline.md`;
`docs/findings/2026-07-12-qwen36-27b-heavy-variation-bakeoff.md`;
`docs/findings/2026-07-12-qwen36-protocol-v2-comparison.md`;
`docs/findings/2026-07-12-rtx-pro-6000-heavy-eval-v2.md`;
`docs/findings/2026-07-12-rtx-pro-6000-heavy-eval-v2-evidence/laguna-reproduction.md`;
`docs/findings/2026-07-12-thinkingcap-heavy-promotion.md`;
`docs/findings/2026-07-13-q36-pro6000-container-recipe.md`;
`docs/findings/2026-07-13-q36-pro6000-container-recipe-evidence/reproduction.md`;
`docs/findings/2026-07-16-gemma4-chat-template-bakeoff.md`;
`docs/findings/2026-07-16-gemma4-unsloth-nvfp4-follow-up.md`;
`docs/findings/2026-07-16-gemma4-vllm0251-wsl2-c128.md`;
`docs/findings/2026-07-17-gemma4-31b-optimization.md`;
`docs/findings/2026-07-17-gpt-oss-puzzle-qualification.md`;
`docs/findings/2026-07-18-gpt-oss-puzzle-heavy-promotion.md`;
`docs/findings/2026-07-26-laguna-s-heavy-qualification.md`;
`docs/findings/2026-07-27-anvil-serving-release-readiness-sweep.md`;
`docs/findings/2026-07-28-agents-a1-multimodal-qualification.md`;
`docs/findings/2026-07-28-qwen35-122b-primary-qualification.md`;
`docs/findings/2026-07-29-agents-a1-primary-promotion.md`;
`docs/findings/2026-07-29-agents-a1-qwen-262k-head-to-head.md`;
`docs/findings/2026-07-29-agents-a1-qwen-262k-head-to-head-evidence/README.md`;
`docs/findings/2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md`;
`docs/findings/2026-08-01-deepseek-v4-flash-0731-research-update.md`;
`docs/findings/2026-08-01-dual-pro-tp2-model-campaign.md`;
`docs/findings/2026-08-02-deepseek-v4-flash-0731-650k-1m-pi-qualification.md`;
`docs/findings/2026-08-02-deepseek-v4-flash-0731-primary-promotion.md`;
`docs/findings/2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md`;
`docs/findings/2026-08-03-deepseek-context-agentic-swe-smoke.md`;
`docs/findings/2026-08-07-deepseek-0731-vision-nvfp4-recipe-intake.md`;
`docs/findings/2026-08-07-deepseek-0731-vision-nvfp4-sglang-first-load.md`;
`docs/findings/2026-08-10-deepseek-v4-flash-0731-r33-quality-control.md`;
`docs/findings/2026-08-10-deepseek-v4-flash-0731-r33-batch-token-ab.md`;
`docs/findings/2026-08-11-deepseek-v4-flash-0731-r33-393k-promotion.md`;
`docs/findings/2026-08-14-qwen38-27b-tp-mtp-context-matrix.md`;
`docs/findings/2026-08-14-qwen38-27b-1m-context.md`;
`docs/findings/2026-08-14-qwen38-27b-official-qualification.md`;
`docs/findings/2026-08-14-qwen38-27b-split-promotion.md`;
`docs/findings/2026-08-15-qwen38-27b-agentic-swe-scout.md`;
`docs/findings/2026-08-15-qwen38-27b-mtp-depth-qualification.md`;
`docs/findings/2026-08-15-qwen38-27b-sglang-consolidation-ab.md`;
`docs/findings/2026-08-15-qwen38-27b-sglang-fp8-single-promotion.md`;
`docs/findings/2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md`;
`docs/findings/2026-08-15-qwen38-27b-sglang-nvfp4-qualification.md`;
`docs/findings/2026-08-16-deepseek-v4-flash-0731-infernal-r15-393k-promotion.md`;
`docs/findings/2026-08-16-qwen38-27b-video-router.md`;
`docs/findings/2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md`;
`docs/findings/2026-08-26-qwen38-flash-next-promotion.md`;
`docs/findings/2026-08-26-qwen38-flash-next-qsa-fast-mtp3-promotion.md`;
`docs/findings/2026-08-26-qwen38-flash-next-vision-promotion.md`;
`docs/findings/2026-08-26-qwen38-flash-next-vision-promotion-evidence/publication-summary.md`;
`docs/findings/2026-08-29-glm53-cardillo-purtell-qualification.md`;
`docs/findings/2026-08-29-glm53-cardillo-adaptive-mtp-evidence/publication-summary.md`;
`docs/findings/2026-08-29-glm53-k3-dflash2-optimization-evidence/README.md`;
`docs/findings/2026-08-29-glm53-k3-dflash2-optimization-evidence/publication-summary.md`;
`docs/findings/2026-08-30-glm53-k3-dflash2-1m-optimization.md`;
`docs/findings/2026-08-31-glm53-xgrammar-524k-qualification.md`;
`docs/findings/2026-08-31-glm53-xgrammar-524k-qualification-evidence/README.md`;
`docs/findings/2026-08-31-glm53-xgrammar-524k-qualification-evidence/publication-summary.md`;
`docs/findings/2026-09-02-glm53-sglang-sm120-393k-promotion.md`;
`docs/findings/2026-09-02-glm53-sglang-sm120-qualification.md`;
`docs/findings/2026-09-02-glm53-sglang-sm120-qualification-evidence/README.md`;
`docs/findings/2026-09-02-glm53-sglang-sm120-qualification-evidence/promotion-publication-summary.md`;
`docs/findings/2026-09-02-glm53-sglang-sm120-qualification-evidence/publication-summary.md`;
`docs/findings/blackwell-sm120-lab-notebook.md`;
`.tickets/closed/2026-07-27-release-sweep-fixes.md`;
`.tickets/closed/2026-07-28-agents-a1-multimodal-qualification.md`;
`.tickets/closed/2026-07-29-agents-a1-qwen-262k-head-to-head.md`;
`.tickets/closed/2026-08-01-deepseek-0731-r16-b12x-performance-spike.md`;
`.tickets/2026-08-01-deepseek-dspark-128k-kv-reserve.md`;
`.tickets/2026-08-01-deepseek-dspark-sm120-w8a8-tune.md`;
`.tickets/closed/2026-08-01-sglang-inkling-modelopt-missing-accelerate.md`;
`.tickets/closed/2026-08-01-sglang-inkling-sm120-grouped-gemm-shared-memory.md`;
`.tickets/closed/2026-08-02-deepseek-r16-native-kv-offload-illegal-access.md`;
`examples/fakoli-dark/q36/README.md`.

## `external-prior`

`docs/findings/2026-08-10-deepseek-v4-flash-0731-community-config-refresh.md`
(community recipe research and proposed test arms; no local measurement);
`docs/findings/2026-08-15-qwen38-27b-external-recipe-refresh.md`
(current external recipe research and proposed official-weight test arms; no
local measurement).

## `protected/co-resident`

`docs/findings/2026-07-07-voice-latency-model-shortlist.md`;
`docs/findings/2026-07-08-stt-model-benchmark.md`;
`docs/benchmarks/hardware/rtx-5090.md`;
`docs/benchmarks/models/nemotron-omni-30b.md`;
`docs/benchmarks/models/nemotron35-asr.md`;
`docs/benchmarks/models/parakeet.md`;
`docs/benchmarks/models/qwen25-omni-3b.md`;
`docs/benchmarks/models/qwen3-asr.md`;
`docs/findings/2026-07-13-t011-ocr-rebalance.md`;
`docs/findings/2026-07-13-t015-resident-set.md`;
`docs/findings/2026-07-28-nemotron35-asr-qualification.md`;
`docs/findings/2026-07-voice-stt-ab.md`;
`docs/findings/2026-07-voice-tts-ab.md`.

## `topology-only`

`README.md`; `START_HERE.md`; `CLAUDE.md`; `docs/ARCHITECTURE.md`;
`docs/COMFYUI-MIGRATION-RUNBOOK.md`; `docs/CONFIGURATION.md`;
`docs/EXTERNAL-BENCHMARKS.md`; `docs/THIN-CAPABILITY-GATEWAY.md`;
`docs/TROUBLESHOOTING.md`; `docs/adr/0010-specialized-engine-tier.md`;
`docs/adr/0011-two-mode-operation.md`;
`docs/adr/0017-gpu-residency-reservations.md`;
`docs/adr/0018-router-transition-safety.md`;
`docs/adr/0034-fleet-control-plane-and-node-runtime-classes.md`;
`docs/benchmarks/rtx-pro-6000-audit.md`;
`docs/findings/2026-07-12-green-context-mps-capability.md`;
`docs/findings/2026-07-18-lifecycle-aware-wsl-cache-reclaim.md`;
`docs/findings/2026-08-01-dual-pro-tp2-campaign-evidence/compatibility-brief.md`;
`docs/findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md`;
`docs/findings/2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md`;
`docs/findings/2026-08-21-qwen38-27b-gguf-250k-rtx5090.md`;
`docs/findings/README.md`; `docs/index.md`;
`examples/fakoli-dark/README.md`;
`skills/anvil-serving-benchmark-docs/SKILL.md`.

## `unrelated`

`CHANGELOG.md` (release-history mention);
`.tickets/closed/2026-08-01-external-benchmark-path-quantization-contamination.md`
(test-adapter identity bug, not a local hardware measurement);
`tests/fixtures/external_benchmarks/rtx6kpro_summary.md` (test fixture, not local
qualification evidence).
