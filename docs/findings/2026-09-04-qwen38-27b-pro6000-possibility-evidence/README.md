# Qwen3.8 27B PRO 6000 comprehensive campaign evidence

This sanitized bundle supports the
[dated finding](../2026-09-04-qwen38-27b-pro6000-possibility-plan.md).

## Campaign boundary

- **Campaign:** `2026-09-04-qwen38-27b-pro6000-possibility`
- **Measured hardware:** two equal RTX PRO 6000 Blackwell Max-Q GPUs under
  Docker Desktop/WSL2; single TP1, dual independent TP1, and TP2 measured
- **Measurement:** direct online streaming; natural-completion screening plus
  a matched 100-request unique-canary sustained-output workload
- **Decision:** DP2 bounded aggregate-throughput winner; TP2 rejected;
  no promotion
- **Not measured:** broad quality, agentic/SWE, multimodal, routed/client,
  load-balancer/failover, and complete power/energy telemetry

## Common campaign artifacts

- [`artifact-manifest.json`](artifact-manifest.json) — campaign role ledger
- [`source-registry.json`](source-registry.json) — dated official, community,
  and local priors
- [`workload-manifest.json`](workload-manifest.json) — workload and metric
  definitions
- [`run-plan.json`](run-plan.json) — executed stage order and stop rules
- [`configuration.json`](configuration.json) — immutable identities and exact
  runtime/topology settings
- [`summary.json`](summary.json) — machine-readable metrics, gates,
  limitations, and no-promotion decision
- [`friction-log.md`](friction-log.md) — runtime, correctness, and restoration
  friction with dispositions
- [`restoration.json`](restoration.json) — exact starting-state restoration
- [`publication-summary.md`](publication-summary.md) — bounded public copy and
  claim ledger

## Headline matched artifacts

- SGLang Inferact TP1:
  [`finalist-k12-chunk1k-4k-c8-canary-long256-n100.json`](finalist-k12-chunk1k-4k-c8-canary-long256-n100.json)
- SGLang Inferact TP2:
  [`opt-tp2-k12-chunk1k-4k-c8-canary-long256-n100.json`](opt-tp2-k12-chunk1k-4k-c8-canary-long256-n100.json)
- DP2 combined:
  [`dp2-combined-4k-c16-canary-long256-n100.json`](dp2-combined-4k-c16-canary-long256-n100.json),
  with [replica A](dp2-replica-a-4k-c8-canary-long256-n50.json) and
  [replica B](dp2-replica-b-4k-c8-canary-long256-n50.json)
- RadixArk K8:
  [`radixark-dflash-k8-4k-c8-canary-long256-n100.json`](radixark-dflash-k8-4k-c8-canary-long256-n100.json)
- kelnei/vLLM MTP2:
  [`kelnei-vllm0271-mtp2-4k-c8-canary-long256-n100.json`](kelnei-vllm0271-mtp2-4k-c8-canary-long256-n100.json)
- kelnei/vLLM no-spec:
  [`kelnei-vllm0271-nospec-4k-c8-canary-long256-n100.json`](kelnei-vllm0271-nospec-4k-c8-canary-long256-n100.json)
- vLLM MTP counters:
  [`kelnei-vllm0271-mtp2-runtime-metrics.json`](kelnei-vllm0271-mtp2-runtime-metrics.json)

Each headline artifact contains request-level timings plus mean, p50, p95,
p99, confidence interval, and standard deviation for TTFT, effective prefill,
decode, TPOT/mean ITL, and E2E. Aggregate throughput is output tokens divided
by the concurrent run wall clock.

## Functional and correctness artifacts

- Selected Inferact TP1: [preflight](finalist-k12-chunk1k-preflight.json)
- TP2: [failed full preflight](opt-tp2-k12-chunk1k-preflight.json) and
  [failed isolated JSON repeat](opt-tp2-k12-chunk1k-json-isolated.json)
- DP2: [replica A](dp2-replica-a-preflight.json) and
  [replica B](dp2-replica-b-preflight.json)
- RadixArk: [target-only](radixark-nospec-preflight.json),
  [K8](radixark-dflash-k8-preflight.json), and
  [K12/1K](radixark-dflash-k12-chunk1k-preflight.json)
