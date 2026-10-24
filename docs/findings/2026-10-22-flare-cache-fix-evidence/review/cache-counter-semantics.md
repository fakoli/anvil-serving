# Cache counter semantics for closure

Source-verified on 2026-10-22 by an independent read-only explorer, then hashes
captured by the parent. This explains retained counters; it is not a new model
measurement or a numerical/performance acceptance result.

Source HEAD is `47581ccac82660953a5b59cb04b2750d64edc97d` with the retained local
instrumentation. `helios/src/scheduler.rs` has no local diff. Relevant SHA-256:

| File under Flare checkout | SHA-256 |
| --- | --- |
| `helios/src/scheduler.rs` | `0f0e7b293a23ce34b05df4061110d43069ed5e650f2a4c291ed273295ad09458` |
| `helios/src/server/runtime.rs` | `0f35cf9a7715ca17f66ea0185d246cef0f51c2b6b6c987eff248f25097744c01` |
| `helios/src/server/metrics.rs` | `4bdf6786ecbb8946ae30faaec3708e6b48aa777ce1badd313e14634698dd4143` |
| `helios/src/server/runtime/phase_tests.rs` | `b21df8e9ad32b725b7f5934820441138483b245eeb35479c176f1f701081aa78` |

The last file includes the later CPU-only test delta; that is outside the frozen
v2 image. Counter producers in the other three files are unchanged by it.

## Token accounting

`flare_cache_reused_tokens_total` exports scheduler `reused_tokens`
(`metrics.rs:525–540,791–794`). Its sole producer adds the consumed depth G from
the GPU cache lookup when a request is admitted
(`scheduler.rs:1196–1219,1252–1255,1439–1442`). Thus it describes GPU-ready reuse
at that admission, not an exclusive GPU-origin history.

`flare_host_cache_reused_tokens_total` exports `host_reused_tokens`
(`metrics.rs:525–534,856–859`). Host lookup extends the GPU plan
(`runtime.rs:1930–1961`). Admission of or joining an inbound ticket through depth
C adds C−G (`scheduler.rs:1253–1268,1341–1373`), before transfer completion. Shared
waiters count their own extension; cancellation or later failure does not make
this an automatically completed-transfer count.

`flare_prefill_tokens_total` adds real consumed prompt work from successful
Prefill-lane graph completions, bounded by lane tokens and excluding padding
(`runtime.rs:1276–1330`, `metrics.rs:392–403,734–739`).

For one successful, clean admission of prompt length P, the segments are
disjoint: **P = G + (C−G) + (P−C)**. For G=128, C=512 and P=800, generic reuse
is 128, host extension is 384 and completed prefill is 288. The frozen serial
replay asserts this equation while separately rejecting cancellation, failed
fetch/store, reset and interference deltas (`harness/replay.py:235–244`).

Do not generalize that clean-request equation to a unique-token inventory or
unfiltered failed/retried/concurrent populations. A completed host restore
publishes a GPU checkpoint (`scheduler.rs:1445–1515`); a later request can count
the same logical prefix in generic reuse. A failed restore is readmitted with
host lookup disabled (`runtime.rs:1825–1895`). Admission counts alone therefore
cannot prove the causal step “completed eligible reuse.” Keep the raw counters,
request outcomes, transfer-completion counters and failure/drain evidence together.

This refines the analysis README's conservative tier-label caveat; it does not
change the frozen harness, merge counters, or discard any failed denominator.

## Admission versus completed physical fetch

Follow-up source trace (same pin and unchanged files) identifies the exact
stages. All admission names below use prefix `flare_host_cache_admission_`.

| Suffix / counter | Increment point | Counting unit |
| --- | --- | --- |
| `raw_lookup_match_total` | Host lookup matched, before `worth_fetching` (`runtime.rs:1939–1949`) | Admission attempt; not unique request |
| `cost_rejected_total` | Matched chain fails transfer-cost eligibility (same range) | Rejected admission attempt |
| `capacity_rejected_total` | Worthwhile chain cannot be admitted; falls back to recomputation (`runtime.rs:1956–1969`) | Rejected host admission attempt |
| `fetch_admitted_total` | Waiter joins an existing inbound ticket or a new fetch is accepted (`runtime.rs:1971–1985`) | Accepted waiter, not completed physical fetch |
| `fetch_failed_total` | Synchronous fetch rejection or asynchronous failed chain event (`runtime.rs:1825–1864,1977–1982`) | Rejection/event; asynchronous failure is per chain |
| `flare_host_cache_fetched_total` | Successful final inbound transfer emits `StoreEvent::Fetched(Ok)` | Completed physical chain |

The completed physical-chain counter comes from store stats, not scheduler
admission: `offload/memory.rs:713–739`, `offload/disk_store.rs:1905–1921`, and
`offload/composite.rs:752–767`. Failure increments the store's `fetch_failed`
instead. Worker event handling finishes the inbound ticket and refreshes store
metrics (`runtime.rs:1844–1865`); successful scheduler completion publishes the
GPU checkpoint and moves waiters to Restore (`scheduler.rs:1445–1515`).

Two shared waiters can produce two admission/fetch-admitted increments but one
physical fetch. A cancelled waiter may already have counted host reuse while
its ticket continues for the survivor. Failed-chain survivors are requeued
with host lookup disabled, so the retry recomputes (`runtime.rs:1855–1895`).

For a controlled serial request, a completed eligible restore is supported by
raw-match=1, cost/capacity rejection=0, fetch-admitted=1, admission/store fetch
failures=0, physical fetched=1, positive host-reused tokens, one completed
request, no cancellation/worker error, drained inbound work and the clean prompt
accounting equation. Use the native before/after Prometheus captures when the
attempt's selected delta dictionary omits a new admission counter. These checks
do not alone prove a latency benefit, numerical fidelity or exclusive origin.

Do not impose the serial 1:1 relation on the concurrent decode batch. Preserve
its batch counters and shared-ticket semantics without invented per-request
attribution.
