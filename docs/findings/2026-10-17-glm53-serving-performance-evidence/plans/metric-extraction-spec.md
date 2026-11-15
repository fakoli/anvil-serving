# Cell metric extraction spec

Private preparation only. Applies to counted matrix cells after the frozen plan
receipt. The two completed baseline warmups are excluded from measured coverage;
they confirm the native capacity shape only.

## Native record

For each `anvil-serving.benchmark/v1` file, preserve the file unchanged and
create a derived cell record referencing it. Bind `model`, `engine`,
`measurement_protocol`, `workload_identity`, `context_tokens`, `concurrency`,
`requests`, `max_tokens`, prompt-cache mode, and sampling to the frozen arm and
cell. Require `completed + failed == requests` and retain all `failures`.

Map the 17 required metric families as follows; no numeric value is invented:

| Coverage metric | Native/telemetry source | Form |
|---|---|---|
| effective prefill | `metrics.effective_prefill_tok_s_*` | per-request distribution; samples/p50/p95 |
| TTFO | `metrics.time_to_first_output_*_ms` | distribution; samples/p50/p95 |
| visible TTFT | `metrics.ttft_*_ms` | distribution when non-null; otherwise `not_collected` with protocol evidence |
| decode | `metrics.decode_tok_s_*` | distribution; samples/p50/p95 |
| TPOT | `metrics.tpot_*_ms` | distribution; samples/p50/p95 |
| E2E | `metrics.e2e_*_ms` | distribution; samples/p50/p95 |
| aggregate throughput | `metrics.throughput_tok_s` plus `throughput_performance_eligible` | scalar, never percentile distribution |
| input/output counts | prompt/completion `*_samples`, `*_p50`, `*_p95`, totals; `request_timings` | distributions and total counts |
| client overlap | `client_concurrency_observed_max` | measured client stream-call overlap only |
| finish/canary/output control | `request_timings`, `failures`, `metrics.request_canary_*`, `metrics.controlled_output_*` | eligibility/disposition, not substituted timing |
| engine phase histograms | before/after raw metric exposition | histogram bucket/count/sum deltas |
| engine active/queued/KV use | timestamped engine samples | sampled maxima and interval, explicitly lower bound |
| KV allocation | engine cache configuration | observed allocated pool/bytes if exposed |
| derived full-context concurrency | allocated token pool / actual full context | separately labeled estimate; never measured occupancy/capacity |
| GPU memory/utilization | per-device collector samples | sampled peak used, min free, max utilization per device |
| host/cgroup memory | host MemAvailable plus cgroup samples | host availability; cgroup current/sample peak/lifetime peak/limit separate |
| OOM/admission/preemption | native failures + cgroup events + engine counters/logs | event count/classification and retained source |

Request-level `request_timings` fields available in warmups are: `prompt_tokens`,
`output_tokens`, `effective_prefill_tok_s`, `time_to_first_output_ms`, `ttft_ms`,
`decode_tok_s`, `tpot_ms`, `e2e_ms`, finish reasons, reasoning/content chunk
counts, visible capture metadata, and canary/control fields. Only terminal,
eligible requests populate performance distributions.

## Engine telemetry extraction

Parse raw Prometheus samples with labels retained. For each cell take before and
after snapshots, then calculate counter/histogram deltas only when both sides
are valid and share engine/model labels. Required phase histogram families:

- `vllm:request_prefill_time_seconds_{bucket,count,sum}`
- `vllm:request_queue_time_seconds_{bucket,count,sum}`
- `vllm:request_decode_time_seconds_{bucket,count,sum}`

Do not derive quantiles from histogram deltas unless the publication explicitly
labels them engine histogram estimates. Preserve bucket deltas, count delta,
and sum delta. Also retain `vllm:num_requests_running`,
`vllm:num_requests_waiting`, `vllm:num_requests_waiting_by_reason`,
`vllm:kv_cache_usage_perc`, and `vllm:num_preemptions_total`. Their sampled
maxima are lower bounds because the collector is periodic.

`vllm:cache_config_info` labels may expose `kv_cache_size_tokens` and
`kv_cache_memory_bytes`. Report the former as observed allocated KV tokens and
the latter as observed allocation bytes. The `kv_cache_max_concurrency` label,
if present, is an engine-reported configuration estimate; it is neither client
occupancy nor proof of simultaneous full-context capacity.

## Memory extraction

Per device, require the collector to emit stable device identity projection plus
used memory bytes, free memory bytes, and utilization percent at each sample.
The existing warmup `gpu.jsonl` uses `host gpus` identity-only output, so it
cannot supply GPU memory/utilization metrics and remains excluded. Keep sample
intervals and collector errors with the derived table.

For host memory, require `MemAvailable` from the sampled host source. For
cgroup, retain current, effective/physical limit, swap limit, events, and two
different peaks:

- **sampled cell peak:** maximum current value from this cell's periodic samples;
- **cgroup lifetime peak:** cgroup-reported `peak_bytes`, which can predate the
  cell and must not be called a cell peak.

## Current collection gaps to resolve before counted cells

1. Actual GPU memory used/free and utilization are absent from existing warmup
   samples; identity-only `host gpus` output is insufficient.
2. Host `MemAvailable` is absent from existing warmup collector records.
3. New collector records must expose parseable fields rather than only opaque
   command stdout, or retain both the raw output and normalized projection.
4. Keep all collector errors and actual timestamps/intervals, including any
   missed samples; do not fill absent telemetry with zero.

After those fields are present, create one per-cell derived telemetry JSON that
references every raw input, preserves collector coverage/errors, and feeds only
the corresponding cell receipt/coverage entry.

## Collector correction recorded after warmups

The corrected GPU sample must preserve the runtime's separately reported
`memory.free` and `memory.total`/`memory.used` fields exactly. Do not replace
reported free with `total - used`: the observed warmup correction has free
memory distinct from that subtraction because the runtime reserves VRAM. Label
reserved VRAM as unavailable to allocation when reported. The collector-gap
disposition records that the two warmups have zero scored requests and remain
excluded.
