# Findings index

Dated evidence snapshots — benchmarks, live validations, and lab notebooks — that ground the
decisions recorded in `docs/adr/` and the PRD task history. Each file is a **point-in-time
record**, accurate as of its date and not maintained afterwards; treat the ADRs and the main
docs as the current source of truth. `.json` files are the machine-readable raw evidence
backing a companion `.md` narrative. For current conclusions, enter through the
[benchmark portal](../benchmarks/index.md), choose
[RTX PRO 6000](../benchmarks/hardware/rtx-pro-6000.md) or
[RTX 5090](../benchmarks/hardware/rtx-5090.md), then use the
[model dossiers](../benchmarks/models/index.md) or
[run catalog](../benchmarks/runs.md). This page remains the complete
chronological evidence index. Newest first.

## Policy

These findings remain public as durable evidence under
[ADR-0027](../adr/0027-public-findings-are-durable-evidence.md). A later benchmark or ADR can
supersede a recommendation, but it does not erase the historical observation or move its
load-bearing evidence into a private repository.

**Address redaction (2026-07-29):** private tailnet addresses in published findings and raw
evidence files were replaced with the generic placeholder `100.64.0.10`. This is a
publication-safety edit only — one host, one placeholder, applied uniformly — and changes no
measurement, configuration, or conclusion.

- Add and index a sanitized dated narrative with the exact revision/configuration, topology,
  method, evidence type, result, failures, and caveats.
