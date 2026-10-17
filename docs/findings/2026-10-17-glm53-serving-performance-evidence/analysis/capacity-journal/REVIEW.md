# Prepared capacity journal patch — append JSONL revision

Prepared only against product HEAD `5e1f92fbe2624b2121f443c146db422dd36d62cb`. Live product source is unchanged; no endpoint calls, lifecycle changes, commit, or PR occurred. `receipt.json` binds the patch and candidate files and verifies live CLI hashes before/after. This revision replaces the rejected whole-JSON snapshot proposal.

## Scope and minimum seam

Patch touches only `anvil_serving/benchmarking/cli.py` and adds `tests/test_capacity_journal.py`. The compatibility facade and canonical capacity command share this coordinator. Existing `run_request` failure classification, `result_timings` bounded projections, publication sanitizer, native-summary atomic writer, and content-bound evidence references remain the core primitives.

Selecting output creates `<output>.journal.jsonl`. A compact header records running/ineligible status, workload identities and controls, including explicit requested/effective reasoning effort. Each collected terminal result appends one compact outcome line and flushes/fsyncs it. Successes retain normalized timing, tokens, visible-capture metadata, and output-contract observations; failures retain existing bounded response timing/metadata and failure observations. Only the coordinator writes. Total journal serialization is O(n), retaining the existing 10000-request limit. No raw request prompt, response body, reasoning text, or auth field is written. Known secret patterns and the selected API key in failure messages are redacted from journal outcomes.

A final completion line contains completed/failed/response counts, observed client overlap, and separate performance eligibility. Complete means dispatch and collection ended, never quality qualification. Missing completion or a truncated final line means incomplete; earlier newline-terminated JSON records remain evidence. The header states this readback rule. No production consumer exists yet, so no new recovery service or reader framework is added. Focused tests exercise this rule. Native summary schema stays unchanged with one additive hash/size/path reference to the whole journal.

Canonical expanded/resolved paths for all output targets, including the derived journal, must be distinct before any request. This prevents recipe emission overwriting the bound sidecar. An existing journal is refused before workload preparation. Exclusive header creation (`x`) also refuses a racing file before worker submission; partial journal bytes are never overwritten. An existing native summary without a journal retains the historical overwrite behavior. Use a fresh output path per run.

Alternatives: final-only writes lose completed evidence; whole-file snapshots were rejected in independent review because O(n squared) aggregate IO is excessive at 10000 requests; lowering request count was rejected by the parent in favor of linear append IO. Managed jobs/resume would add an unrelated lifecycle.

## Checks

- Focused benchmark suite plus journal tests: 188 passed, 5 subtests passed.
- Abrupt child exit on the second request preserves the first terminal result and max-reasoning header, leaves no completion footer or new native summary.
- A simulated truncated append preserves earlier newline-terminated records and cannot claim success.
- Normal success, request failure, and reasoning-only stream-validation failure retain correct eligibility, failure metadata, and a valid whole-journal SHA-256 reference.
- Canonical recipe/journal path collision rejects before calls or writes. Preexisting and startup-racing partial journals retain exact bytes with zero request calls. A 10000-request output dry plan remains supported.
- Negative control: interruption regression fails on unchanged HEAD because no journal exists.
- Apply-check and exact hashes are in receipt.json. No network/model requests occurred.

## Remaining limits

- fsync latency remains inside aggregate benchmark wall time; per-request timing calculation is unchanged. Compare producer versions explicitly.
- A kill before a terminal outcome is collected/appended can lose that outcome. Partial stream chunks are outside scope. This tests process-exit retention, not power-loss durability; the containing directory is not fsynced.
- Missing/truncated footer means incomplete even if all requests appear present. Records are evidence, not a resumable workload.
- Persistence errors propagate outside model-failure handling; existing executor shutdown still waits for submitted workers. Concurrent processes must use distinct output paths.
- KV/cache and GPU/host memory remain external telemetry; client concurrency does not prove residency or engine execution. No values are synthesized.
- Existing native-summary/log sanitization is outside this patch. Journal redaction does not make arbitrary operator-provided metadata publication-safe.
- Independent reviewer accepted v3 (see receipt.json); root application/full-suite checks remain required after both live arms end. No deployment authorization is implied.
