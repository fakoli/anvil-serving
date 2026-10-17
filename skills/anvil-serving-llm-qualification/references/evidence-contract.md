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
- For failure diagnosis, retain bounded, sanitized effective requests and raw
  response/SSE content, including available reasoning and tool arguments, in
  protected operator evidence. Record capture limits and missing content.
  Preserve diagnostic detail privately; publish only safe excerpts and hashes.
- Never retain authorization values or media bytes in evidence/logs.
- Preserve the lowest actionable startup/request error and chronological
  friction, including harness timeouts that leave a child container running.

## Hard gates

Pass requires 100 percent deterministic assertions, exact endpoint identity,
visible answers, allowed finish reasons, valid tool calls, matching hashes, and
no OOM, malformed response, parser corruption, or unexplained request loss.

Multimodal router work begins only after direct `video_url` and image gates
pass. Unsupported cross-dialect video translation fails closed.

A failed gate disqualifies that configuration/run from acceptance, not all
supported configurations of the model. Follow `configuration-search.md` before
candidate rejection. Keep coding correctness, strict formatting, budget stops,
and runtime failures distinct in the decision record.

## Comparison and publication

Every model validation or test campaign declares whether it is a serving
comparison, focused quality check, or smoke before requests. A serving
comparison retains a content-bound `frozen_plan` with
`frozen_before_requests: true`, pinned source revision/file hashes, at least two
model/revision/image/runtime/hardware arms, and required cells mapping arm,
workload, context, concurrency, and request count. `metrics_coverage.required_cells`
exactly matches the plan. Each cell declares the required families:
effective prefill; TTFO and visible TTFT when separately observable; decode,
TPOT, E2E, and aggregate output throughput; actual input/output counts and
distributions; weights, memory, and KV capacity (KV bytes and runtime token
pool separately); context; requested and observed concurrency/occupancy; GPU,
host, and cgroup memory; OOM/admission failures; sample count, p50/p95,
workload identity, and recipe identity. Record each metric as `measured`,
`configured`, `estimated`, `blocked`, or `not_collected`. Only retained native
observations are measured. Engine-reported `kv_cache_tokens` are allocated slots; a full-context
concurrency calculation from them is a separately labeled estimate, not
simultaneous-capacity proof. Configured concurrency is not measured occupancy.

Record native, configured, and measured context separately, including input
size, output/reasoning reserve, and simultaneous requests. An untested larger
window remains unknown even if advertised or configured. A focused smoke or
quality-only check may scope performance out and explain why it was not
remeasured; it is not a full serving comparison.

Performance measurement, quality, and promotion are independent. Preserve an
ordinary quality miss and complete separately planned safe diagnostic timing
cells when authorized; infrastructure, identity, resource, OOM, or admission
failure stops unsafe work. A run may finish with failed or blocked cells, but
no full-comparison claim is allowed while any required metric is non-measured.

Report logical checkpoint bytes separately from runtime GPU allocation, KV
capacity, throughput, and latency. A quant is Pareto-preferred only after every
hard gate passes and it clears the campaign’s explicit memory/throughput delta.

Publish:

- the common files and all ten roles from
  `../../anvil-serving-benchmark-docs/references/artifact-set-contract.md`;
- before/after cache and serve state as configuration and restoration evidence;
- source registry with observed dates and evidence classes;
- corpus and checksums as the workload manifest;
- native raw text, image, video, capacity, and router artifacts without schema
  conversion;
- failures and the live friction log; and
- final precision/modality decision table in the decision summary.

Complete publication through `skills/anvil-serving-benchmark-docs/SKILL.md`,
including its `anvil-serving.benchmark-artifact-set/v1` manifest,
publication-ready result card and artifact-consistency checks. Add a
publication summary only when compact public communication is requested;
otherwise mark the role `not-applicable` with a reason. A format-only migration uses complete retained artifacts and does not
authorize a model load or benchmark rerun.

Evidence publication does not authorize production promotion.
