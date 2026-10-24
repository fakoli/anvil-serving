# Diagnostic interpretation

This is an observational stack campaign, not scored qualification. Keep every failed attempt in its original population; do not compute an overall winner after the incumbent C4 failure.

For each completed block retain the native metric distributions, actual input/output counts, TTFO versus visible TTFT, client generation and E2E latency, throughput, and failure denominator. Capacity TPOT is generation duration divided by completion tokens after the first; it is not a distribution over individual token intervals. C1 per-block p99 with52 requests is descriptive only. Keep priming outside measured statistics. Native cache-hit counters determine cache reuse, not the word warmup.

For scheduler traces, group by runtime graph index/kind/shape and scheduled physical lane count, with queued and unqueued samples separate. Compare each rank event interval and their maximum; never sum ranks. Retain selection-to-commit, commit-to-completion-observation, retained shape-cost observation at selection versus the source-equivalent measured cost, prior-cost-observation age, freshness and refresh origin. Host wall time includes staging, coordination, device queueing and delivery. Its difference from max-rank time is not a pure CPU or launch-cost estimate. Bound UTC alignment using both before/after monotonic anchors; compare only records from the same container incarnation.

For fixed-output requests retain ingress/enqueue, native token timestamps, client arrivals, exact1024-token completion and7037-token prompt assertions, and all failures. Token timestamps use a process-local origin. An omitted reasoning-effort field uses the pinned source default; it is not evidence of a client override. This fixture has no semantic correctness oracle. Trace-off and trace-on runs remain separate. Three repetitions expose variability; a small difference does not prove negligible observer overhead.

For Nsight retain the raw report, warnings, SQLite, capture interval, rank/graph/module mapping and unmapped fraction. Reuse the authenticated package/cache mapping only after source and package hashes match. Summed kernel times overlap. Hardware samples cover the entire device, so SM/tensor/DRAM activity cannot be assigned to one kernel without a further isolated component measurement. Do not infer bandwidth saturation from an unverified metric denominator.

Optimization decision: only propose a code change after time is attributed to a shared code path and a measurable hypothesis predicts which phase improves. Candidate directions include scheduler cost freshness/launch windows, TP rank imbalance or transport, speculative graph shape/acceptance, and routed MoE cooperative tile scheduling. All remain hypotheses until this wave supplies evidence. No power-limit changes, unsafe cooperative-kernel replay, re-quantization, or upstream submission belong to this wave.

## Independent timing-review corrections

The trace does not carry the frozen submitted plan estimate. `retained_ns` is the retained shape-cost observation selected for that plan; compare it to max-rank GPU time when queued, otherwise max(max-rank GPU time, host wall time), matching the cost estimator. `queued` means another executor frame was outstanding at submission, not that an HTTP request waited. Prior observation age is selected_at_ns minus selection.observed_at_ns.

The request array contains physical scheduler slot/generation handles, not HTTP request IDs. Graph token/prefill/packed fields are capacities, not actual lane token counts. Client/native correlation is process-clock window overlap only; this trace cannot assign each graph to a specific HTTP request or token. These gaps remain explicit rather than inferred from chronological proximity.

Workload seeds4342/4642 bind deterministic prompt prefixes/content. The capacity runner does not forward them as API sampling seeds. Do not describe this as matched independent sampling-seed replication. The native fixed-token fixture separately sends API seed42.
