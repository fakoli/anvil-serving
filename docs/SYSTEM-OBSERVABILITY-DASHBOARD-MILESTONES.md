# System Observability Dashboard Execution Milestones

This document is the execution layer for the approved
`system-observability-dashboard` PRD. The PRD and its Anvil tasks remain the
requirements and evidence ledger. These milestones provide the larger,
outcome-oriented context used to execute that work without treating each small
task as an isolated product objective.

The dashboard remains required scope for v0.11.0. This plan does not authorize
packaging, release, deployment, or live topology changes. Those actions remain
human-gated after implementation, verification, and operator testing.

## Execution contract

- Start each implementation pass from the milestone outcome, not from one task
  title. Load the full PRD decisions and every member task into working context.
- Treat member tasks as evidence-bearing subcontracts. Claim, verify, and submit
  evidence for each task through Anvil even when implementation crosses task
  boundaries inside one coherent milestone.
- Do not declare a milestone complete merely because its member unit tests pass.
  The milestone exit gate must demonstrate the integrated operator outcome.
- Preserve task-level provenance: evidence must identify the task, verification
  command, artifact, and relevant milestone gate.
- Do not begin the next milestone until every member task is accepted and the
  current milestone exit gate passes.
- Keep ordinary inference fail-open when observability is unavailable or
  degraded. Never turn a telemetry failure into a routing or serving outage.

## Milestone 1 — Trustworthy local telemetry

**Outcome:** Fakoli Dark can produce one normalized, read-only view of its
Windows host, WSL/Docker boundary, NVIDIA GPUs, shared and dedicated GPU memory,
and containers without administrator privileges or third-party Python runtime
dependencies.

**Anvil tasks:** T001, T009, T002, T003, T004, T005, T006

**Execution order:**

1. T001 defines the common sample and capability contract.
2. T009 establishes degraded-state handling and redaction used by every output.
3. T002, T004, and T006 implement Windows, NVIDIA, and Docker collection.
4. T003 separates WSL/Docker pressure from Windows host pressure.
5. T005 adds Windows shared-GPU-memory evidence without conflating it with VRAM.

**Exit gate:**

- One fixture-driven integrated snapshot contains Windows CPU, memory, paging,
  disk, and network; WSL/Docker boundary usage; GPU utilization and
  dedicated/shared memory; and per-container resources.
- Unsupported, permission-denied, stale, ambiguous, and failed signals remain
  distinguishable from healthy zero values.
- The snapshot and logs contain no credentials or tokens.
- All member task verification commands pass together.

**Completion evidence (2026-07-11):** Complete.

- All seven member tasks are accepted with signed Anvil evidence: T001
  (`EV48E9883A`), T009 (`EV15198D6A`), T002 (`EV28D816A6`), T004
  (`EV45C35D36`), T005 (`EV13CA845D`), T006 (`EV7F264423`), and T003
  (`EVCC1CFAF4`).
- The fixture-driven integrated snapshot gate covers Windows host pressure,
  WSL and Docker boundaries, dedicated and shared GPU memory, NVIDIA activity,
  and per-container resources in one normalized sample set. It also proves
  healthy zero, unsupported, permission-denied, stale, ambiguous, and failed
  states remain distinct and that serialized samples and logs are secret-free.
- All milestone tests pass together (`81 passed`). The repository-wide gate
  passes (`3241 passed, 2 skipped`), as do Ruff, strict documentation build,
  Markdown link checking, the full CLI-reference audit, and Windows CLI
  hygiene scanning.
- Live unprivileged smoke collection succeeded on Fakoli Dark for Windows host
  metrics, both NVIDIA GPUs, both Windows non-local/shared-memory adapter
  counters, 22 Docker containers, and the WSL/Docker boundary. WDDM's
  unavailable per-process GPU-memory values remain explicit missing data rather
  than fabricated zeros.

## Milestone 2 — Whole-topology telemetry service

**Outcome:** Fakoli Dark exposes one structured, authenticated telemetry API
that combines local data with non-elevated macOS telemetry from Fakoli Mini,
service and port health, and explicitly configured optional adapter
capabilities.

**Anvil tasks:** T020, T007, T008, T019, T010

**Execution order:**

1. T020 implements the generic macOS host probe while preserving Mini's
   model-free reference role.
2. T007 adds configured service, endpoint, port, owner, and served-identity
   health.
3. T008 carries Mini samples over the authenticated Anvil controller contract.
4. T019 validates and inspects optional collector endpoints without managing
   their service lifecycle.
5. T010 integrates all completed probes behind the structured API.

**Exit gate:**

- A single API response distinguishes Fakoli Dark, its WSL/Docker boundary,
  both Dark GPUs, managed containers, and Fakoli Mini/macOS.
- Remote collection uses the authenticated controller; no raw SSH or
  unauthenticated public metrics path exists.
- The API binds to `127.0.0.1` by default and requires explicit authenticated
  configuration for private/tailnet exposure.
- Mini remains useful when optional adapters are absent and remains model-free
  in the reference topology.
- All member task verification commands pass together.

**Completion evidence (2026-07-11):** Complete.

- All five member tasks are accepted with signed Anvil evidence: T020
  (`EV05E23EDB`), T007 (`EVB44164BC`), T008 (`EVEEAA26C5`), T019
  (`EV7366F078`), and T010 (`EV39C59764`).
- The integrated API gate exercises one authenticated response containing
  Fakoli Dark host and WSL/Docker boundary metrics, two distinct GPUs, managed
  containers, service health, and Fakoli Mini host memory delivered through
  the authenticated controller contract. It also proves an absent optional
  adapter remains explicit and non-fatal, and that no authentication token is
  serialized.
