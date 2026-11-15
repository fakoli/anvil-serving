# Anvil TensorFold telemetry contract v1

Frozen 2026-11-08 before qualification. Metric names below carry the
`tensorfold:anvil_` prefix. Counts are unitless; token/page gauges name their
units. Durations are monotonic engine-clock seconds, not HTTP/client latency.
`contract_info{version="1"} 1` identifies this contract.

- `streams_active`: total started streams in the engine-thread publication;
  omitted when the backend cannot report it. `streams_observed`: context-token
  observations actually present. `streams_complete`: 1 only when both counts
  and current running requests agree. `stream_context_tokens{stream="N"}`:
  occupancy of each observed stream, with indices local to one publication.
- `kv_allocation_available`: 1 only for a complete global logical decode-KV
  page pool. `kv_pool_info{scope="global_logical_decode_kv_pool"} 1`;
  `kv_allocated_pages + kv_free_pages = kv_total_pages`;
  `kv_reserved_pages <= kv_free_pages`; `kv_page_size_tokens` and
  `kv_pool_partitions`. Allocated pages are mapped pages with physical backing;
  free pages include promised but unmapped pages; reserved pages are those
  promises. Tensor-parallel replicas are counted once. Prompt-cache byte
  budgets and retained prompt-state bytes are never allocated decode KV.
  Unsupported or partitioned pools report availability 0, not guessed sums.
- `arrival_to_admission_seconds`, `admission_to_prefill_seconds`, and
  `prefill_to_first_token_seconds` are bounded cumulative histograms. Shared
  upper edges: 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120,
  300, +Inf seconds. `_count`, `_sum`, `_bucket{le="..."}` follow Prometheus
  semantics. Arrival is accepted engine submission after HTTP parsing and
  tokenization; admission is first scheduler admission; prefill-start is the
  first actual prefill launch, not stream setup; first-token is the first
  committed generated token, including reasoning. Each histogram records once
  when its later boundary is reached. Cancellation never creates a missing
  boundary. Requeue preserves first boundaries. `arrivals_total`,
  `admissions_total`, `prefill_started_total`, `first_tokens_total` count the
  corresponding boundaries. Background warm jobs are included and use the
  same engine-submission origin. HTTP admission and client timing stay separate.
- `full_context_bound_available`: 0 unless all explicit KV and admission-policy
  inputs exist. Missing declarations report
  `full_context_bound_info{reason="missing_admission_policy_inputs"} 1`.
  When available, `full_context_bound` is **additional full-context requests**:
  `min(admission_limit-active_requests,
  floor(max(0,free_pages-reserved_pages-reserve_pages) /
  ceil((requested_context_tokens+per_request_reserve_tokens)/page_size_tokens)))`.
  Required inputs: nonzero requested context/page size, one global pool partition,
  explicit full-context reservation policy, policy reserve pages, per-request
  verification/scratch token reserve, admission limit and active request count.
  `_info` declares semantics, policy and formula_version="1"; gauges expose
  `full_context_requested_tokens`, `full_context_reserve_pages`,
  `full_context_per_request_reserve_tokens`, `full_context_admission_limit`,
  `full_context_active_requests`. Missing or inconsistent inputs yield unavailable.

This ABI does not change scheduling, cache policy, model, route, context or output
settings. CPU fake-engine tests are contract checks, not live GPU qualification.
