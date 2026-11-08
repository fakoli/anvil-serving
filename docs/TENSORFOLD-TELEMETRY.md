# TensorFold telemetry contract v1

The `tensorfold-prometheus-v1` collector reads the engine's bounded `/metrics`
response. Configure it once, then inspect it with the existing collector command:

```sh
anvil-serving collectors configure --name engine-telemetry --adapter tensorfold-prometheus-v1 --endpoint http://127.0.0.1:30000/metrics --capability active-streams --capability global-kv-allocation --capability engine-admission-timing --output engine-telemetry.json --confirm
anvil-serving collectors inspect --config engine-telemetry.json
```

Non-loopback private endpoints require an authentication environment reference.
The collector performs one read, bounded to 256 KiB and the selected timeout;
it sends no inference request and changes no model, admission, route or cache policy.
`anvil_serving.tensorfold_telemetry.parse_metrics(raw)` exposes the same parser
for a content-bound benchmark collector. Missing contracts or inputs remain
`unavailable`; missing series are never interpreted as zero.

## Frozen names and meanings

All names below have the `tensorfold:` prefix. The marker
`anvil_contract_info{version="1"} 1` selects this exact contract. A different
version requires a reviewed parser change. Counts/pages/tokens are nonnegative
integers; ratios range from zero to one; durations and histogram sums are seconds.
No request identifiers, prompts or per-request timestamps are exported.

| Metric | Meaning |
| --- | --- |
| `anvil_streams_active` | Engine-published count of started streams, including streams omitted from the bounded occupancy snapshot. |
| `anvil_streams_observed` | Streams included in that snapshot. |
| `anvil_streams_complete` | One only when the snapshot covers every active stream and its active count matches current running requests; zero otherwise. |
| `anvil_stream_context_tokens{stream="N"}` | Current context tokens of a sample-local stream index, never a stable request identity. |
| `kv_cache_usage_ratio{pool="N"}` | Existing per-stream context occupancy ratio; its `kv_cache_usage_perc{stream="N"}` alias must agree. It is not global allocation. |
| `anvil_kv_allocation_available` | One only when the entire supported decode allocator is observed. |
| `anvil_kv_pool_info{scope="global_logical_decode_kv_pool"}` | Logical decode pool scope; TP rank copies are not summed. |
| `anvil_kv_pool_partitions` | Must equal one for complete global allocation; partial or unsupported partitions remain unavailable. |
| `anvil_kv_allocated_pages`, `anvil_kv_free_pages`, `anvil_kv_total_pages` | Physically mapped logical pages, unmapped pages and pool capacity; allocated + free = total. Allocated includes retained allocator ownership. |
| `anvil_kv_reserved_pages` | Promised but unmapped pages, a subset of free pages. |
| `anvil_kv_page_size_tokens` | Positive logical tokens per page. |
| `anvil_full_context_bound_available` | One only when every derivation input and the admission policy is known; zero is unavailable, not a zero-request bound. |

The unsharded decode allocator supplies global allocation. Existing shard-part
metrics remain separate and do not imply a complete global pool. Prompt-cache
bytes/states are never substituted for decode KV pages.

## Timing boundaries and bounded histograms

`anvil_arrival_to_admission_seconds`, `anvil_admission_to_prefill_seconds` and
`anvil_prefill_to_first_token_seconds` use cumulative Prometheus `_bucket`,
`_sum` and `_count` series. Their fixed `le` edges are
`0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, +Inf`.
Buckets/counters are process-lifetime cumulative and reset on engine restart;
compare deltas only under unchanged process custody. Histograms retain no
unbounded request history. Each histogram is updated only when both boundaries
were observed, so cancelled or rejected requests need not enter every stage.
`anvil_arrivals_total`, `anvil_admissions_total`, `anvil_prefill_started_total`
and `anvil_first_tokens_total` expose the boundary counts in that order.
Counts cannot increase along the request path. Complete timing requires each
histogram count to equal its ending boundary count; missed preceding boundaries
remain explicit incomplete timing rather than being silently excluded.

The population includes background engine jobs, such as automatic next-turn
warm prefills. The ABI does not split foreground and background jobs; reconcile
these counts with HTTP responses only when the job populations are independently
known. A background prefill can reach admission without generating a first token.

Arrival is engine submission after HTTP handling/tokenization. Admission is the
first successful engine admission. Prefill starts at the first actual prefill
launch, including the corresponding cached path. First token is the first
generated token, which may be reasoning or special output. HTTP/tokenization,
network time, client TTFT and visible-answer TTFT are separate measurements.

## Conservative full-context bound

The result has status `estimated`, method `derived`, and semantics
**additional full-context requests from this snapshot**,
not a measured useful-context capacity or a throughput promise. The version-one
formula is:

```text
pages_per_request = ceil((requested_tokens + per_request_reserve_tokens) / page_size_tokens)
usable_pages = max(0, free_pages - reserved_pages - reserve_pages)
bound = min(admission_limit - active_requests, usable_pages // pages_per_request)
```

Availability requires `anvil_kv_pool_partitions 1`, complete global KV,
`anvil_full_context_bound_info{semantics="additional_full_context_requests",policy="full_context_reservation",formula_version="1"} 1`,
and these exact inputs: `anvil_full_context_requested_tokens` (positive),
`anvil_full_context_reserve_pages`,
`anvil_full_context_per_request_reserve_tokens`,
`anvil_full_context_admission_limit`,
`anvil_full_context_active_requests`, and `anvil_full_context_bound`.
Active requests cannot exceed the admission limit. Reserve tokens include any
required verification/scratch window; assuming that window is zero is forbidden.
The collector recomputes the formula and rejects disagreement.

Until the runtime can prove all policy inputs, it exports availability zero and
`anvil_full_context_bound_info{reason="missing_admission_policy_inputs"} 1`.
No guessed concurrency is published. Telemetry qualification does not establish
full benchmark-family coverage or alter an existing historical comparison.
