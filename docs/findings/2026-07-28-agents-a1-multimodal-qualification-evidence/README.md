# Agents-A1 multimodal qualification evidence

This directory contains raw, date-stamped artifacts for the Agents-A1 BF16,
official FP8, and ProtoLabs NVFP4 qualification campaign. The narrative finding
links each artifact and distinguishes local results from external priors.

No artifact in this directory authorizes model or router promotion.

Publication-grade performance evidence uses `capacity-v3`. Each request
retains TTFT, effective prefill rate, generation duration, decode rate, mean
inter-token latency, prompt/output tokens, and E2E latency. Effective prefill
is prompt tokens divided by client-observed TTFT, so it includes queueing,
scheduling, first-token work, and prefill; it is not a kernel-only prefill
measurement.

Key final artifacts:

- `fp8-moe-ab-comparison.json` — paired three-repetition default/tuned A/B and
  the rejected tune decision.
- `fp8-moe-ab-*-v3-*.json` — raw per-request publication timing records.
- `E=256,N=512,...,dtype=fp8_w8a8.json` — complete 18-batch tuner output.
- `fp8-moe-ab-default-startup.log` and
  `fp8-moe-ab-tuned-startup.log` — fallback warning and exact load proof.
- `fp8-router-preflight.json` and `fp8-router-preflight-fixed.json` — original
  thinking-contract failure and corrected routed preflight.
- `fp8-router-multimodal-matrix.json` — initial router matrix, including the
  two preserved 500-classification failures.
- `fp8-router-error-classification-fixed.json` — corrected streaming and
  non-streaming malformed/cross-dialect 4xx probes.
- `serve-state-before.json` and `serve-state-after.json` — exact production
  router protection and campaign teardown/restoration proof.
- `friction-log.json` — chronological failures, fixes, and evidence limits.