- kelnei/vLLM: [MTP2](kelnei-vllm0271-mtp2-preflight.json) and
  [no-spec](kelnei-vllm0271-nospec-preflight.json)

TP2's performance artifact completed 100/100 canaries, but its strict JSON
gate emitted a duplicate object around a literal closing think delimiter and
failed again in isolation. It is rejected.

## SGLang optimization artifacts

- Draft depth: K4 [`C1`](opt-k4-4k-c1.json),
  [`C4`](opt-k4-4k-c4.json), [`C8`](opt-k4-4k-c8.json);
  K12 [`C1`](opt-k12-4k-c1.json), [`C4`](opt-k12-4k-c4.json),
  [`C8`](opt-k12-4k-c8.json), [`32K`](opt-k12-32k-c1.json);
  K16 [`C1`](opt-k16-4k-c1.json), [`C4`](opt-k16-4k-c4.json),
  [`C8`](opt-k16-4k-c8.json), [`32K`](opt-k16-32k-c1.json)
- K12 chunk sweep: 1K [`C1`](opt-k12-chunk1k-4k-c1.json),
  [`C4`](opt-k12-chunk1k-4k-c4.json), [`C8`](opt-k12-chunk1k-4k-c8.json),
  [`32K`](opt-k12-chunk1k-32k-c1.json); 8K
  [`C1`](opt-k12-chunk8k-4k-c1.json), [`C4`](opt-k12-chunk8k-4k-c4.json),
  [`C8`](opt-k12-chunk8k-4k-c8.json), [`32K`](opt-k12-chunk8k-32k-c1.json)
- Compile: [`C1`](opt-k12-compile-4k-c1.json),
  [`C4`](opt-k12-compile-4k-c4.json), [`C8`](opt-k12-compile-4k-c8.json),
  [`32K`](opt-k12-compile-32k-c1.json)
- Mamba strategy/slots: lazy96 [`C1`](opt-k12-chunk1k-lazy96-4k-c1.json),
  [`C4`](opt-k12-chunk1k-lazy96-4k-c4.json),
  [`C8`](opt-k12-chunk1k-lazy96-4k-c8.json),
  [`32K`](opt-k12-chunk1k-lazy96-32k-c1.json); Mamba40
  [`C1`](opt-k12-chunk1k-mamba40-4k-c1.json),
  [`C4`](opt-k12-chunk1k-mamba40-4k-c4.json),
  [`C8`](opt-k12-chunk1k-mamba40-4k-c8.json),
  [`32K`](opt-k12-chunk1k-mamba40-32k-c1.json),
  [`82K/C8`](opt-k12-chunk1k-mamba40-82k-c8.json)
- Finalist: [preflight](finalist-k12-chunk1k-preflight.json),
  [natural N100](finalist-k12-chunk1k-4k-c8-n100.json),
  [sustained N100](finalist-k12-chunk1k-4k-c8-canary-long256-n100.json), and
  [82K/C8](finalist-k12-chunk1k-82k-c8-n8.json)

The original target-only/DFlash2 K8 matrix remains retained as the baseline;
see files prefixed `nospec-`, `nospec-mamba96-`, and `dflash-k8-`.

## Topology, checkpoint, and alternate-runtime artifacts

- TP2: files prefixed `opt-tp2-k12-chunk1k-`
- DP2: files prefixed `dp2-`
- RadixArk target-only/K8/K12: files prefixed `radixark-`
- kelnei/vLLM MTP2 and no-spec: files prefixed `kelnei-vllm0271-`

The 82K/C8 artifacts are negative interactive-latency evidence. They are not
warm-prefix results and must not be used to claim responsive long-context C8.

## Graphs and restoration

- [`graph-manifest.json`](graph-manifest.json) — exact chart selections
- [`benchmark-graph-data.json`](benchmark-graph-data.json) — plotted values,
  source paths, and hashes
- [`benchmark-matrix.svg`](benchmark-matrix.svg) — derivative dashboard
- [`restoration-smoke.json`](restoration-smoke.json) and
  [`restoration-router-smoke.json`](restoration-router-smoke.json) — fresh
  direct and routed restoration checks

The graph is derivative; raw JSON is authoritative. Publication does not
authorize a serve, route, client, or deployment change.
