# ADR-0006 — Multiplexer swaps drain in-flight requests before evicting the resident model

- **Status:** Accepted
- **Date:** 2026-07-02
- **Relates to:** ADR-0002 (serves are compose-defined), the multiplexer single-resident swap model (`anvil_serving/multiplexer.py`), PR #98's parked follow-up list

## Context

The multiplexer serves ONE resident model per GPU and swaps on demand: a request
for a non-resident model stops the old backend container and starts the new one.
`Multiplexer.ensure_loaded` serialized the load/swap under a lock, but the relay
(the long-lived streaming copy of the backend's response to the client) ran
deliberately *outside* the lock so concurrent same-model requests don't serialize.

The gap: nothing connected the swap to those in-flight relays. A request for
model B while a request for model A was still streaming would `docker rm -f` A's
container mid-stream — the A client saw a connection reset partway through its
completion, with no error status (the 200 + headers were already sent). On the
fakoli-dark fast tier — two models sharing one GPU/port as a swap pair, driven by
alternating harness traffic — this is not a corner case; it is the steady state.

Constraints: stdlib-only (`threading` primitives, no async framework); the fix
must not serialize same-model requests (AC3: concurrent requests for the resident
model share it freely); and a swap must not be blockable forever by a hung client
(bounded availability for the requested model).

## Considered options

1. **Reader/writer lock over the whole request** — relays hold a shared lock,
   swaps take it exclusively. Rejected: an unbounded hold by one slow client
   blocks the swap forever, and stdlib has no fair RW lock; hand-rolling one adds
   more state than the problem needs.
2. **Reject non-resident requests while anything is in flight (503, client
   retries)** — simplest, but it pushes the drain problem onto every caller and
   makes swap latency visible as errors instead of waiting.
3. **Lease counting + condition variable with a bounded drain (chosen)** — each
   relay holds a *lease* on its model for exactly the duration of the upstream
   copy; a swap waits on a condition until the old resident's lease count reaches
   zero, up to a `drain_timeout`, then proceeds regardless (severing and logging
   any laggards).

## Decision

`Multiplexer` gains lease-based in-flight tracking, all under the existing lock
(now a `threading.Condition`):

- **`mux.lease(name)`** is the new serve-path API: a context manager that
  performs `ensure_loaded` and registers the in-flight lease *atomically* (both
  under the condition's lock), yields the backend `base_url`, and releases +
  notifies on exit. Atomicity closes the race where a swap lands between
  "ensure_loaded returned" and "the relay registered itself". The HTTP handler
  holds the lease for the entire upstream open + relay.
- **Swaps drain before stopping.** Inside the swap, before `backend.stop()`, the
  swapping thread waits on the condition until the old resident's lease count is
  zero or `drain_timeout` (default 30 s, CLI `--drain-timeout`) elapses. On
  timeout it logs the severed count and proceeds — a hung client bounds, never
  blocks, model availability. `--drain-timeout 0` restores the old
  swap-immediately behaviour.
- **New arrivals queue behind an in-progress swap.** `Condition.wait` releases
  the lock during the drain, so without a gate a stream of requests for the OLD
  model could keep taking fresh leases on the dying resident and starve the swap.
  A `_swapping` flag makes every `ensure_loaded`/`lease` acquisition wait until
  the swap settles, then re-evaluate residency (a queued old-model request then
  triggers its own swap back — thrash policy stays the router's residency
  concern, ADR-untouched).

## Consequences

- An active completion is no longer severed by a routine swap; swap latency for
  the new model is now bounded by `min(longest in-flight request, drain_timeout)`.
- Same-model concurrency is unchanged (leases are counted, not serialized), and
  `ensure_loaded` keeps its exact exception contract (UnknownModel / LoadError /
  BackendError) — the OOM guard still runs *before* the drain so a doomed swap
  never waits.
- The severed-laggard path still exists, by design, at the `drain_timeout`
  boundary; the handler's existing "backend unreachable → 503" mapping covers a
  request that loses the race, and the timeout event is logged with the count.
- Follow-up (out of scope here): swap debounce/hysteresis for alternating-model
  traffic, and surfacing drain waits in `/healthz` for operator visibility.
