# CPU shared-fetch lifetime proposal

Proposal only, 2026-10-22. Source has not been changed. No build, cargo test,
GPU, HTTP, or formal-harness execution occurred. `input.json` identifies the
input file SHA-256 and Git HEAD; local instrumentation is already present in
that source and is preserved by the insertion-only patch.

One test, two controlled completion outcomes. Two Generations join one
FakeStore fetch; dropping the first Generation is serviced by Worker::admit.
The survivor stays Inbound while Fetched is held. Additional scheduler tickets
fill the other snapshot rows and expose their destination identities; every
such ticket excludes the protected page/row IDs. Row admission then fails.
An ordinary request fills remaining pages and another allocation fails. Exact
resource counts before/after this bounded pressure distinguish private waiter
cleanup from ticket ownership; the original FakeStore ticket remains unchanged.
Controlled successful Fetched restores the survivor using the exact original
page chain and snapshot slot, with one checkpoint pin. Controlled failure frees
the ticket and readmits only the survivor with host lookup disabled. Success publishes directly from Finishing after Resume fence 2; only the
failure/readmission arm completes Snapshot fence 4. Both arms
complete exactly one surviving Generation, issue no second fetch, then force
ordinary admission to reclaim the entire page pool after final mock fences.

Limits: CPU scheduler/Worker fixture only. There is no real network disconnect,
executor drain, TP2 transfer, physical DMA overlap, model execution, numerical
comparison, or performance evidence. The two pressure tickets use scheduler
admission directly and are completed as failures after their waiters cancel;
they do not submit physical fetches. Private reserved pages are not exposed by
LiveRequest::pages before graph commit, so the proposal checks exclusion of
other ticket destinations plus pool exhaustion and restored original identity;
it does not enumerate those private reserved-page IDs. No production hooks or
new framework are proposed. Reclamation uses ordinary admissions, not a mocked
shutdown-drained assertion.

Later, from OPERATOR_PATH_d81b073e3d22/flare-local-inference, after patch review and only
between GPU windows:

```sh
cargo test --manifest-path helios/Cargo.toml --features server --lib server::runtime::phase_tests::cancelled_shared_fetch_keeps_ticket_resources_until_completion -- --exact --nocapture
```

That command compiles if needed; it is intentionally unrun here. This proposal
used one implementation pass and one focused source correction (cache eviction
through ordinary admissions instead of incorrectly expecting shutdown to evict
cached checkpoints). Independent review requested one further narrow correction: successful full-prompt
Resume goes directly to Finishing, so Snapshot is advanced only in the failure
arm. Parent owns application, review, and focused test results.

## First execution result

After managed restoration, the independently reviewed correction was applied as a
later test-only source delta. The locked command with jobs=2 and server feature
failed compilation in 7.38 seconds (exit 101), before the focused test ran.
Existing offload tests import runtime_package without that feature enabled and
reference unavailable gpu_transport. No repair or retry occurred. The proposed
fixture is applied but unverified. Frozen v2 Docker/runtime/harness identity is
unchanged. See ../lifetime-test-execution/execution.json and stderr.log.

## Command repair1 result

Authorized command-only r2 added the existing runtime-package feature. The same
exact focused CPU test passed: 1 passed, 0 failed, exit 0, 23.56 seconds including
compilation. Original server-only compile failure remains retained. Source and
proposal patch hashes were unchanged during r2. See
../lifetime-test-execution/r2/execution.json for command, feature set, and executed
test-binary hash, plus stdout.log/stderr.log for output. This is CPU lifetime
coverage only and a later test-only source delta outside the frozen v2 image.
