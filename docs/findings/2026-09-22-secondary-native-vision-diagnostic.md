# Huihui Qwen3.8 secondary native vision diagnostic on RTX 5090

**Date:** 2026-09-22
**Status:** `compatibility-only`, `not-qualified`, `no-promotion`

This retained diagnostic checks one narrow browser-observation compatibility path. It does not select a model, authorize an action, establish capacity, or change a deployment.

## Outcome

The direct native diagnostic used 12 frozen synthetic one-image cases at C1, a 1,024-token completion limit, and temperature 0. Eleven of 12 exact whole-answer assertions passed. The sole failure was `adversarial-2`: expected `Payment pending | 402`; the response was `PAYMENT PENDING | Invoice 402`, retaining an extra `Invoice` label.

All 12 requests ended with `stop`, produced no reasoning output, and recorded no transport error. That is limited to this sequential diagnostic; it is not a reliability, quality, latency, cold-start, warm-start, throughput, or capacity result.

## Configuration and preflights

The observed direct lane used `qwen38-huihui-ninfer-schemafix-mtp3-160k` at model revision `181446902fc777c479749e98cf2abf2250263a8d`, NInfer revision `70434721b1ae29d0616f3de9b376c8a4d91590b5`, and runtime image `sha256:22fa89d51912535dfb0ebc6cf035e33a99a06b6a27b592dfef1f112d5e349b43`, on one RTX 5090. The evaluator used `thinking_mode=unsupported` to omit `chat_template_kwargs`; the evidence policy forbids reasoning output. This describes the request policy, not a runtime reasoning limitation. The retained recipe name is `lyf/Qwen3.8-27B-Huihui-NInfer-NVFP4-MTP3-160K-Baked`; actual context, KV and launch flags were not retained independently, and exclusive GPU use is unproven.

Three native preflights remain separate evidence:

- An explicit `enable_thinking=false` template request was denied with HTTP 400 before inference.
- The native-policy preflight at a 512-token limit reached `length` on its general-image case and failed.
- The native 1,024-token policy preflight completed with `stop` and passed.

Neither the passing preflight nor the diagnostic qualifies the configuration.

## Measurement, limitations, and retained evidence

The 12 client full-request samples have descriptive median latency of 0.190012 s and nearest-rank p95 of 0.422884 s. Cold/warm status is unknown; no TTFT, decode rate, concurrency, or comparative performance claim is supported.

The generic evidence-show tool rejects the retained multimodal schema, so the native evidence has not been rewritten to fit it. The post-run restoration check is retained as evidence only and does not make a deployment claim. The exact-format failure remains open.

Retained sanitized evidence: [evidence index](2026-09-22-secondary-native-vision-diagnostic-evidence/README.md) and [artifact manifest](2026-09-22-secondary-native-vision-diagnostic-evidence/artifact-manifest.json).
