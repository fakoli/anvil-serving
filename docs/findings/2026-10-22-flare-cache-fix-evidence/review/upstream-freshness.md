# Upstream freshness: row-share and host-admission observations

Observed **2026-10-23T00:21:01Z**. This is a read-only upstream comparison; it
does not rebase, fetch into, build, or otherwise mutate the frozen Flare
checkout.

## Pins and method

| Item | Value |
| --- | --- |
| Local frozen base | `47581ccac82660953a5b59cb04b2750d64edc97d` |
| Origin | `https://github.com/NVIDIA/flare-local-inference.git` |
| Upstream default branch | `main` |
| Upstream `main` at observation | `e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982` |
| Relationship reported by the official compare API | base is 132 commits behind `main` and zero commits ahead |

The remote ref came from read-only `git ls-remote --symref origin HEAD`; the
symbol comparison used the official `main` source, because the remote object is
intentionally not present in this frozen checkout. Sources: [current
commit](https://github.com/NVIDIA/flare-local-inference/commit/e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982),
[official comparison](https://github.com/NVIDIA/flare-local-inference/compare/47581ccac82660953a5b59cb04b2750d64edc97d...e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982),
and the [repository](https://github.com/NVIDIA/flare-local-inference).

## Exact affected surfaces

| Candidate surface | Current upstream finding | Contribution consequence |
| --- | --- | --- |
| `SlabPool::sized_counts` row share | [`slab.rs`](https://github.com/NVIDIA/flare-local-inference/blob/e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982/helios/src/offload/slab.rs#L235-L265) still calls `sized_counts(layout, bytes)`. The implementation still fixes rows at one quarter of the byte budget (`bytes / 4 / row_record`) before minimum/budget clamps ([lines 334-355](https://github.com/NVIDIA/flare-local-inference/blob/e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982/helios/src/offload/slab.rs#L334-L355)). It has no row-share parameter or equivalent configuration knob. | The local `row_percent` sizing/configuration change is not duplicated upstream. |
| `PrefixCacheOffload` configuration | Current [`PrefixCacheOffload`](https://github.com/NVIDIA/flare-local-inference/blob/e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982/helios/src/offload/mod.rs#L60-L75) exposes only memory bytes, disk, disk limit, and transport. | No upstream equivalent of the local `row_percent` or transfer-verification fields was found. |
| Host-admission observation | Current [`admit_with_host_store`](https://github.com/NVIDIA/flare-local-inference/blob/e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982/helios/src/server/runtime.rs#L2021-L2037) receives scheduler, store, spec, and prefill rate only. Its call site likewise has no metrics argument ([lines 1132-1138](https://github.com/NVIDIA/flare-local-inference/blob/e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982/helios/src/server/runtime.rs#L1132-L1138)). The current metrics source has `host_cache_fetched_total`, but no `host_cache_admission_*` family or the candidate row/pressure exports. | The raw-match, cost-rejected, capacity-rejected, and fetch-admitted observations are not already upstream under these names or an equivalent producer path. |

The present upstream `main` does retain `host_cache_fetched_total`; that is the
completed physical-fetch statistic already traced in the frozen evidence, not
an admission-observation replacement.

## Relevant upstream movement

No post-base commit changed `slab.rs`: its most recent upstream touch remains
[`e0d731e`](https://github.com/NVIDIA/flare-local-inference/commit/e0d731e146a87a2541f4cf554a06d574c5b747fb), before the frozen base. Upstream did
advance adjacent host-store contracts after the base:

- [`adc5654`](https://github.com/NVIDIA/flare-local-inference/commit/adc5654ab529b11546d8da81f436a4ad055498de), *Make multi-host prefix records safe when a peer fails*, changes host-store failure handling. In current [`PrefixStore`](https://github.com/NVIDIA/flare-local-inference/blob/e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982/helios/src/offload/mod.rs#L725-L745), `poll` returns `Result<()>` and a reporting failure stops the worker so transfer-held ids/pins are not reused. This is not a row-share or admission-counter implementation, but it is an allocator/restore contract change that a future upstream transplant must review.
- [`c2ee3b7`](https://github.com/NVIDIA/flare-local-inference/commit/c2ee3b7e15339961073b5ffef0130071920f5884), *Forward prefix cache settings to every Orbit rank*, affects the CLI forwarding seam. It does not add row share; a future contribution must preserve its current forwarding behavior.
- [`01a4e4d`](https://github.com/NVIDIA/flare-local-inference/commit/01a4e4da4248b25cc6289ec78f2428acf2d01cec), *Average planned verification depth across request rounds*, changes unrelated metrics.
- The current head, [`e5bd6a6`](https://github.com/NVIDIA/flare-local-inference/commit/e5bd6a67c0ae1d07f4f5dd52b8197cc36f462982), starts peer record transport before join and adds multi-host-cache qualification; it does not add an equivalent row-share or admission-observation surface.

The official open issue/PR lists and a focused repository search for `row`,
`slab`, `prefix cache`, `offload`, and `admission` found no published issue or
PR equivalent to the candidate knob/counters. The closed
[multi-host disk prefix caching PR](https://github.com/NVIDIA/flare-local-inference/pull/29)
is related host-cache work, not an equivalent implementation.

## Completeness and packaging decision

This comparison is complete for the named `sized_counts`,
`PrefixCacheOffload`, host-admission producer, and metrics-export surfaces at
the observed `main` SHA. It is partial for all 132 intervening commits and all
historical issue/PR discussion: GitHub's comparison file list is capped, and no
local remote object was fetched. The focused issue/PR query is a freshness
check, not a claim of exhaustive semantic search.

There is no upstream duplicate to remove from a future contribution package.
The frozen campaign remains correctly pinned to `47581cca`; **do not rebase it
for this evidence run**. If contribution packaging is later authorized, start
from then-current `main`, isolate the row-share/observation delta, and reconcile
it with the changed `PrefixStore::poll` failure contract and current CLI
forwarding before proposing anything. No upstream submission was made.
