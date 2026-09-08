# Native Linux benchmark evidence and workflow gaps

Status: Open. Observed during the 2026-09-08 GLM Linux/WSL comparison.

## Retained model compliance failures

The unchanged general-image preflight expects contiguous STATUS READY and GPU
42 PERCENT phrases; the answer places correct values in Markdown table cells.
The independent image corpus passes 12/12. Preserve both outcomes. Separately,
the strict 128-word controlled-output scout emits 503 words to the 512-token
limit, and unique natural-answer canaries appear away from the required first
position in 9/10 requests. Do not relax these gates or count those populations
as performance-eligible. Any future prompt-contract redesign needs independent
fixtures, explicit versioning, and a new matched baseline.

## Product/documentation gaps

- README/preflight examples still reference eval benchmark run, but the command
  registry exposes eval benchmark capacity. Synchronize examples with the CLI.
- The generic evidence inspector rejects multimodal-benchmark-evidence/v1 even
  though the managed multimodal command emits it. Add schema support without
  flattening native attempts, corpus hashes, or failed evidence.
- Recipe status does not include restart count, OOMKilled, or start time. Add
  bounded fields so qualification does not need read-only Docker metadata.
- Durable multi-cell capacity and cursor log work remains tracked in
  2026-09-05-capacity-campaign-jobs-and-cursor-logs.md.

## Acceptance

1. CLI-reference audit catches retired command spellings in maintained docs.
2. Evidence-reader tests cover passing, failed, and malformed native multimodal
   artifacts while preserving their schema and attempt counts.
3. Recipe-status tests expose restart/OOM/start fields without environment or
   unrelated process disclosure.
4. Any model/prompt retest retains these original failures and its exact
   changed controls; a successful manual retry alone is not closure.

No serving, routing, credentials, or qualification thresholds changed in this
campaign. Raw evidence is under the dated Linux comparison finding.

## Additional routed boundaries

- A 380K-target needle request succeeds directly but gets HTTP413 through the
  router. Installed config declares upstream context admission, yet enabled
  media admission applies its byte/word estimate even to text-only requests.
  Installed serve.py passes estimated prompt tokens to media_admission.py,
  whose unconditional total-context check rejects this input. The 260K target
  passes. Preserve this as a gateway envelope limitation, not model incapacity.
- Routed thinking-enabled checks return correct visible results but retain no
  reasoning channel, failing the required-reasoning evidence gate; identical
  direct controls retain reasoning and pass. Installed dialect/internal paths
  do not preserve reasoning_content. Verify intended protocol contract before
  changing projection or documentation. This campaign changes neither.

## Durable context progress and partial evidence

The 150-case context job reports only `executing context suite` until the
whole suite returns. Operator status cannot distinguish completed cases or
current context depth; engine prefill logs prove activity but are not benchmark
case evidence. Extend the managed suite/job interface with bounded per-case
completion events and retained partial native observations. Preserve exact
request identity, failed attempts, ordering and final aggregation; do not
infer progress from GPU allocation or relabel partial runs as complete. Verify
interruption, timeout, cursor replay, restart recovery and final-versus-partial
count agreement independently. No runner change is made during this baseline.

The context adapter also accepts a job `thinking_mode` parameter without
forwarding it to the native request builder. Preserve this run as default
thinking. A future control-contract fix must either honor supported controls
with request-body tests or reject unsupported controls before submission; it
must not retroactively label this evidence thinking-disabled.
