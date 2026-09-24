# ThinkingCap Qwen3.8 27B benchmark and optimization plan

Status: evaluation target corrected to the 32 GB RTX 5090. Prior downloads occurred on the wrong PRO 6000 host; no inference ran. Complete local staging and a smaller 5090-specific recipe remain pending. No local performance or quality result exists.

Selected target: bottlecapai/ThinkingCap-Qwen3.8-27B-NVFP4A4-AWQ@f8fe157f207a13f977bc3d620ce10a3e9ba5ab11. User selected the NVFP4 W4A4 AWQ build with complete local weights and mandatory vision. Other artifact metadata is retained for provenance.

## Required multimodal contract

User requires locally available model weights and working vision. Retain all checkpoint shards, auxiliary weights, tokenizer and image/video processor assets from the pinned repository. Verify the tensor index includes the vision components after authenticated access; public image-text-to-text metadata alone is not a tensor audit. Weight-only describes quantization, not omission of vision. Never disable the vision encoder or use a language-only serving mode to obtain a performance or memory win. Image understanding, OCR, screenshot/chart interpretation, and multi-image requests are hard acceptance gates; measure their memory and latency with vision enabled. Video remains a separately verified capability.

## Entry gates

- Existing model-host home .env is the verified HF_TOKEN source. The subsequent authenticated managed download succeeded and verified the complete snapshot.
- Target the local RTX 5090 Docker Desktop runtime; capture fresh live GPU memory, reservations, cache capacity, current recipes/router, and exact restoration state through managed Anvil commands.
- Local GPU inventory observed an RTX 5090. BF16 has 27,781,427,952 BF16 parameters, or 55,562,855,904 weight bytes: full GPU residency cannot fit its 32 GB class. Quantized fit remains unresolved until runtime, workspace, recurrent state, and KV allocations are measured.
- Correct target inventory: RTX 5090, 32,607 MiB total; existing Qwen Huihui NInfer NVFP4 MTP3 160K managed serve is running. PRO 6000 work is superseded and its GLM campaign is irrelevant to this evaluation. Establish exact local candidate and restoration previews before temporary interruption.
- Verify the executing source: isolated checkout is Anvil 1.0.0; original dirty checkout reported 1.2.1. Pin the final host launcher before inference and preserve unrelated changes.
- Preview exact managed pulls and recipe lifecycle. Keep one isolated loopback candidate at a time. Route changes and promotion remain separately gated.

## Quality and functional matrix

1. Thinking-disabled direct preflight first: exact identity, visible text, JSON, tool arguments, streaming, and tool-result continuation. Stop on failure and inspect owning runtime logs.
2. Main comparison: ThinkingCap versus base Qwen3.8 at matched precision/runtime/hardware, with xhigh reasoning. Medium and low are separate tradeoff lanes; retain thinking-disabled as a compatibility control.
3. Provisional quality budget: 8,192 visible tokens plus 32,768 reasoning tokens. Verify runtime accounting. Preserve exhaustion as failure; any budget adjustment must be applied to both models.
4. Pin corpus revisions, licenses, hashes, sampling, seeds, and item selection before requests. Use deterministic executable grading where possible and an independent model/human for subjective grading. Run three repetitions per case.
5. Record accuracy, reasoning and visible tokens, finish reasons, retries, time to correct answer, and successful tasks/hour. Small local probes are not full GPQA/MMLU/SWE scores.
6. Context: 4K, 32K, 64K, 128K, then 250K usable prompt tokens when measured capacity supports it. Keep output/reasoning reserve separate; record actual tokenizer counts. Include retrieval at multiple depths, distractors, long tools, and tool-error recovery.
7. Run hashed image/OCR and direct video/mixed-media corpora for claimed modalities. A failed direct video gate blocks routed video qualification.
8. Include 100-turn stability and independent agentic/SWE suites. Missing datasets or runners remain explicit gaps.

## Capacity and optimization matrix

- Scout 4K/32K at C1/C2/C4/C8 with four requests per cell. Extend to 64K/128K only within observed KV capacity.
- Finalists: three warmed repetitions, 100 measured requests per population, 256 response words, 2,048 completion tokens, strict output validation, unique prompt-cache mode, and request canaries. Keep these controlled decode lanes thinking-disabled; do not mix them with variable-length reasoning tasks.
- Retain TTFT, queue-inclusive effective prefill, decode, aggregate throughput, request-level TPOT, p50/p95 E2E, errors, GPU allocation, and available power/energy telemetry. Label p99 population and method.
- First compare no speculation against publisher MTP3 using an explicit TRITON_ATTN draft attention backend; test MTP2 if supported. Hold all other controls fixed. Do not assume a base-model DFlash draft remains compatible with this fine-tune.
- Keep the selected NVFP4A4-AWQ checkpoint fixed and compare against a matched W4A4 base-Qwen3.8 control where available. Optimize runtime, MTP, batching, and KV settings with vision enabled. Weight-only NVFP4 is a separately labeled optional comparison. BF16/FP8/GGUF downloads are outside the initial campaign. Revalidate quality, tools, and vision after every runtime change.
- Change one batching, chunk-size, KV-format, or speculation control at a time. Kernel tuning requires an exact fallback warning or measured bottleneck; this dense model does not justify an MoE tuner by name alone.
- Provisional acceptance: at least 5% repeated primary performance gain, no protected-lane regression above 3%, all deterministic gates passing, and at most one percentage point quality loss. Unresolved statistical uncertainty means inconclusive.

## Closure

Restore captured managed recipe/router/GPU state and verify applicable direct/routed smokes. Publish native evidence, finding/index, run catalog, ThinkingCap dossier, measured hardware page, and paired charts. No promotion is authorized by this campaign.

The initial managed download denial was resolved. All 26 snapshot files and the pinned vLLM 0.29.0 runtime are cached. The index contains 333 visual tensors and the model config retains BF16 vision. No inference, benchmark, optimization, or ThinkingCap serving lifecycle mutation has run.

## Target correction

The 49K/C8 PRO 6000 recipe is not a 5090 candidate. Recompute explicit feasibility before creating the smaller 5090 scout. Preserve vision and measure a modest context/C1 first. Higher-context and concurrency rows remain conditional on observed memory and quality.
