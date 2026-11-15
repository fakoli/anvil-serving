# Exact production-transfer verification feasibility

**Recommendation: implement one default-off diagnostic flag through the existing
offload options and per-rank transfer workers.** This is feasible without new
snapshot graphs, model-package changes, publisher exceptions or GPU allocation.
Run it only for correctness pilots in both arms; disable it for performance.
It proves actual source/destination transport bytes, not equivalence to uncached
model computation or correctness of the checkpoint's original contents.

## Existing seams

- `flare/src/cli.rs` already maps offload CLI/config into
  `PrefixCacheOffload`; carry one boolean through `Prepared`/`Resolved` and
  `offload/mod.rs::open` to both memory and staged-disk `Transfers::start`
  paths. No environment variable or per-model policy is needed. Keep current
  `start` as the default-off entry if that avoids changing every test caller.
- `offload/transfer.rs::RankWorker::open` owns the rank context, lowest-priority
  `Stream`, blocking `Event` and reusable descriptor arrays. Allocate optional
  verification scratch here, before the worker's ready handshake.
- `RankWorker::run` calls validated `expand`, groups descriptors with
  `batch_end`, performs `copy_batch`, then synchronizes. This is the smallest
  common place to observe every actual page plane and complete row in both
  directions and every rank, while retaining the normal copy implementation.
- `cuda.rs::Stream::{host_buffer,download,mapped_host_address,record}` and
  `Event::synchronize` suffice. `PinnedHost::{bytes,bytes_mut}` supplies bounded
  CPU views. No hashing or CUDA graph operation is necessary.

## Exact bounded algorithm

Use one pinned readback buffer and one ordinary expected-byte buffer, each
`BATCH_BYTES` (currently16MiB), per rank. Preserve existing descriptor order,
`batch_end` and original `copy_batch` arrays. For each original batch:

1. Capture each source descriptor before issuing the production batch.
   Outbound: download the model's device source into the pinned buffer on the
   existing rank stream, wait on its event, and copy those bytes into that
   descriptor's packed range in the expected buffer. Inbound: copy the pinned
   slab's CPU-readable source directly into that expected range.
2. Issue the unchanged production `copy_batch` and wait on the same rank stream's
   event. Do not publish a completion yet.
3. Compare each completed destination against its expected range. Outbound:
   compare the slab's CPU-readable destination. Inbound: download the model's
   device destination into the reusable pinned buffer, wait for completion,
   and compare its bytes. Bound every offset/sum by the original batch's total
   bytes. Existing `download` writes at offset0, so descriptor-at-a-time readback
   avoids a new offset-aware CUDA API.
4. Count checked production bytes separately from physical verification traffic.
   Advance to the next original batch only after comparison completes.

Resolve CPU slab pointers through `Stream::mapped_host_address` for the complete
descriptor range. **Do not cast a RankPart device alias or synthetic slab
identity to a CPU pointer.** Split pools map only each rank's own part; existing
pointer-attribute validation checks HOST memory and contiguous endpoint mapping.
Failure to obtain the required mapping must fail verification, not skip bytes.
Compare only `unit_bytes`, not uninitialized alignment padding.

## Ownership, errors and evidence

Published GPU checkpoints are pinned before stores start; host slabs remain
owned until replies; inbound model destinations cannot be consumed until the
combined reply completes. Verify within that existing lifetime. Outbound source
immutability and inbound source stability remain required contracts; the new
checks must not release pins or return early. All ranks are checked independently.

Return mismatch/driver errors from `run`. Existing `serve` drains before sending
failed `RankReply`, and `Transfers::absorb` waits for every rank, retaining the
first error. Preserve that behavior. Report rank, direction and bounded
descriptor/byte offset only; never report payloads, host addresses or contents.
The normal failed-restore recomputation path can hide the error from a successful
HTTP response, so the diagnostic pilot must fail on any verification mismatch,
not merely inspect final request success.

Expose verification enabled plus checked outbound/inbound bytes or completed
checked windows and failures through a bounded existing diagnostic/startup
surface. A successful pilot needs nonzero checked inbound AND outbound counts
for both ranks with page and continuation-row coverage; zero mismatches alone
does not prove a transfer occurred. Include an intentional synthetic mismatch
test proving error propagation, chunk/offset checks and multi-rank aggregation.
Do not deliberately corrupt real model payloads for that negative test.

## Size, overhead and limits

Bounded scratch is32MiB/rank,64MiB total for TP2:32MiB pinned plus32MiB ordinary
host memory, allocated only when enabled. No additional device allocation is
needed. Existing16MiB chunks cover the81,464,576-byte per-rank row in several
pieces; `expand` also covers all24page planes/rank, including draft planes.
Host budget/containment reporting must include this additional scratch outside
the32GiB retention pool. Fail startup if scratch cannot be allocated.

The extra DtoH traffic, CPU copies, memcmp and per-descriptor synchronization
can materially perturb scheduling, transfer estimates, admission eligibility
and races. This is expected diagnostic overhead; timing and restore rates from
verification-enabled runs cannot establish A/B performance. Same signed package,
same image, explicit flag and identical instrumentation settings in each pilot
arm preserve the comparison boundary. Keep normal performance arms disabled.

This requires CLI/options plumbing, transfer scratch/comparison logic and focused
tests; it is a small coherent diagnostic change rather than a one-line assertion.
It still does not prove cancellation under pressure, semantic recurrent/draft
state construction or numerical equivalence to recomputation. Those remain
separate correctness gates.
