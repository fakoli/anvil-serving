# Capacity timing conflated hidden reasoning with visible decode

Status: fixed locally with reasoning-aware capacity protocol

## Symptom

The capacity-v3 stream timer started generation at the first visible content
delta, but used `usage.completion_tokens` as the decode numerator. Reasoning
serves include hidden reasoning in completion usage and can stream reasoning
well before visible text. This inflated reported decode throughput and treated
reasoning time as prefill/TTFT.

The same behavior made a request that exhausted its cap in reasoning fail only
as a generic `ValueError` with no retained detail beyond the exception class.

## Fix

- Record the first non-empty reasoning or content delta as time to first output.
- Preserve first-visible-content TTFT as a separate user-perceived latency.
- Start completion-token decode timing at first output.
- Use time to first output for effective prefill throughput.
- Count reasoning chunks and identify such artifacts as
  `capacity-v4-reasoning`, preventing silent comparison with capacity-v3.

## Follow-up

- Retain bounded exception messages or stable failure codes in failed request
  rows so `stream completed without visible content` is distinguishable from
  transport and parser failures.
- Add explicit reasoning token counts when an endpoint reports
  `completion_tokens_details.reasoning_tokens`.
