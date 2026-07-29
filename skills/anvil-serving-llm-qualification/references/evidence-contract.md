# Qualification evidence contract

## Required identity

- Repository branch, commit, worktree, and dirty-state note.
- Model repository and exact 40-character revision.
- Runtime image tag/digest and engine revision.
- Served model name, engine/quantization, serve flags, environment controls,
  context, KV dtype, maximum sequences, GPU UUID/name/memory, and topology.
- Corpus schema/path/hash plus every media path, MIME, byte count, and SHA-256.

## Required attempt evidence

- Case/check ID, modality, repetition, sampling controls, concurrency and token
  budgets.
- Request start/order, client clock, TTFT, effective prefill, generation
  duration, decode rate, mean inter-token latency, E2E, token usage, output,
  reasoning presence, finish reason, tool calls, validation results, and
  sanitized failure. Label effective prefill as queueing/scheduling-inclusive,
  not kernel-only.
- Never retain authorization values or media bytes in evidence/logs.
- Preserve the lowest actionable startup/request error and chronological
  friction, including harness timeouts that leave a child container running.

## Hard gates

Pass requires 100 percent deterministic assertions, exact endpoint identity,
visible answers, allowed finish reasons, valid tool calls, matching hashes, and
no OOM, malformed response, parser corruption, or unexplained request loss.

Multimodal router work begins only after direct `video_url` and image gates
pass. Unsupported cross-dialect video translation fails closed.

## Comparison and publication

Report logical checkpoint bytes separately from runtime GPU allocation, KV
capacity, throughput, and latency. A quant is Pareto-preferred only after every
hard gate passes and it clears the campaign’s explicit memory/throughput delta.

Publish:

- before/after cache and serve state;
- source registry with observed dates and evidence classes;
- corpus and checksums;
- raw text, image, video, capacity, and router artifacts;
- failures and friction log;
- final precision/modality decision table and restoration evidence.

Evidence publication does not authorize production promotion.