- For local functional, capacity, or quality results, add the result card and companion
  publication summary defined by the [finding format](../benchmarks/finding-format.md).
  Platform copy remains derivative; it never replaces the finding, raw evidence, or promotion
  gate.
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
The legacy corpus is not retroactively certified: [issue #175](https://github.com/fakoli/anvil-serving/issues/175)
published or gap-recorded the private-only grounding it identified, while the broader machine-local
path and public-artifact audit remains tracked by
[issue #290](https://github.com/fakoli/anvil-serving/issues/290).

| Date | File | Subject |
|------|------|---------|
| 2026-08-28 | [2026-08-28-media-gateway-live-validation.md](2026-08-28-media-gateway-live-validation.md) | Exact merged media gateway live validation across Fakoli Dark, Mid Mod, and Mini: real Hermes MCP image/video jobs, A2A replay, authenticated artifact checks, cold approval/unavailable controls, two prompt-adherent FLUX.2 images, one decodable but perceptually failed Wan2.2 video, complete teardown, and no promotion |
| 2026-08-28 | [2026-08-28-media-gateway-release-readiness.md](2026-08-28-media-gateway-release-readiness.md) | Source-merge readiness for the unified MCP/A2A media gateway, managed worker, pinned image/video workflows, and Hermes skill: complete source/package gates passed, isolated worker rollback passed, live enablement remains human-required with no route, workflow availability, promotion, client, or serving-state change |
| 2026-08-28 | [2026-08-28-comfyui-media-qualification.md](2026-08-28-comfyui-media-qualification.md) | Exact managed ComfyUI qualification on one RTX 5090: FLUX.2 Klein 4B FP8 produced a decodable 512×512 PNG at 12,919 MiB peak; Wan2.2 TI2V 5B produced a 17-frame H.264 MP4 at 18,263 MiB peak; clean rollback, quality human-required, no route or promotion |
| 2026-08-26 | [2026-08-26-qwen38-flash-next-vision-promotion.md](2026-08-26-qwen38-flash-next-vision-promotion.md) | Qwen3.8 Flash Next vision promotion and comprehensive dual-PRO TP=2 benchmark: direct media 30/30, live routed repeats 57/60 strict with retained literal-rubric misses, admission/SSE/tool edges 8/8, six-size context/throughput sweep through 245K actual prompt tokens, 516,032-token KV pool, and Hermes/Pi/OpenClaw vision convergence |
| 2026-08-26 | [2026-08-26-qwen38-flash-next-qsa-fast-mtp3-promotion.md](2026-08-26-qwen38-flash-next-qsa-fast-mtp3-promotion.md) | Fix-forward RadixArk Qwen3.8 Flash Next QSA-fast/MTP3 text Primary promotion: exact SM120 patch provenance, matched no-spec A/B, 154.9 tok/s at 4K and 134.1 at 128K, full-reserve capacity, bounded quality, and fresh Hermes/Pi/OpenClaw 262K acceptance |
| 2026-08-26 | [2026-08-26-qwen38-flash-next-promotion.md](2026-08-26-qwen38-flash-next-promotion.md) | Human-authorized RadixArk Qwen3.8 Flash Next NVFP4 text Primary promotion on dual RTX PRO 6000 TP=2: exact revision/image pins, 253,325-token direct and routed retrieval with 8,192-token reserve, bounded quality, tools 20/20, Responses, real OpenClaw/Hermes/Pi acceptance, and the SM120/WSL2 fix-forward record |
| 2026-08-24 | [2026-08-24-anvil-serving-0.35.1-release-readiness.md](2026-08-24-anvil-serving-0.35.1-release-readiness.md) | Release-candidate verification for inference-owned model metadata, the explicit capability-meta-router product decision, synchronized `0.35.1` source/local-image defaults, package artifacts, and publication-only closure with no route, promotion, client, or live fleet change |
| 2026-08-22 | [2026-08-22-anvil-serving-0.35.0-release-readiness.md](2026-08-22-anvil-serving-0.35.0-release-readiness.md) | Release-candidate verification for routed model evaluation, context and long-tool qualification, recipe feasibility screening, serving-agnostic routing guidance, and the Qwen3.8 `llm.secondary` evidence; package publication only, with no route, promotion, or fleet deployment change |
| 2026-08-21 | [2026-08-21-qwen38-27b-gguf-250k-rtx5090.md](2026-08-21-qwen38-27b-gguf-250k-rtx5090.md) | Managed llama.cpp Qwen3.8 27B Q4_0/no-spec versus Q4_0/MTP3 qualification on one RTX 5090: exact retrieval through 253,822 actual prompt tokens with 8,192-token reserve, tools after 110K, 18/18 images, 3/3 101-turn endurance, +50.7% short decode, mathematical Q6_K+MTP disqualification, exact baseline restoration, and promotion deferred |
| 2026-08-21 | [2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md](2026-08-21-deepseek-v4-flash-0731-infernal-r18-1m-promotion.md) | Human-approved Infernal Invocation r18 1M Primary promotion on dual RTX PRO 6000 TP=2: exact digest/source pins, 1,040,063-token retrieval, agentic and 160/160 tool gates, matched K5/no-spec A/B, authenticated routed acceptance, Mini generation-2 context/compaction convergence, real Hermes/Pi/OpenClaw passes, and explicit automatic-rollback P1 caveat |
| 2026-08-21 | [2026-08-21-qwen38-27b-rtx5090-recipe-research.md](2026-08-21-qwen38-27b-rtx5090-recipe-research.md) | Extensive current-source RTX 5090 recipe research plus matched MTP3/ReplaySSM qualification: decode +80.5% at 4K and +67.9% at 64K, but only 70,231 KV tokens and 1.9% slower 64K end-to-end; EXL3, NInfer, TurboQuant, alternative NVFP4, GGUF, Reddit, X, and independent-site leads normalized; exact 128K baseline restored, no route or promotion change |
| 2026-08-21 | [2026-08-21-qwen38-27b-radixark-nvfp4-dflash2-rtx5090.md](2026-08-21-qwen38-27b-radixark-nvfp4-dflash2-rtx5090.md) | RTX 5090 NVFP4 DFlash2 diagnosis: exact high-throughput/float32 arm exposed 24,347 KV tokens; BF16, 0.945 memory, disabled radix/prefill graph, and one Mamba slot raised the safe ceiling to 70,262 and passed a 49,549-token retrieval plus tools 20/20, but still failed the 128K contract; exact stock baseline restored, no route or promotion change |
| 2026-08-20 | [2026-08-20-qwen38-sharp-template-ab.md](2026-08-20-qwen38-sharp-template-ab.md) | Same-weight RTX 5090 Qwen3.8 27B stock-versus-Sharp v22.1 chat-template A/B: Sharp functional pass, unchanged 24/30 MMLU-Pro diagnostic with +10.8% completion tokens and +10.7% latency, -5.1% tokens on a thinking-disabled behavior lane but one failed ambiguity check, exact stock restoration, and rejected/no-promotion decision |
| 2026-08-17 | [2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md) | Retained direct 128K RadixArk Qwen3.8 27B NVFP4 qualification on one RTX 5090: 119,675-token retrieval, tools 20/20, multimodal 30/30, eight-image/two-video boundary 4/4, corrected invalid corpus expectations retained, 64K rollback, and no route or promotion |
| 2026-08-17 | [2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md](2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md) | Single-RTX-5090 RadixArk Qwen3.8 27B NVFP4 qualification: exact digest-pinned SGLang recipe, 60K retrieval and tools 20/20, direct image/OCR/video pass, deterministic multimodal 30/30 including temporal and mixed-media cases, LFM2.5-VL auxiliary comparison, FP8-KV warning, and no-promotion decision |
| 2026-08-16 | [2026-08-16-deepseek-v4-flash-0731-infernal-r15-393k-promotion.md](2026-08-16-deepseek-v4-flash-0731-infernal-r15-393k-promotion.md) | Human-approved Infernal Invocation r15 393K Primary promotion on dual RTX PRO 6000 TP=2: matched K5/no-spec A/B, 351,118-token direct and 340,119-token routed retrieval, tools and repeated agentic passes, fixed-port r33 rollback, retained estimator/reasoning/client caveats, and full upstream author credit |
| 2026-08-16 | [2026-08-16-qwen38-27b-video-router.md](2026-08-16-qwen38-27b-video-router.md) | Current official-FP8 SGLang Qwen3.8 video qualification and managed router-only expansion: direct 30/30 with video 14/14, live admitted 28/28, fail-closed two-image/one-video limits, SSE/tool/error edges, Primary regression, and no model restart |
| 2026-08-15 | [2026-08-15-qwen38-27b-agentic-swe-scout.md](2026-08-15-qwen38-27b-agentic-swe-scout.md) | Router-only AI-MBP25 qualification of the current Qwen3.8 27B SGLang service: agentic smoke 2/2, agentic scout 16/18 with both failures isolated to debug-loop repetitions, and fixed five-instance SWE-bench Verified scout resolved 5/5 under the official grader; bounded evidence, no promotion change |
| 2026-08-15 | [2026-08-15-qwen38-27b-sglang-fp8-single-promotion.md](2026-08-15-qwen38-27b-sglang-fp8-single-promotion.md) | Human-approved single-card official-FP8 SGLang promotion at TP=1/393K/MTP 3/1/4 with FP8 E4M3 KV and CPU multimodal transport: guarded 108K/tools gate, direct and routed 18/18 media, routed Responses correction, Hermes/OpenClaw acceptance, and the second RTX PRO 6000 left empty |
| 2026-08-15 | [2026-08-15-qwen38-27b-sglang-consolidation-ab.md](2026-08-15-qwen38-27b-sglang-consolidation-ab.md) | Matched SGLang BF16, official-FP8, and Inferact-NVFP4 consolidation A/B at TP=1/393K/MTP=3: all three passed a repeated 18-request image/OCR/chart/UI/spatial/two-image corpus, quantized candidates materially improved multimodal and text latency, official FP8 selected as the preferred single-service challenger, exact current split restored, and no promotion |
| 2026-08-15 | [2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md](2026-08-15-qwen38-27b-sglang-mtp-multimodal-qualification.md) | Matched SGLang MTP=3 qualification for official FP8 and Inferact NVFP4 on both RTX PRO 6000 placements: 111.3 versus 98.1 decode tok/s, complete functional and repeated quality gates, 389K retrieval, and bounded image/OCR recovery through explicit CPU feature transport; exact current split restored, no promotion |
| 2026-08-15 | [2026-08-15-qwen38-27b-sglang-nvfp4-qualification.md](2026-08-15-qwen38-27b-sglang-nvfp4-qualification.md) | Matched SGLang TP=1/393K official-FP8 versus Inferact NVFP4 qualification on two RTX PRO 6000 lanes: audited Safetensors, full functional and bounded-quality passes, 388,979-token retrieval, five-run and cross-card speed comparison, retained WSL2 multimodal limitation, exact current-split restoration, and no-promotion result |
| 2026-08-15 | [2026-08-15-qwen38-27b-mtp-depth-qualification.md](2026-08-15-qwen38-27b-mtp-depth-qualification.md) | Official-FP8 Qwen3.8 27B MTP=4/5 qualification on both RTX PRO 6000 lanes: complete functional and near-393K passes, repeated deterministic quality, cross-card swap proving lane variance, exact MTP=3 split restoration, and no-promotion result |
| 2026-08-15 | [2026-08-15-qwen38-27b-external-recipe-refresh.md](2026-08-15-qwen38-27b-external-recipe-refresh.md) | Current vLLM, SGLang, Hugging Face, Reddit, X, and community recipe refresh for Qwen3.8 27B on RTX PRO 6000: dormant official-FP8 MTP=4/5 A/B recipes, official-weight SGLang compatibility plan, third-party artifact exclusions, and no live or promotion change |
| 2026-08-14 | [2026-08-14-qwen38-27b-split-promotion.md](2026-08-14-qwen38-27b-split-promotion.md) | Human-approved official Qwen3.8 27B split promotion: FP8 MTP=3 text Primary plus BF16 MTP=3 multimodal/OCR at matched 393K, routed 30/30 media, one 32-image request, relay thinking-control fix, and Hermes/OpenClaw client acceptance without fallback |
| 2026-08-14 | [2026-08-14-qwen38-27b-tp-mtp-context-matrix.md](2026-08-14-qwen38-27b-tp-mtp-context-matrix.md) | Official Qwen3.8 27B BF16/FP8 TP=1 versus TP=2 matrix on dual RTX PRO 6000: matched MTP=3 controls, 10-request 4K latency/decode, cold retrieval at 388,979, 598,729, and 985,107 actual prompt tokens, engine KV accounting, PCIe/P2P caveat, exact split restoration, and no-promotion boundary |
| 2026-08-14 | [2026-08-14-qwen38-27b-1m-context.md](2026-08-14-qwen38-27b-1m-context.md) | Official Qwen3.8 27B FP8 1M-context continuation on one RTX PRO 6000: repeated retrieval through 825,049 actual prompt tokens, steep cold-prefill latency, post-stress gate, exact 262K restoration, current official recipe comparison, and no-promotion boundary |
| 2026-08-14 | [2026-08-14-qwen38-27b-official-qualification.md](2026-08-14-qwen38-27b-official-qualification.md) | Official Qwen3.8 27B BF16 multimodal and FP8 text qualification on co-resident single-card RTX PRO 6000 lanes: artifact-safety hashes, full functional/reasoning/quality/context/capacity gates, BF16 media 30/30, FP8 c5 30/30, MTP=3, prefix-cache, and KV-precision A/Bs, retained warnings, and no-promotion boundary |
| 2026-08-11 | [2026-08-11-deepseek-v4-flash-0731-r33-393k-promotion.md](2026-08-11-deepseek-v4-flash-0731-r33-393k-promotion.md) | Human-approved r33 393K Primary promotion on dual RTX PRO 6000 TP=2: direct 359,900-token capacity, exact managed routing, OpenClaw and Hermes aligned to 393K context/32K output/high reasoning with client-path passes, retained routed-needle calibration failure, and unsubmitted SWE smoke due to missing wheel profiles |
| 2026-08-10 | [2026-08-10-deepseek-v4-flash-0731-r33-batch-token-ab.md](2026-08-10-deepseek-v4-flash-0731-r33-batch-token-ab.md) | Matched r33 batch-token A/B on dual RTX PRO 6000 TP=2: 8,192 to 4,096 reduced peak activation 34.1%, raised minimum-rank KV allocation 0.72 GiB, retained 6/6 functional and 119,503-token capacity passes, reproduced the 8,192 baseline, and confirmed the 4,096 candidate healthy/direct-only at campaign close with a reported-token accounting caveat |
| 2026-08-10 | [2026-08-10-deepseek-v4-flash-0731-r33-quality-control.md](2026-08-10-deepseek-v4-flash-0731-r33-quality-control.md) | Digest-pinned r33 target-only quality control on dual RTX PRO 6000 TP=2: complete functional and repeated high-reasoning gates, 119,503 actual prompt tokens at 73.86 decode tok/s, context-generator calibration caveat, measured 283,917-token GPU KV ceiling, and a translated-not-loaded 393K FP8-KV plus 16 GiB host-offload candidate |
| 2026-08-10 | [2026-08-10-deepseek-v4-flash-0731-community-config-refresh.md](2026-08-10-deepseek-v4-flash-0731-community-config-refresh.md) | Two-pass community configuration research for DeepSeek V4 Flash 0731: 11 pinned or gap-labeled dual-RTX-PRO and dual-DGX-Spark candidates, 28 sources, 13 Reddit channel groupings, a quality-first 393K FP8-KV arm, NVFP4 weight/cache separation, runtime and agent-protocol gates, and no-promotion scope |
| 2026-08-07 | [2026-08-07-deepseek-0731-vision-nvfp4-sglang-first-load.md](2026-08-07-deepseek-0731-vision-nvfp4-sglang-first-load.md) | WebBrain DeepSeek V4 Flash 0731 Vision (NVFP4) first GPU load on SGLang TP=2: marlin/marlin MoE JIT fix, grounded image conditioning, confabulated OCR/GUI quality failures, no chat template, and no-promotion fail-back to the 650K Primary |
| 2026-08-03 | [2026-08-03-deepseek-context-agentic-swe-smoke.md](2026-08-03-deepseek-context-agentic-swe-smoke.md) | Remote AI-MBP25 benchmark-worker qualification against DeepSeek 0731: 8K context pass, retained agentic final-answer failure after successful tool-error recovery, and one officially graded SWE-bench Verified resolution |
| 2026-08-02 | [2026-08-02-deepseek-v4-flash-0731-primary-promotion.md](2026-08-02-deepseek-v4-flash-0731-primary-promotion.md) | Human-approved DeepSeek 0731 650K Primary promotion: Dark/Mini Pi and Mini OpenClaw high-reasoning smokes, generic per-tier output clamp with warning, exclusive TP=2 safety, and retained 1M client-shaped B12X workspace failures |
| 2026-08-02 | [2026-08-02-deepseek-v4-flash-0731-650k-1m-pi-qualification.md](2026-08-02-deepseek-v4-flash-0731-650k-1m-pi-qualification.md) | DeepSeek 0731 GPU-only 650K/1M qualification after moving display output to the iGPU: 640K and 985K retrieval, matched 32K performance, maxseq1 B12X workspace crash, maxseq4 recovery, maxseq16 1M qualification, memory caveat, and no-promotion decision |
| 2026-08-02 | [2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md](2026-08-02-deepseek-v4-flash-0731-native-kv-offload-256k.md) | DeepSeek 0731 r16 native KV offload on WSL2: mmap-only pinning translation, 128K store/replay, 256K context through 249,573 prompt tokens, 16 GiB CPU-to-GPU reload, stale-tmpfs attribution, and ownership-aware Anvil lifecycle cleanup |
| 2026-08-01 | [2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md](2026-08-01-deepseek-v4-flash-0731-r16-dspark-qualification.md) | Official DeepSeek V4 Flash 0731 on the pinned r16 B12X runtime: DSpark K5 versus same-image no-spec A/B, low/high/max reasoning, 128K timing and per-card telemetry, 27/27 coding-agent attempts, durable WSL2 translation, and no-promotion reserve decision |
| 2026-08-01 | [2026-08-01-deepseek-v4-flash-0731-research-update.md](2026-08-01-deepseek-v4-flash-0731-research-update.md) | DeepSeek V4 Flash 0731 identity, intelligence evidence, reasoning/tool protocol, vLLM/SGLang and DSpark status, 0731 NVFP4/GGUF conversions, reconciliation with the local dual-PRO TP=2 run, and priority no-promotion qualification plan |
| 2026-08-01 | [2026-08-01-dual-pro-tp2-model-campaign.md](2026-08-01-dual-pro-tp2-model-campaign.md) | Five-model exclusive TP=2 qualification on two RTX PRO 6000 cards: DeepSeek V4 Flash 0731, Inkling Small, Qwen3.5, Nemotron 3 Super, and Laguna S; pinned recipes, reasoning-aware capacity, NVFP8 search, failures, and no-promotion decision |
| 2026-07-29 | [2026-07-29-agents-a1-primary-promotion.md](2026-07-29-agents-a1-primary-promotion.md) | Agents-A1 official FP8 passes the complete 262K protocol-v3 gate and becomes the thinking-disabled Primary; Qwen3.5 becomes the immediate managed rollback |
| 2026-07-29 | [2026-07-29-agents-a1-qwen-262k-head-to-head.md](2026-07-29-agents-a1-qwen-262k-head-to-head.md) | Same-GPU, same-context Agents-A1 official FP8 versus current Qwen3.5 122B NVFP4: matched 8K/240K telemetry, unchanged image/video corpus, runtime video failure, restoration, and no-promotion verdict |
| 2026-07-28 | [2026-07-28-agents-a1-multimodal-qualification.md](2026-07-28-agents-a1-multimodal-qualification.md) | Agents-A1 BF16, official FP8, and ProtoLabs NVFP4 qualification on the RTX PRO 6000: text/image/video gates, context/capacity, memory, kernel tuning, routed media admission, and no-promotion decision |
| 2026-07-28 | [2026-07-28-qwen35-122b-primary-qualification.md](2026-07-28-qwen35-122b-primary-qualification.md) | Official NVIDIA Qwen3.5 122B NVFP4 at its native 262,144-token window: pinned recipe, single-PRO-6000 gates, current loading research, caveats, and Laguna rollback |
| 2026-07-28 | [2026-07-28-nemotron35-asr-qualification.md](2026-07-28-nemotron35-asr-qualification.md) | Shared 30-case English STT qualification: Nemotron 3.5 ASR not qualified, Qwen3-ASR 0.6B qualified as an unpromoted replacement candidate, reusable corpus/evidence CLI, and restored protected services |
| 2026-07-27 | [2026-07-27-omni-voice-stack-qualification.md](2026-07-27-omni-voice-stack-qualification.md) | Co-resident Qwen2.5-Omni-3B, Parakeet STT, and Kokoro TTS on the RTX 5090; measured memory, multimodal gates, Gemma license blocker, and no-promotion caveat |
| 2026-07-27 | [2026-07-27-omni-stack-qualification.md](2026-07-27-omni-stack-qualification.md) | Exclusive RTX 5090 Omni tier for auxiliary text, image understanding, and OCR; managed lifecycle, capacity, routing, and caveats |
| 2026-07-27 | [2026-07-27-anvil-serving-release-readiness-sweep.md](2026-07-27-anvil-serving-release-readiness-sweep.md) | Full CLI/parser sweep, Agents-A1 and Laguna qualification, purpose-service, voice, ComfyUI, stack, cache, and resolved release fixes |
| 2026-07-26 | [2026-07-26-laguna-s-heavy-qualification.md](2026-07-26-laguna-s-heavy-qualification.md) | Laguna S 2.1 NVFP4 thinking-control diagnosis, repeated 240K quality gate, capacity evidence, and guarded Heavy promotion |
| 2026-07-22 | [2026-07-22-private-evidence-publication-audit.md](2026-07-22-private-evidence-publication-audit.md) | #175 inventory, sanitized public publication, offline rerun, and exact missing-mirror record |
| 2026-07-22 | [2026-07-22-adr-0008-evidence-gap.md](2026-07-22-adr-0008-evidence-gap.md) | Public provenance correction for ADR-0008 raw logs that were never committed and could not be found |
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
| 2026-06-29 | [2026-06-29-harness-intent-routing.md](2026-06-29-harness-intent-routing.md) | Dated multi-harness feasibility research for model-name-as-intent routing, with version-dependent limits |
| 2026-06-28 | [2026-06-28-planning-capability-eval.md](2026-06-28-planning-capability-eval.md) | Historical Anvil PRD-to-tasks evaluation with complete bounded prompts, outputs, judge records, and reproducible offline aggregates |
| 2026-06-28 | [2026-06-28-anvil-integration-audit.md](2026-06-28-anvil-integration-audit.md) | Pinned Anvil integration audit: one planning endpoint, no fleet or two-endpoint router |
| (running) | [blackwell-sm120-lab-notebook.md](blackwell-sm120-lab-notebook.md) | Blackwell sm_120 lab notebook: which models serve (and how) on fakoli-dark |
