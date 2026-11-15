# FLARE-FIX-001 correctness coverage — 2026-10-22

Read-only source/evidence review. No GPU work or HTTP request was performed.

## Current evidence

`offload-tests-memlock256m.log` records118 passed,0 failed,8 ignored. This is
real CPU offload/disk/composite coverage under the stated256MiB test-process
memlock allowance, not CUDA execution. It includes sizing/budget/minimum/count
bounds, alignment/disjoint slabs/double release, rank-address translation,
endpoint eligibility, parent pins, deferred children, row eviction/compaction,
multi-window ownership, partial submit failure, and disk/composite drain/error
paths. Existing scripted transfer replies exercise ownership logic but cannot
prove DMA completion, loaded-model state fidelity or live cancellation safety.
The offload filter does not include every scheduler/runtime phase test.

`pilot-a-restore-comparison.json` records case51 with one completed host fetch,
7699 host-reused tokens beyond9 GPU-reused tokens, zero measured prefill and
128 generated IDs exactly matching the recomputed reference. The full consumed
endpoint is7708tokens, remainder28 within a512token page. This is meaningful
end-to-end evidence for one partial-page checkpoint and128 continuation tokens
under armA. It is not a direct byte comparison, a candidate-arm acceptance,
C4/lifetime stress, or proof of every recurrent/draft-state path.

## Applicability of ignored GPU tests

`offload/transfer.rs::split_pools_cost_each_device_its_part_and_round_trip_units`
is applicable as a generic two-device mapping/transport smoke after the managed
model is unloaded. It creates two contexts, synthetic k/v page planes and10KB
rows, an8GiB host pool, and checks per-device mapped cost, byte-exact outbound/
inbound page+row transfers and memory release after drain. It calls default
`sized`/`sized_by_rank`, so exercises25%, not the75% candidate. It does not load
the retained TP2package, use its large continuation geometry or exercise model
recurrent/draft computation, request cancellation or pressure eviction. Retain
its bounded result as generic CUDA evidence only.

The other seven ignored offload tests are not drop-in tests for the retained
package. They authorize through `runtime_package/test_support.rs::authorize`,
which trusts the fixed test publisher and namespace`flare-tests`, and load
`&[0]`. The retained package has two rank bundles and the operator development
publisher. `runtime_package/model.rs:248` explicitly requires distinct selected
device count to equal rank count. Their authentication/device assumptions must
be adapted through supported authorization and both devices before claiming
exact-package validation; never bypass signature verification. Several tests
then take only the first manifest. The memory/composite tests also construct
registered/minimum pools rather than the exact32GiB rank-split75% pool.

## Minimum remaining genuine safety coverage

1. Establish a usable repeated-recomputation oracle, then require the same
   independently checked continuation in both arms at an exact partial-page
   endpoint. Include the fixed MTP configuration and retain native offered/
   accepted/speculative work. One direct exact-TP2 byte roundtrip through both
   ranks' actual24page planes and complete continuation row at the chosen split
   is the smallest strong transfer oracle: it checks recurrent, convolution,
   index-tail and draft payload bytes without interpreting generated prose.
   Source `glm53/state.py` includes recurrent/convolution state; contract.py
   declares complete continuation storage and a draft cursor. Do not infer
   semantic correctness solely from a pattern-copy test.
2. Exercise a real in-flight restore with concurrent pressure and cancellation:
   retain another waiter on the same chain, cancel one, let the other finish,
   then require exact output/byte fidelity, no early slab reuse, no stuck
   waiters and drained transfers. Combine with a child published while its
   parent is outbound and verify the parent's pin survives allocation pressure.
   This can be one bounded lifecycle fixture, not a separate test framework.
3. Exercise row pressure and protected page pressure in the candidate pool,
   checking counters, retained parents and eventual row/page reclamation.
   CPU bookkeeping tests already cover failure branches; for memory-only
   acceptance a live disk campaign is unnecessary, but GPU disk/composite
   behavior and general upstream compatibility remain unverified.

These are remaining acceptance obligations, not instructions to enlarge this
read-only task. C1/C4 correctness, interruption/drain, independent strict-output
quality and protected long-context workload gates remain required by the plan.
The synthetic split-pool test plus case51 do not discharge all of them.

## Case40 divergence

The receipt's first_divergence=7 means the eighth generated token, not token
value8. Case40 reference and replay both have7693prefill,9GPU-reused tokens,
zero host fetches/host reuse,128generated tokens and no recorded store/fetch
failures. Therefore a host restore was not the direct triggering operation for
this discrepancy. It does not prove the allocator or cache innocent: persistent
state, earlier transfer corruption, scheduling shape, speculative execution,
sampling state or numerical nondeterminism are not excluded by these aggregate
counters. Identical prompts, lengths and reuse counts do not prove identical
intermediate state, RNG progression or numerical execution.

Case51's exact restored continuation is positive local evidence, but cannot
cancel the failed all-case gate. Preserve `failed_exact_all_case_gate` and
`performance_eligible=false`. The smallest next diagnostic is repeated matched
case40 recomputation with identical settings, concurrency, starting-state
contract and native speculative-work accounting, paired with repeated case51
restore/recompute. If cold recomputation itself is unstable, identify the cause
or establish an independently justified numerical/logit oracle with tolerance
frozen before a new comparison; do not relax exact-token acceptance after seeing
this failure or use successful timing as correctness evidence.
