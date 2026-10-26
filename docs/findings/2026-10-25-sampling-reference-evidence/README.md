# Sampling-reference evidence

This focused preweight diagnostic compared the stock and patched split top-p kernels against a predeclared independent binary64 numerical reference. All eight forced-logit cases passed. Across eight ordinary cases and 272 rows, stock and patch were identical on 271 rows. On seed 58,231, vocabulary 154,856, batch 64, row 31, stock exactly matched the 114,428-token reference while the patch retained one additional token. That violated the stricter local no-worse policy, so startup stopped before model weights and inference.

- [Dated finding](../2026-10-25-sampling-reference.md)
- [Artifact manifest](artifact-manifest.json) and [manifest source](artifact-manifest-source.json)
- [Experiment plan](plan.json), [reference design](reference-design.md), [identity](identity.json), [source freeze](source-freeze.json), and [build receipt](build.json)
- [Verbatim public-safe kernel receipts](kernel-receipts.log), [derived kernel summary](kernel-summary.json), [independent outcome review](outcome-review.json), and [candidate exit status](candidate-status.json)
- [Sources](source-registry.json), [projection provenance](projection-ledger.json), [friction log](friction-log.md), [restoration](restoration.json), [strict JSON smoke](restoration-smoke.json), [closure](closure.json), and [decision summary](summary.json)

The kernel check measured one RTX PRO 6000 Blackwell Max-Q GPU. A planned two-GPU model serve did not reach weight loading. This is a functional kernel diagnostic, not a performance comparison, model-quality result, end-to-end reasoning-boundary fix, or promotion.

The ordinary difference met the upstream count-compatibility allowance but failed the predeclared stricter local policy because the patch was one token farther from the numerical reference than stock on that row. No tolerance, seed, case, or rerun was adapted after the result.

Public files are allowlisted projections of retained private managed receipts. Operator topology, host/container identities, GPU UUIDs, process IDs, command arguments, private paths, and raw warning text were omitted. Original-private hashes bind the source receipts; the projection ledger and artifact manifest bind the public bytes. The eight trace warnings came from the synthetic regression child and are represented only by bounded aggregate counts; they are not serving-worker trace evidence.

Exact operational identity, configuration, readiness, exclusive mode, GPU ownership, and router admission were restored with the engine idle and no OOM delta. The single restored-incumbent strict JSON smoke failed: reasoning-like prose preceded the requested object. Because no matched pretrial smoke exists and candidate weights never loaded, the failure is not attributed to the candidate. Operational restoration is verified; functional restoration acceptance remains incomplete. The campaign is closed, but its kernel and functional-smoke gates did not pass.
