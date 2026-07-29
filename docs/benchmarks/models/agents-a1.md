# Agents-A1

## Current status and review date

Benchmark-qualified `challenger` with thinking disabled; `no-promotion`.
Review date: 2026-07-28.

## Immutable identity

`InternScience/Agents-A1` revision
`addff08f1653ee72765c5cf458fe84556bb34f8e`.

## Tested hardware and topology

RTX PRO 6000 on Fakoli Dark. An initial GPU-selection defect exposed the 5090
too; the fixed loader pinned both Docker and CUDA visibility to the PRO.

## Engine, quantization, KV, context, and concurrency recipe

Pinned vLLM nightly `f25953cc...`; 64.69 GiB weights, 20.43 GiB KV,
2,029,630 KV tokens, 120K retrieval. Thinking must be disabled.

## Evidence by measurement class

`functional`, `capacity`, `quality`: smoke/JSON, 120K retrieval, 20/20 tools,
6/6 intelligence, session 3/3, and tool 3/3.

## Decision and promotion state

`challenger`, `no-promotion`. Qualification did not supersede Laguna or
authorize a Primary change.

## Failures and gotchas

Default thinking exhausted the response budget after 29.1 seconds with no
visible answer. The first artifact pull filled Docker storage; current pull
preflight fixes the product defect but does not retroactively validate that run.

## Dated run history

- [2026-07-27 release-readiness qualification](../../findings/2026-07-27-anvil-serving-release-readiness-sweep.md#agents-a1-qualification)
