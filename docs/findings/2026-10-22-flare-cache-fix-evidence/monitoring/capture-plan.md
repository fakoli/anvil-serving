# Continuous telemetry readiness — 2026-10-22

Read-only preflight: existing Prometheus continuously captures the incumbent and
host. The skill check ran once. Its 227 Python tests passed (3 skipped), live
verification reports `passed=true`, and final log verification reports
`passed=true`. Exact script exit status was not captured: the shell wrapper
failed afterward assigning zsh's read-only `status` variable. See check.stdout,
check.stderr, capture-note.txt and live-verification.json. No rerun occurred.

At inspection there were 11 healthy targets. HTTP discovery and Prometheus both
identified incumbent `glm53-nvidia-fp4-local-tp2-327k-c4-mtp3-r2`, vLLM, generation
`40202210b60c660ae092fc56936a8fb29bc4e835b381b0d9d0e077fef57bd58b`.
This dated observation does not establish the identity of a later trial.

## Shortest existing capture

Use existing Prometheus collection, then export each arm's exact UTC interval
through `/api/v1/query_range` with step=15s. `queries.json` contains the selectors;
replace GENERATION from that arm's actual HTTP discovery receipt. No new sampler
process or operational wrapper is needed for engine/GPU/host metrics. A bounded
five-minute preflight query returned25series successfully; retained as
preflight-query-range.json.

The direct API is a single curl request with `--get`, `--data-urlencode query=...`,
`--data-urlencode start=...`, `--data-urlencode end=...`, `--data-urlencode step=15s`
and `--output <arm>-telemetry.json`. Keep selectors in ordinary experiment config,
UTC boundaries in arm receipts, and export promptly rather than rely on14day
retention. Query-range samples at15s do not increase collection resolution.

Engine and raw GPU scrape intervals are15s, host memory30s, and canonical GPU
recording rules30s. Short requests can lie entirely between samples. Preserve
the harness's per-request before/after engine counters for exact event deltas;
Prometheus is the continuous trajectory and independent collection record.

After every managed load, wait for exactly one discovery generation matching
recipe, container, model and the live model check; require that same
`registration_generation` on one healthy inference-engine Prometheus target.
Retain discovery and /api/v1/targets receipts. Require two actual successful
scrapes before the arm. Avoid rates across generations; use generation-filtered
raw counter differences and reset-aware rates within one generation. Capture
the old-generation withdrawal interval separately rather than fill it with zero.

Keep `flare_.*` native counters/gauges, including cache admission outcomes,
row/page pressure/free/resident counts, completed transfers/reused tokens,
prefill/generated/accepted work, graph launches and request outcomes. Preserve
client TTFT separately; native graph-duration rates do not measure GPU utilization.

GPU canonical series currently present: utilization, power draw and limit,
SM/memory clocks, P-state, power-cap flag/time ratio, hardware/software thermal
flags and hardware power-brake flag. Validate collection_success=1 and successful
sample age0..90s throughout. Missing data stays unavailable. Host
`node_memory_MemAvailable_bytes` is present. All queries select physical host
primary-node and retain GPU UUIDs; never infer compute from VRAM allocation.

## Cgroup trajectory gap

No `container_memory_*` or `container_oom_*` Prometheus series was present.
Existing capacity CLI has no independent continuous hardware/cgroup sampler.
Managed `models recipes status` exposes current, peak, effective max/swap,
OOM and oom_kill counters through serve_recipes.py and recipe_memory.py, but it
is a snapshot command. Before/after snapshots preserve cgroup peak and OOM
events but do not provide continuous current-memory samples.

For a continuous cgroup trace, the experiment's existing bounded harness must
sample the supported recipe status surface periodically with UTC/monotonic
anchors, binding the immutable container ID and checking containment_error.
Reuse recipe_memory.observation through the managed status operation; do not
parse Docker environment or create a separate operations wrapper. This sampling
integration is still required; it was neither implemented nor run by this task.
Do not label the continuous cgroup requirement satisfied by the healthy
Prometheus targets or before/after memory snapshots.

## Source pointers

- OPERATOR_PATH_eccd0e5f084d/prometheus.yml:
  scrape intervals, flare metric keep rule, generation-aware HTTP discovery.
- .../prometheus/rules/gpu.yml: units, freshness gates, clocks/event mappings.
- .../docs/recipe-monitoring.md: generation withdrawal/publication and caveats.
- OPERATOR_PATH_d9b1abb6edc3/recipe_memory.py::observation:
  cgroup.current/peak/max and memory.events with immutable-ID path checks.
- .../anvil_serving/serve_recipes.py: managed status host_memory/containment_error.
- .../anvil_serving/benchmarking/cli.py: capacity workload runner; no standalone
  host/cgroup telemetry capture options.

No inference request, lifecycle mutation, infrastructure edit, secret read,
service restart or GPU workload was performed by this preflight.
