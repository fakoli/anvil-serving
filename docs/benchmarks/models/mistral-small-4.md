# Mistral Small 4 119B

## Current status and review date

Historical low-latency challenger; `no-promotion`. Review date: 2026-07-28.

## Immutable identity

`mistralai/Mistral-Small-4-119B-2603-NVFP4` revision
`d57a94c74a961e1f9b489b8b3e792923ca29149b`.

## Tested hardware and topology

Single RTX PRO 6000 on Fakoli Dark.

## Engine, quantization, KV, context, and concurrency recipe

vLLM nightly, NVFP4, `TRITON_MLA`, Mistral reasoning/tool parsers, text-only,
131,072 context, five sequences.

## Evidence by measurement class

`functional`, `capacity`, `quality`: 131K context, tools, repeated slices, and
short-request latency.

## Decision and promotion state

`no-promotion`; retained as the low-TTFT control.

## Failures and gotchas

Only 5/10 stable MMLU-Pro items at the tuned 2K point. Low latency alone did
not satisfy the quality gate.

## Dated run history

- [2026-07-12 challenger comparison](../../findings/2026-07-12-heavy-intelligence-challengers.md)
