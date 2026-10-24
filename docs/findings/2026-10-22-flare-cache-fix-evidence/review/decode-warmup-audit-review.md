# Diagnostic harness and transfer-audit review

## Completed decode review

Reviewed `harness/decode.py`, `check-decode.py`, `analyze.py`, exact body fixtures and `decode-contract.json`. Independently ran:

`PYTHONPATH=OPERATOR_PATH_cc25b9dd30c4/anvil-serving python3 check-decode.py`

Also wrote and ran `review/check-decode-independent.py`. This mocks HTTP and credentials without reading actual credentials or contacting a service, invokes the actual `run_decode` orchestration, and uses a four-party barrier to verify C4 concurrency. It checks exact frozen body bytes are forwarded; native1024-token normalization; one whole-batch speculative-work record; and retention of all four attempts, including a deliberate one-request failure. Both focused checks passed.

C4 metrics are correctly attributed to the drained batch rather than copied into individual request rows. Exact IDs, prompt counts, reasoning span or explicit count, and the complete labeled native speculative work must match before protected decode statistics can pass. Native decode rate is1023 intervals between1024 raw token timestamps. The unchanged prior bodies request temperature1, top_p0.95, top_k20 and seed42; this package clamps top_k20 to-1. No greedy or deterministic claim is warranted.

`diagnostic-amendment.json` prospectively preserves the failed original numerical gate, request bytes, arm order, denominators, containment and thresholds. B or full-matrix collection remains diagnostic only. Reviewer requested an explicit analyzer guard that cannot recommend the candidate in this amended diagnostic phase, even if receipt bookkeeping is accidentally optimistic. Existing false restore-fidelity receipts and prospective blockers also prevent acceptance, but should not be the only representation of the amendment.

## Warmup final scope

Three frozen distinct-prefix prompts in fixed order, requested one token each, approximately8k/16k/29k input sizes; separate from all425 measured/prime requests per arm. Body and corpus hashes are enforced. Native exact lengths and capacity remain pending pilot evidence; approximate text sizes are not validation. Fixed warmup consumes resources and affects cache history, so every fresh incarnation must use the same approved order or retain a deviation. Independently reran final `check.py` and `check-decode.py`: both passed. The canonical diagnostic amendment now forces `recommend_candidate=false` and `performance_eligible=false` even with passing hand-entered acceptance receipts and comparison thresholds. `comparison_gates_passed` is reported separately. Conditional GO for bounded diagnostic collection, with native capacity and byte-gate prerequisites still parent-owned.

## Actual transfer audit

Reviewed completed source patch: per-rank source capture precedes unchanged production copy_batch; synchronized destination comparison precedes successful rank reply. Errors drain the stream before combined-rank failure publication. Fixed scratch is16MiB pinned plus16MiB expected per rank (64MiB TP2); mixed memory+disk verification is rejected to avoid a second transfer set. Default is off; native/Python flag propagation and headroom accounting are present. No payload or pointer contents enter diagnostic logs. Transfer timing is perturbed, so measured matrix arms must explicitly retain verification off. This audits actual copy payloads and does not replace the failed sampled numerical gate or verify independently selected source IDs.

Independent CPU check `cargo test --manifest-path helios/Cargo.toml --features runtime-package verification --lib --locked -q` passed27 tests; this broad filter includes existing unrelated verification tests as well as the new corruption/mixed-tier tests. The focused host-headroom test also passed. No reviewer GPU action occurred.

One pre-live validation gap was identified: the existing ignored two-device25/75 roundtrip invoked audit-disabled Transfers::start, so it did not exercise the new CUDA mapped_host_address assumptions or capture/compare methods. Requested that author enable verification in that fixture and that parent run it before the costly model audit. This is a targeted pending verification requirement, not a demonstrated production defect. Final approval of live audit awaits that result.

Resolved in source: fixture now calls start_with_verification(...,true), checks exact per-rank outbound/inbound byte counts, positive descriptor counts and zero errors, and reads split slab storage through mapped_host_address. Per-rank VerificationStats arrives through actual rank replies; first-error aggregation and drain behavior remain intact. Independently ran focused offload::transfer::tests:8passed,2GPUtests ignored. git diff --check passed. Final static GO for shared diagnostic image build and parent-owned bounded GPU verifier test, with actual model audit conditional on that test passing. No GPU outcome is claimed by this review.

Reviewed full source delta frozen in `reviewed-source.patch`, SHA256 b132065b27532028bf2a02e754e86553ea846281f616f22ce74e89442ff0ec76. It includes preserved pre-existing source edits; this hash identifies the reviewed working delta, not an upstream commit. No remaining source-review blocker identified.

## Final v2 runtime pin wrapper review

Reviewed actual runtime-pins.json and all four selected managed registries. Shared image is sha256:d8fb208298fd3dd3f6b2b631148b01467a9449a9dcd1fef7dca304714bcf9411; source patch hash matches the frozen reviewed delta. A uses default25%, B explicitly75%, both32GiB; audit variants add exactly the verification flag while normal timing variants omit it. Wrapper captures exact runtime bytes/hash, selected registry/hash and configured image; initial status plus running inventory binds the real instance, and repeated samples reject registry/runtime drift. Audit mode requires the flag; timing mode rejects it. Legacy fallback remains only when runtime-pins.json is absent and cannot select audit.

Independent check-phase.py passed real retained-schema identity, configured-image/audit-mode tests and actual SIGTERM/SIGINT fixture-child cleanup. No HTTP or GPU was used by reviewer. Conditional GO for three bounded correctness HTTP requests after parent-owned GPU verifier pass. Frozen review hashes are in runtime-pins-review.json; failed numerical acceptance remains unchanged.
