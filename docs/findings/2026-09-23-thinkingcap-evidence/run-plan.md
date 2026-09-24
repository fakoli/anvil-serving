# RTX 5090 ThinkingCap benchmark plan

This target-specific execution plan supersedes the broad [initial planning draft](run-plan-initial-superseded.md). The user explicitly authorized autonomous tests, stopping the incumbent, and bringing up a suitable ThinkingCap model on the RTX 5090. No route/client alias change is included.

## Fixed identity and capability contract

Model: `bottlecapai/ThinkingCap-Qwen3.8-27B-NVFP4A4-AWQ@f8fe157f207a13f977bc3d620ce10a3e9ba5ab11`. Runtime: vLLM0.29, image `sha256:082ca6f035279109041ffd3fe0695cb568b29bc580b35c4f297a66a08b216c1b`, engine `98dff2a81d747d1dba01a47f939f48c3526d4206`. One local RTX5090, Windows/Docker Desktop/WSL2. Operational launcher is the verified original checkout, CLI1.2.1; isolated1.0.0 checkout owns public documentation.

All26 snapshot files are cached locally. Vision and MTP BF16 tensors remain intact. Vision cannot be disabled to obtain fit or speed. Required gates: text, JSON, tool arguments, streaming tools, tool-result continuation, images, OCR, charts, screenshots, spatial relations and two-image comparison. Video is outside the requested image contract.

## Measured entry and baseline

Exact incumbent restoration was captured before the authorized managed unload. Its text and18-image checks passed; strict256-word capacity failed4/4, and the limited10-question MMLU-Pro diagnostic passed24/30 attempts with six length failures. These are not matched quality/precision controls for ThinkingCap.

Initial16K/C1 eager calibration uses BF16 KV, .87 memory fraction,1024-token prefill chunks and no prefix cache/speculation. The default WSL V2 runner failed before weights at UVA initialization. An explicit managed `VLLM_WSL2_ENABLE_PIN_MEMORY=1` recipe resolved it. Measured model loading21.34GiB and cache5.67GiB/80,554tokens establish initial physical fit, not a long-context qualification.

## Sequential optimization

1. Eager control: full functional preflight and18-attempt image corpus; original4-request256-word strict capacity scout.
2. Because that scout failed exact-length validation, use the predeclared32-word,512-token short-output workload for every subsequent matched candidate arm. Preserve the failure. This workload supports short-output comparisons only.
3. Compare eager and graph-enabled arms by removing only `--enforce-eager`. Then compare graph control with MTP3, with `attention_backend: TRITON_ATTN` nested inside speculative configuration for the drafter only. Target FLASH_ATTN remains matched. Observe actual startup backends.
4. Capacity uses nominal4K input, C1, seed0, unique cache, request canaries, strict exact output, thinking disabled. Scout4requests, then100 measured requests for survivors. Repeat finalist populations when needed to establish stability. Below100, p99 is only descriptive; even100 is one tail order statistic, not a stable service SLA.
5. Recheck text/tools and18 image cases after runtime changes. Separate reasoning-enabled and diagnostic quality suites from controlled decode. Record budgets and finish reasons; exhaustion is failure.
6. Expand context toward64K only after post-graph/MTP cache measurement. Verify actual prompt counts, output reserve and retrieval correctness. Test concurrency within measured capacity; never equate allocated KV tokens with independently qualified full windows.
7. FP8 KV is a separate optional context arm. At this pinned version FLASH_ATTN/SM120 cannot use FP8 KV; a target Triton BF16 control is needed before a dynamic per-token/head FP8 arm. No uncalibrated1.0 scales or unsupported legacy calculate-kv-scales flag.

## Evidence and closure

Retain all native failed and successful outputs, exact recipes, source identities, GPU telemetry, independent review and gaps. Never reconstruct overwritten native evidence: duplicate initial preflight is functional-only, with its lost first raw record disclosed. Use unique output names and poll an existing process session rather than resubmitting after a tool timeout.

Keep a validated useful candidate available under the user's authorization; restore the exact incumbent if no suitable full-vision candidate survives. Verify final direct health and behavior. Publish the finding/index, run catalog, model dossier, RTX5090 page, artifact ledger and derivative charts. No universal model-quality or quantization winner may be inferred from this single selected checkpoint.

## Bounded cache expansion after the MTP scout

The [expansion plan](optimization-expansion-plan.json) was recorded before the new live comparisons. MTP BF16 reported 44,683 cache tokens and its 100-request short-output run had one strict word-count failure. A matched target-Triton BF16 control precedes a dynamic per-token/head FP8 cache arm, with both target and draft inheriting the same KV format. Runtime source review corrects the earlier assumption that the nested drafter backend alone controls the standard V2 MTP path. Keep every failed population failed; select a useful direct profile only within its independently measured functional, context and output limits.

## Final disposition

The selected 32K BF16 profile and completed context/agentic outcomes are recorded in [the final decision](summary.json) and [coverage](coverage-and-gaps.md). The original plans above are retained as predeclarations; planned but rejected branches do not imply execution. [Ending state](restoration.json) verifies authorized direct retention.
