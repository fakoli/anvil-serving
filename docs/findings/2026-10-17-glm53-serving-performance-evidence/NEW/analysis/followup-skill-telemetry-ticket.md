# Draft: first-class numerical telemetry for capacity cells

Status: draft after the active GLM A/B campaign; do not implement or alter its
frozen runner/collector until both arms finish.

## Gap

`anvil-serving host gpus` is an inventory command. It cannot supply timestamped
GPU used/free memory or utilization, and native Linux has no public read-only
`MemAvailable` observation surface. A parser self-test or one sample before and
after a capacity client cannot prove resource behavior during that client.

## Minimal change

Add one small stdlib-only, read-only `host telemetry --json` command that uses
the existing registered `observability.probes.gpu_nvidia.collect_nvidia_gpus`
probe for `nvidia-gpu`. It already emits normalized GPU total/used/available,
utilization, power, and temperature from `nvidia-smi -q -x`; do not duplicate
that query or change `host gpus` inventory rows. Add a native Linux
`host-resources` probe for `/proc/meminfo` that exposes `MemTotal`,
`MemAvailable`, and swap facts through the same command. Return explicit
unavailable/invalid states rather than fabricated values.

Add a managed capacity-cell runner contract that is reviewed and bound to the
frozen plan, runner, and collector hashes, then launches only its frozen argv.
During the owned client process it must sample engine occupancy, the GPU
snapshot, Linux host memory, and managed cgroup memory/events at declared
cadence; validate exact serve/container/GPU identity on slow samples and
model/engine/process identity on every engine sample; drain the upstream
engine; and reconcile native request/token totals to engine counters. A missing
family, cadence gap, probe error, identity drift, counter mismatch, OOM event,
or deadline must fail the cell closed. A blocked/incomplete cell remains
retained but cannot support a full comparison claim.

## Acceptance

- Unit fixtures cover the existing NVIDIA XML probe through the command,
  invalid/unavailable probes, Linux meminfo, and no regression to `host gpus`
  inventory output.
- One stdlib-only orchestration fixture enters the actual runner with fake
  process, engine, GPU, host, and cgroup seams. It proves every collector has
  in-process samples, exact counter reconciliation, and fail-closed collector,
  timeout, native-binding, frozen-plan, and overwrite paths.
- No model request, serve lifecycle, route change, or promotion is part of this
  work. Update qualification and repeatable-campaign guidance with the same
  boundary.
