# Sampling upstream-tolerance evidence

This active trial uses a prospectively frozen `upstream-count-v1` kernel admission rule. All eight forced cases and eight ordinary cases (272 rows) passed. The single candidate request failed the repeated-close and exact-output gates. Exact operational restoration passed; both matched strict JSON smokes failed, with no established quality regression. No promotion follows.

- [Dated finding](../2026-10-25-sampling-upstream-tolerance.md)
- [Frozen experiment](plan.json), [bounded probe plan](probe-plan.json), [identity](identity.json), [source freeze](source-freeze.json), and [build](build.json)
- [Verbatim safe kernel receipts](kernel-receipts.log) and [derived kernel summary](kernel-summary.json)
- [Candidate request](request.json), [response and token IDs](response.json), [native result](probe-result.json), [boundary summary](boundary-summary.json), [worker traces](serving-worker-traces.json), [candidate attestation](candidate-attestation.json), and runtime inspections [before](runtime-candidate-before.json) / [after](runtime-candidate-after.json)
- [Pretrial strict JSON smoke](baseline-smoke.json), [source registry](source-registry.json), and [projection ledger](projection-ledger.json)

The kernel measurement used one RTX PRO 6000 Blackwell Max-Q GPU. The single model request measured the two-GPU serve, TP2/DCP2, FP4 weights, FP8 KV, context 327,680, concurrency 4 and MTP 3. The ordinary masks match between versions on 271 of 272 rows; exact binary64-reference matches are 36 for stock and 35 for patch. The retained one-token difference is a diagnostic under the new policy. Earlier failed reports remain unchanged.

Public artifacts retain only synthetic workload material and allowlisted configuration or numerical evidence. Private topology, host/container identity, process IDs, GPU UUIDs, runtime paths and general startup warning text are omitted. The ledger binds retained private source receipts by SHA-256 and describes each projection. Kernel receipts are exact selected lines; the kernel summary is derived, not an independent measurement.

The pretrial JSON smoke failed strict output acceptance. The candidate returned correct numeric content with duplicated prose, two closing markers at positions 64 and 126, and zero token-0 outputs. The original symptom did not recur, but the full reasoning-limit gate failed. [Operational restoration](restoration.json) and [closure](closure.json) are complete; strict functional acceptance remains incomplete. The [manifest](artifact-manifest.json), [manifest source](artifact-manifest-source.json) and [authoritative summary](summary.json) bind the closed inventory. Kernel success is not a claim about model quality, reasoning closure, speed or promotion. Ignis remains held.

Additional evidence: [trace freshness](trace-freshness.json), independent [kernel](independent-kernel-review.json) and [live](independent-live-review.json) reviews, [restored runtime](runtime-restored.json), [router](router-restored.json), [mode](mode-restored.json), [shared memory](shared-memory-restored.json), [resources](resources-final.json), [restored smoke](restoration-smoke.json), and [friction log](friction-log.md).

The [initial baseline inspection](runtime-before.json) and [independent restoration review](independent-restoration-review.json) support final operational comparison. Both smokes failed, and their cache counts differ; they are not a performance pair.
