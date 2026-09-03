# Qwen3.8 27B RTX 5090 quant bakeoff evidence

This directory contains the sanitized evidence bundle for the 2026-09-03
single-GPU RTX 5090 Qwen3.8 27B quantization and speculative-decoding bakeoff.
Native benchmark artifacts are authoritative; the files here provide the
campaign-level index.

## Campaign boundary

- **Campaign ID:** `2026-09-03-qwen38-27b-rtx5090-quant-bakeoff`
- **Capability:** text LLM serving
- **Repository revision:** `aed0214e03d2fe0e8220e3249a12f90994fd89b5` at campaign start; clean isolated worktree
- **Evidence labels:** `functional`, `capacity`, `matched-performance`,
  `bounded-quality`, `compatibility-failure`, `exact-restoration`
- **Decision labels:** `challenger`, `no-promotion`
- **Promotion boundary:** benchmark evidence does not authorize a router or live-alias change

## Common campaign artifacts

- [`artifact-manifest.json`](artifact-manifest.json) - role ledger and explicit evidence gaps
- [`source-registry.json`](source-registry.json) - dated source provenance
- [`summary.json`](summary.json) - bounded machine-readable decision
- [`friction-log.md`](friction-log.md) - failures and operational friction
- [`restoration.json`](restoration.json) - starting state and final restoration proof
- [`configuration.json`](configuration.json) - sanitized starting identity and policy envelope

## Workload and plan

- [`run-plan.json`](run-plan.json) declares the candidate order, paired
  no-speculation/speculation controls, repetitions, context points, concurrency
  checks, gates, and ranking order.
- [`workload-manifest.json`](workload-manifest.json) defines the deterministic
  smoke, timing, durable-context, and concurrency workloads.

## Raw run evidence

- Every loaded arm has retained `*-preflight.json`, warm/cold
  `*-timing-4k-*.json`, and `*-capacity-*.json` artifacts.
- Gittensor and CometKim have repeated `*-quality.json` artifacts.
- The advertised Gittensor DSpark startup failure is retained in
  [`gittensor-dspark-load-failure.json`](gittensor-dspark-load-failure.json).
- vLLM speculative acceptance counters are retained in the four
  `*-runtime-metrics.json` files.
- The invalid CometKim per-request thinking-control attempt remains labeled
  `*invalid-thinking-control.json`; it is not included in performance claims.

## Decision and publication

The Gittensor target-only SGLang arm won the primary TTFT metric at 50.9 ms
warm median while passing repeated bounded checks and a 244,002-token actual-
prompt request. CometKim MTP3 won decode at 228.0 tok/s but failed strict tools
0/3. The exact Unsloth incumbent was restored. See the
[decision summary](summary.json), [publication summary](publication-summary.md),
and [dated finding](../2026-09-03-qwen38-27b-rtx5090-quant-bakeoff.md).
