# Observability overhead gate

- Overall result: **PASS**
- Observed: 2026-07-11 on the Fakoli Dark Windows workstation, using the
  complete default Windows, WSL/Docker, NVIDIA, container, service-health,
  retention, and dashboard-sampler path.
- Raw artifact: `anvil-evidence://observability/observability-overhead.json.gz`
- SHA-256: `98da7a3426493e6f9aef92c7e663ab8a7ec2a596b0824002a1f0f841022c983e`
- Compressed size: 596 bytes

## Normal profile
- Result: **PASS**
- CPU average / peak: 0.3051% / 3.1323%
- RSS average / peak: 37640520 / 38699008 bytes
- Disk writes / GPU allocation: 0 / 0 bytes
- Throughput / latency change: 0.3529% / -0.3516%
- Child-process peak CPU / RSS: 9.7487% of host capacity / 99,512,320 bytes
- Docker / WSL command invocations: 10 / 0
- Failures: none

## Benchmark profile
- Result: **PASS**
- CPU average / peak: 0.1513% / 0.2716%
- RSS average / peak: 38563185 / 39501824 bytes
- Disk writes / GPU allocation: 0 / 0 bytes
- Throughput / latency change: 0.3529% / -0.3516%
- Child-process peak CPU / RSS: 9.8206% of host capacity / 99,028,992 bytes
- Docker / WSL command invocations: 12 / 0
- Failures: none

## Method and interpretation

The normal sampler used the approved 2-second core and 5-second costly
cadences. The benchmark sampler used 1-second core and 2-second costly
cadences. The harness sampled the Python process at 250 ms, measured process
CPU against total logical host capacity, read Windows working-set and process
I/O counters, queried NVIDIA compute-process allocation for the dashboard PID,
and watched collector child processes at 5 ms for peak CPU and RSS. The raw
artifact contains the exact unrounded results and profile limits.

The collection-effect check bracketed a six-second collection-on control with
two three-second collection-off controls and compared median latency and its
reciprocal throughput across 295 collection-on samples. It is an overhead
control workload, not a model-quality or model-throughput benchmark, and does
not change any model recommendation in `docs/BENCHMARKS.md`.

The benchmark-mode costly frame reported 87 degraded samples. Inspection tied
these to explicitly unavailable per-container GPU attribution, not failed
collection; the core frame was healthy. Docker and WSL cost is reported as
collector command invocations because those are the external API boundaries in
the stdlib collector path. Zero capture writes refers to the dashboard Python
process and its in-memory retention path; raw evidence was compressed only
after measurement ended.
