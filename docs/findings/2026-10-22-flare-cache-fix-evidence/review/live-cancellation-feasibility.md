# Live cancellation during shared restore: feasibility

2026-10-22, one bounded read-only source pass. No hardware, HTTP, builds, lifecycle changes or source/harness changes. Existing successful A/B byte audits are reused; no sampling investigation was repeated.

**The frozen managed runtime can attempt a real disconnect while two matching requests restore, but its existing external evidence cannot prove the requested lifetime invariant. Do not close the live-cancellation coverage item from such an attempt.** No additional live attempt is needed merely to obtain an ambiguous pass. The current formal image/freeze should remain unchanged.

## What the existing path supports

`server/http.rs:310-318` submits requests and awaits scheduler admission. The worker acknowledges admission while the request may still be Inbound (`server/runtime.rs:1190-1205`), so a streaming client can be admitted before the transfer finishes. Dropping its Generation sets the cancellation flag (`server/runtime.rs:120-123`); the worker observes that flag, calls scheduler.cancel and increments the cancellation counter (`runtime.rs:1031-1044`). The existing HTTP disconnect test confirms cancellation propagation while waiting (`server/runtime/phase_tests.rs:1947`).

Matching requests join one inbound ticket without issuing another fetch (`runtime.rs:1956-1984`, `scheduler.rs:1244,1356-1368`). Cancelling an Inbound request removes that waiter from the ticket while the ticket continues owning the destination resources (`scheduler.rs:2468-2473`). Only the store completion event calls finish_inbound (`runtime.rs:1826-1860`); successful completion publishes the destination checkpoint and pins it for surviving waiters (`scheduler.rs:1446-1517`). The production transfer path drains on failure and reports completion only after its stream work finishes (`offload/transfer.rs:553-600`).

These are source-supported ownership semantics, not evidence that a particular live disconnect occurred before DMA completion.

## Exact external evidence gap

The available evidence cannot join these facts into one observed interval:

1. Two identified HTTP requests are waiters on the same identified prefix ticket.
2. The worker services one identified cancellation while at least one rank's transfer completion is still pending.
3. That ticket's exact page/row allocations stay reserved through every rank's completion, while the other waiter survives and completes.

Prometheus exposes aggregate inbound-in-flight, inbound-waiting and cancelled counts (`server/metrics.rs:1015-1028` and the cancellation counter). They have no request-to-ticket/chain identity and are refreshed by worker passes; a positive gauge can describe a completion not yet consumed by the worker. Scrapes are not atomic ownership/event records. Even a scrape showing two waiters followed by one cancellation and one survivor cannot prove cancellation preceded physical copy completion or that no particular allocation was briefly reused.

The opt-in byte audit emits one completed-job line per rank with direction/pages/row/bytes/copies/failures (`offload/transfer.rs:580-599`). It does not emit transfer-start timestamps, request IDs, ticket keys, allocation identities, cancellation timestamps or pin/release events. Audit equality proves the audited copy, not the absence of early reuse during an unobserved interval. It also changes timing and remains excluded from formal timing arms.

The authenticated `/debug/scheduler-trace` is not the missing oracle: `server/scheduler_trace.rs:112-152` serializes completed model-graph samples with slot/generation, graph/fence and timings. It does not serialize host-transfer jobs, cancellation events, ticket membership or resource lifetime. The terminal detail structure likewise contains request phase/counters, not the required ticket/allocation correlation (`server/metrics/scheduler.rs:52-71`). Client close time is only a client action, not the worker's cancellation-service timestamp. A longer prompt or faster polling cannot repair these identity and ordering gaps.

## Existing focused test coverage

- `server/runtime/phase_tests.rs:1190`: host-hit waiter stays Inbound until controlled StoreEvent::Fetched, then Restore/Resume; also checks checkpoint pins.
- `server/runtime/phase_tests.rs:1331`: two requests join one fetch and produce two inbound waiters.
- `scheduler/tests.rs:2520`: alias backing survives cancellation of both waiters until transfer completion, success and failure branches.
- `scheduler/tests.rs:2672`: cancellation of a sole inbound waiter retains ticket page/row resources until finish_inbound, including failure cleanup.
- `scheduler/tests.rs:2578` vicinity: two surviving waiters restore and finish from a shared ticket.
- `server/runtime/phase_tests.rs:1060-1135` vicinity: store/executor drain ordering and retained ownership when drain fails.

These deterministic tests cover pieces of the invariant. They do not combine a real network disconnect, one surviving waiter and real asynchronous TP2 transfer overlap. This pass inspected them without rerunning tests.

## Smallest later source test

First add one focused integration case to the existing `server/runtime/phase_tests.rs` fixture, using its existing FakeStore completion queue (`phase_tests.rs:986-1043`), not a new framework:

1. Admit two same-chain requests; assert exactly one fetch, two Inbound waiters and capture the ticket's destination pages/row.
2. Withhold Fetched; drop the first Generation (or abort the existing HTTP response fixture) and let the worker service cancellation.
3. Assert the cancelled handle is absent, the second handle remains Inbound, the original ticket/destination allocations remain owned, and bounded allocation pressure cannot obtain those same page/row IDs. Do not require all free counts to stay identical: cancelling a waiter may legitimately release its private resources.
4. Release successful Fetched; advance the surviving waiter through Restore/Resume and completion. Assert only that waiter receives completion, no additional fetch occurred, and the ticket becomes reclaimable only after its final pins/fences release. Repeat the controlled failure branch to verify clean readmission and drain ownership.

This is the smallest missing composed **CPU lifetime test** and must be labeled as such. It does not claim physical DMA overlap.

If genuine live TP2 overlap is a mandatory acceptance item, a separately built, later diagnostic test needs a narrowly scoped test-only transfer gate plus an ordered receipt: request handles/HTTP correlation, ticket and destination identities, cancellation-serviced event, and per-rank transfer-completed events. A gate holding a submitted restore before its completion publication can deterministically test cancellation while the restore owns resources; it must not be labeled cancellation during active DMA unless an actual unfinished CUDA-event interval is also demonstrated. Existing frozen production APIs supply neither gate nor receipt. Keep that stronger item explicitly unexercised rather than silently replacing it with the CPU case or a successful byte audit.

No change to current diagnostic matrix authorization is inferred here. This note resolves feasibility and evidence scope only; it does not waive the outstanding correctness item.