- All milestone member tests pass together (`79 passed`), including the
  controller transport and collector CLI gates. The repository-wide gate
  passes (`3293 passed, 2 skipped`).
- The independent adversarial pass covered fail-closed authentication and
  host identity, malformed and duplicate capability requests, bounded samples
  and response sizes, stale/future remote timestamps, read-only HTTP methods,
  and Mini's model-free default registry. No merge-blocking finding remained.

## Milestone 3 — Live operator dashboard

**Outcome:** One supported Anvil CLI command serves the read-only dashboard that
replaces routine Task Manager, Docker Desktop, GPU-monitor, and Mini shell
inspection for current state and recent trends.

**Anvil tasks:** T011, T013, T012, T015

**Execution order:**

1. T011 supplies the supported dashboard serve command and one-page shell.
2. T013 implements tiered sampling and bounded retention.
3. T012 renders the retained core signals as time-series curves with real gaps.
4. T015 adds pressure, health, ownership, freshness, and model-loading
   interpretation.

**Exit gate:**

- `anvil-serving dashboard serve` starts the page on `127.0.0.1` by default.
- The page contains no state-changing control.
- A rendered-page verification shows Dark, Mini, GPU, container, service, and
  model-loading information on one screen with explicit degraded states.
- Retention holds one hour at full resolution, 15-second aggregates for 24
  hours, and one-minute aggregates for seven days, with a 250 MiB ordinary
  history cap.
- Ordinary history writes stop during benchmark capture.
- All member task verification commands pass together.

**Completion evidence (2026-07-11):** Complete.

- All four member tasks are accepted with signed Anvil evidence: T011
  (`EVDBCD6F6F`), T013 (`EV8D4C2F02`), T012 (`EVF1C4E555`), and T015
  (`EV10DDA877`).
- The supported `anvil-serving dashboard serve` command was exercised as a
  real foreground service on `127.0.0.1`. Live probes returned packaged HTML,
  315 current samples, retained time-series data, and a model-loading phase
  without exposing a mutation endpoint.
- The integrated milestone gate covers the approved core/costly sampling
  cadences, the 250 MiB deterministic retention ceiling, tiered history,
  visible missing-data gaps, both-GPU series, ownership reliability,
  freshness, and host/shared/VRAM loading transitions.
- Desktop (1440×1000) and mobile (430×932) Chromium renders were inspected;
  the responsive pass corrected mobile value clipping. The independent
  adversarial pass also caught and fixed a duplicated `serve` dispatch token
  and eliminated dashboard refreshes that bypassed the retained sampling
  cadence.
- All milestone member tests pass together (`234 passed`). The final
  repository-wide gate passes (`3317 passed, 2 skipped`), along with Ruff,
  strict documentation, Markdown links, and the full CLI-reference audit.

## Milestone 4 — Benchmark evidence and v0.11 acceptance

**Outcome:** Anvil benchmarks can capture synchronized system context, retain
privacy-safe evidence, compare a capture with current/history data, and prove
that observability does not materially distort the measured workload.

**Anvil tasks:** T016, T017, T014, T018

**Execution order:**

1. T016 integrates the adaptive capture lifecycle with the retention buffer.
2. T017 writes compressed private raw evidence plus sanitized repository
   manifests and findings.
3. T014 compares current and retained series against benchmark sessions while
   preserving sampling gaps.
4. T018 runs last against the complete collector and dashboard path and applies
   the strict overhead gate.

**Exit gate:**

- A capture contains five minutes of pre-history, the complete benchmark
  lifecycle, at least five minutes of post-history, and stabilization extension
  up to the 15-minute hard limit.
- Raw telemetry stays outside Git by default. A sanitized dated finding and
  manifest record checksum, size, capture quality, summary, and artifact
  locator without secrets.
- The dashboard compares current/history signals with the retained benchmark
  session and shows gaps explicitly.
- Normal mode stays at or below 100 MiB RSS and 1 percent average host CPU.
  Benchmark mode stays at or below 150 MiB RSS and 2 percent average host CPU,
  allocates no GPU memory, and performs no continuous disk writes during
  capture.
- Controlled collection-on/off runs show no more than a 1 percent change in
  benchmark throughput and latency.
- All member task verification commands and the full relevant test suite pass.

**Completion evidence (2026-07-11):**

- All milestone tasks passed strict review: T016 `EVAACC806C`, T017
  `EV0D97115E`, T014 `EVB548D0DF`, and T018 `EVBCC8DE05`.
- The integrated capture, artifact, history-comparison, and overhead gate passes
  with all milestone member tests (`27 passed`). The final repository gate is
  clean (`3344 passed, 2 skipped`), together with Ruff, strict documentation,
  Markdown-link, and full CLI-reference audit gates.
- The Fakoli Dark target-path overhead run passed without threshold changes.
  Normal mode averaged 0.3051 percent host CPU with 38,699,008-byte peak RSS;
  benchmark mode averaged 0.1513 percent host CPU with 39,501,824-byte peak
  RSS, zero dashboard-process capture writes, and zero GPU allocation. The
  controlled collection effect was 0.3529 percent throughput and -0.3516
  percent latency.
- The sanitized result is indexed at
  [the observability overhead finding](findings/2026-07-11-system-observability-overhead.md).
  Its external raw artifact checksum is
  `98da7a3426493e6f9aef92c7e663ab8a7ec2a596b0824002a1f0f841022c983e`.

## Evidence handoff

For each milestone, the executing model should begin with this document, the
approved PRD, and all member Anvil work packets. Task evidence is submitted as
each packet is satisfied. The last task in the milestone also records or links
the integrated exit-gate evidence. A later milestone may consume accepted
artifacts from an earlier milestone, but it must not silently reinterpret or
replace them.
