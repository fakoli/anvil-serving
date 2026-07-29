# Nemotron Puzzle 75B

## Current status and review date

Historical challenger; `no-promotion`. Review date: 2026-07-28.

## Immutable identity

`nvidia/NVIDIA-Nemotron-Labs-3-Puzzle-75B-A9B-NVFP4`, observed revision
`1d370e47fbc56d1019a471c2339663cdbbb5236f`.

## Tested hardware and topology

Single RTX PRO 6000 on Fakoli Dark.

## Engine, quantization, KV, context, and concurrency recipe

vLLM nightly, NVFP4, MTP 3, 131,072 context, two sequences.

## Evidence by measurement class

`functional`, `capacity`, `quality`: preflight, 131K needle, 20/20 tools,
intelligence control, and MTP throughput A/B.

## Decision and promotion state

`no-promotion`; the local MTP result was strong but the recipe lacked a pinned
stable engine at decision time.

## Failures and gotchas

Old service configuration used a mutable nightly and did not always pass an
explicit revision. Reruns must pin both before claiming equivalence.

## Dated run history

- [2026-07-12 recheck](../../findings/2026-07-12-nemotron-puzzle-recheck.md)
- [2026-07-10–11 bakeoff](../../findings/2026-07-10-blackwell-local-model-bakeoff.md)
