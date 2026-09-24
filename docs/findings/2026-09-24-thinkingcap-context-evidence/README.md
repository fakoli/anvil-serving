# ThinkingCap RTX 5090: 128K context extension

This extends the previously qualified 32K profile after the user required at least 128K. The checkpoint, full vision assets and serving image remain pinned. The current candidate uses MTP3, BF16 KV and generic UVA offload of 3.02 GiB of weights, including the vision tower. It has passed function, vision, reasoning, repeated quality, strict short-output capacity, the full 60-case text context matrix, and the synthetic agentic gate. The earlier selective-vision, no-MTP branch failed quality and is retained as rejected evidence.

- [Run plan and superseding 128K requirement](run-plan.json), [workload manifest](workload-manifest.json), [source registry](source-registry.json), and [independent plan review](context-plan-review.json).
- [Current startup calibration](calibration-mtp-uva3.json), [native engine log](native/mtp-uva3/startup.log), [functional gate](native/mtp-uva3/functional.json), [vision corpus](native/mtp-uva3/vision.json), and [repeated quality](native/mtp-uva3/quality.json).
- [Current pinned recipe](configurations/thinkingcap-awq-vllm029-5090-128k-mtp3-bf16-uva3.toml) and [MTP/UVA3 feasibility](feasibility-128k-mtp-uva3.json).
- [Rejected no-MTP calibration](calibration.json), [quality failure](native/candidate/quality.json), and [earlier GPU-only feasibility](feasibility-128k-result.json).
- [Current quality summary](quality-summary.json), [strict capacity comparison](capacity-summary.json), [comparison chart](benchmark-matrix.svg), [MTP/UVA3 context scout](context-scout-summary.json), [full context summary](context-full-summary.json), and [agentic summary](agentic-summary.json).
- [Initial GPU reservation failure](native/failed-gpu-reserve/startup.log), [friction](friction-log.md), and [operational source identity](operational-source-check.json).
- [Coverage](coverage-and-gaps.md), [final resource and ending identity](final-resource-summary.json), [campaign state](campaign-state.json), [ending state](restoration.json), [decision summary](summary.json), and [artifact ledger](artifact-manifest.json).
- [Publication summary](publication-summary.md) and [independent pre-final review](pre-final-review.json).

Only the local RTX 5090 is measured. No route, client alias or deployed topology is changed. Native evidence remains authoritative; redaction ledgers preserve original and public hashes while normalizing operator identities. The prior 32K measurements and decisions remain unchanged; its public bundle was privacy-resealed as recorded in its additional redaction ledger.

Recipe files preserve their launch-time candidate annotations and exact bytes; subsequent qualification belongs to the decision and native result records. Embedded native proof paths and hashes refer to original inputs. The applicable redaction ledger maps those names and original hashes to each public path, hash and byte count; do not treat a redacted file as byte-identical to its private source.

Durable job exports retain both the native `benchmark-result/v1` outer artifact and its extracted, unchanged `benchmark-evidence/v1` content. The existing evidence inspector accepts the companion and rejects the outer wrapper; the [product gap](product-gaps/2026-09-24-benchmark-evidence-job-wrapper.md) records this tooling limitation separately from model correctness.

## Publication privacy correction

Before merge on 2026-09-24, remaining operator container IDs were replaced with stable synthetic labels. The [additional redaction ledger](merge-publication-redaction.json) records the prior sealed-manifest hash and exact per-file hash transition. Measurements and campaign decisions are unchanged; the manifest now binds the sanitized publication bytes. The seal checks in `validation-final.json` describe the original campaign snapshot, before this publication correction.
