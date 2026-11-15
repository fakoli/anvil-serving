# Bounded marker-store and commit trace

This prospective diagnostic layer preserves the previously tested top-p patch and
its startup gate. It has not itself established a live causal conclusion or a
performance result. Base image: `sha256:41c4926b08c7298fb63bc7f1204926c4f95ce9083ff610069ab442141213b1aa`.
The five original source files are from vLLM commit
`8244d1c44348ae8a5bbfc9f1b607021652283ed4`; exact original and replacement SHA256s
are in `source-hashes.json`. The patch retains the upstream Apache-2.0 license.

Use the managed image-build and recipe surfaces. The recipe entrypoint is
`["python3", "/opt/anvil-marker-trace/launch.py", "vllm", "serve"]` and the explicit
trace setting is `ANVIL_MARKER_TRACE=1`. The image does not change its inherited
entrypoint. The outer wrapper requires the actual CUDA trace regression to pass
within 120 seconds, then executes the existing `/opt/anvil-sampling/launch.py`
unchanged; that wrapper retains its separate 180-second kernel gate. No weights
are loaded until both gates pass. The regression checks original versus patched
budget logits/cache exactly, actual force-event stores, committed history and
boundary-enabled stop truncation, with a 64 MiB allocated-tensor ceiling. Its
selected tokens are synthetic; it does not test stochastic rejection sampling.

`install.py --source-root <pinned-source-root>` checks patch application and Python
compilation offline. The image uses `--install`, validates every original and
replacement hash, preserves `baseline-thinking_budget.py`, and verifies the
existing top-p source and launch wrapper before and after installation. Every
copied build-context file is SHA256-bound in the Dockerfile; installation has no
network or package-install step. The `io.anvil-serving.marker-trace.manifest-sha256`
label binds the exact `source-hashes.json` bytes. Runtime attestation must also
check the installed helper and source files against the reviewed build context.

## Observation contract

Default off performs no trace synchronization. Enabled traces select only
requests with thinking budget 64 and max_tokens 512. They admit at most two
requests per process, sequential C1 batches, four expanded logit rows, prompt
length 1–128, and 512 steps/request. Unsupported shapes, CUDA graph capture,
missing step pairs, history mismatches or overflow emit `kind:invalid` and fail.
There are at most 1,030 records and 4 MiB per process, at most 8,192 bytes/record,
and at most 32 recorded marker positions per snapshot. An extra small invalid
record can report overflow. These synchronization-heavy diagnostics cannot
support performance comparisons.

`ANVIL_MARKER_TRACE` JSON records carry distributed rank, synthetic request ordinal,
request slot and contiguous step number. They contain no request ID strings,
prompt text, ordinary prompt IDs or full logits. Actual GPU snapshots retain:

- budget, committed length, computed count, start/end marker history positions,
  cache before and after the committed-cache update;
- expanded row mapping, local/effective positions and input marker positions
  (input position zero is the committed last token; subsequent positions are drafts);
- only start/end marker logits before budget, after budget and after sampling;
- an eight-column GPU event row: `observed, forced, stored_token, last_start,
  last_end, effective_len, thinking_count, end_prefix_len`;
- selected and accepted generated token IDs, at most four per step, rejected
  counts before and after boundary handling, last sampled token and actual newly
  committed history;
- boundary metadata `[enabled,prompt_len,instruction_len,min_len,max_len,
  num_stops,prefill_tail_len]`, exactly `num_stops` stop IDs (maximum 128), and
  the bounded boundary-capture tensor. A null projection means no boundary
  state was passed to the actual commit operation.

The `forced=1` event is written immediately after the unchanged budget-kernel
`tl.store` to the actual marker logit. Device synchronization precedes host
snapshots. `budget_store` means an observed store for that logit row;
`marker_matches_store` additionally joins the stored marker and accepted token
at that row. Neither establishes the counterfactual that the marker would not
have been sampled otherwise. An accepted marker with no observed store is
unforced by this target budget kernel; draft acceptance/recovery and row history
must be considered before describing its origin. No drafter-logit causal claim
is made. `verified_tokens` precede boundary handling; `committed` is reconciled
against actual history afterward.

A complete evidence packet must account for all expected trace-producing TP
ranks, require request_start/request_end, contiguous steps and no invalid records,
then reconcile committed generated IDs exactly with the native response. Missing,
truncated, mixed-request or unmatched traces leave classification unknown. The
preweight selftest uses a separate process and `kind:gpu_selftest`; it is not a
request trace. Worker logs must be retained with sufficient bounds for both
ranks; raw prompt-bearing engine logs are outside this artifact's payload.
