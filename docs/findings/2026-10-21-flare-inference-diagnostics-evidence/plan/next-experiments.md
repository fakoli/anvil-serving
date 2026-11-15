# Next bounded Flare experiments

These are proposed follow-ups, not measurements or submitted patches. Keep the pinned checkpoint/package, TP2, recipe and300W limits. Use managed load/unload, exact retained restoration, fresh monitoring generation, and the reviewed trace command. Stop on identity, containment, CUDA, authentication or output-contract failures.

## 1. Distinguish cache order sensitivity from incompatible keys

Current evidence: every control block reused only9prompt tokens/request, restored zero host tokens, completed three host stores/request, and ended at52resident host records. Resident bytes are about8.73GiB of the32GiB budget. Earlier12-request working sets did reuse the host tier, so zero hits here do not prove a universally broken cache.

Smallest live experiment: fresh managed incarnation; use the identical52prompt bodies from this wave; prime once in forward order and replay once in reverse order atC1. Retain body hashes and exact order; do not insert unrelated preflight requests between prime/replay. Capture native counters before/after each phase and preserve all output failures. This is a cache compatibility probe, not a speed comparison. Reverse hits support retained-suffix/order sensitivity; zero reverse hits weakens that explanation and requires key/checkpoint lookup inspection.

Expose the demonstrated missing counters before changing policy: free/total row and page slabs, row-only versus page-pressure eviction attempts, retired leaf rows/pages, ancestor row compactions, pinned/non-durable/no-eligible outcomes, and lookup miss reasons. Keep labels bounded; do not export prompt hashes, request IDs or tokens as Prometheus labels. Reuse existing metrics snapshots and owning index/slab operations.

Source candidates at47581cca: helios/src/offload/slab.rs sized_counts fixes about one quarter of the budget for recurrent-state rows; memory.rs allocate can request eviction with need_row while pages remain free; index.rs evict chooses eligible leaves before compacting a single-child ancestor. None is yet a proven defect.

Use existing CPU MemoryCore/PrefixIndex test helpers to reproduce52rows under repeated branched checkpoint insertion. Assert stored/fetched values and pin/lifetime correctness; measure whether compaction preserves useful leaves. Three stores/request does not establish three independently useful endpoints. Do not hard-code a larger row ratio from that count.

Only after the reproducer and counters agree, compare one policy change at a fixed total byte budget. Preserve a small-working-set lane and a page-heavy long-context lane. Require the declared>=5% primary benefit and<=3% protected regression with three matched repetitions, numerical/continuation correctness, and exact restoration. A larger cache allocation is a separate resource tradeoff.

## 2. Finish repeated internal timing and observer-cost coverage

Use the retained1024-token fixture. WarmC1/C4 separately, then three trace-off and three trace-on repetitions per concurrency, with prospectively recorded order. Require exact output length, both ranks, zero drops, unchanged incarnation and valid clock anchors. A one-second trace samples part of decode; it does not cover whole-request latency. Report distribution and variability; a small observed difference does not prove zero observer cost.

Group trace samples by actual graph identity, queued state and physical lanes. Compare rank intervals without summing them. retained_ns is a prior shape-cost observation, not a plan estimate. Correlate requests only by overlapping process-clock windows until an explicit HTTP-to-slot lifecycle mapping is implemented and independently validated.

## 3. Bind the slow graph to a safe kernel reproducer

Earlier package/module joins nominate the routed W4A16FusedMoeKernel in Local Inference Lab b12x/CuTe DSL. Their overlapping summed kernel durations do not establish a critical-path fraction. The new pilot observes closely matched rank intervals for speculative4x3 only; it does not rule out stalls inside both ranks.

Capture a fresh short both-rank Nsight Systems node timeline and a separate hardware-metric interval with unprofiled controls. Check event-loss warnings, package/module hashes and unmapped fraction before interpretation. Verify metric units/denominators before claiming SM, tensor or memory saturation.

Use the pinned model geometry and numerical reference from existing b12x tuning fixtures. Preserve cooperative TP behavior; do not independently replay a kernel that waits on another rank. Nsight Compute is conditional on an isolated, numerically checked reproducer and a supported replay method. Candidate kernel changes are tile shape, shared/routed overlap, or transport scheduling only after measured attribution predicts the affected phase.

## 4. Make the workflow available to normal fleet callers

The trial checkout now has capacity config files, protected authenticated recipe probes, generation-bound monitoring and scheduler-trace capture. The existing kernel-tuning and observability skills carry the workflow. A new plugin would duplicate those owners.

Before fleet adoption, independently review and release the generic Anvil hooks through the native CLI/controller owners; prove exact version/source parity and fresh registration during a managed test swap. Keep secret files under their current protected owner. No route or client alias change is needed for diagnostics.

The current local Flare instrumentation patch is already a possible observability contribution. Show its final diff, live completeness evidence, observer-cost limitations and tests to the user before any upstream submission. A performance patch remains a separate result.
