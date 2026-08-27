# Qwen3.8 Flash Next vision-promotion evidence

This directory is the sanitized evidence bundle for the
[dated finding](../2026-08-26-qwen38-flash-next-vision-promotion.md). All
artifacts bind the exact RadixArk revision, SGLang runtime, dual-PRO TP=2
topology, and c1 route contract. No artifact contains a credential, private
address, GPU UUID, personal path, media bytes, or data URL.

## Compact outputs

- [`summary.json`](summary.json) - machine-readable decision summary
- [`throughput-summary.csv`](throughput-summary.csv) - compact six-size table
- [`publication-summary.md`](publication-summary.md) - platform variants, alt
  text, and claim ledger

## Context and throughput

- [`capacity-4096-c1.json`](capacity-4096-c1.json)
- [`capacity-32768-c1.json`](capacity-32768-c1.json)
- [`capacity-65536-c1.json`](capacity-65536-c1.json)
- [`capacity-131072-c1.json`](capacity-131072-c1.json)
- [`capacity-196608-c1.json`](capacity-196608-c1.json)
- [`capacity-253952-c1.json`](capacity-253952-c1.json)

Each `anvil-serving.benchmark/v1` artifact retains request-level prompt and
output tokens, TTFT, effective prefill, decode, inter-token, E2E, measurement
definitions, completion state, and exact identity.

## Multimodal corpus

- [`direct-multimodal.json`](direct-multimodal.json) - direct 30/30
- [`isolated-routed-multimodal.json`](isolated-routed-multimodal.json) - 27/30
- [`isolated-routed-multimodal-r2.json`](isolated-routed-multimodal-r2.json) - 30/30 repeat
- [`live-routed-multimodal.json`](live-routed-multimodal.json) - 29/30
- [`live-routed-multimodal-r2.json`](live-routed-multimodal-r2.json) - 28/30 repeat

The artifacts retain every strict assertion and model output. The five routed
misses across four runs are visible rather than cherry-picked; every miss was
a correct observation that omitted one literal rubric word.

## Router and regression gates

- [`isolated-router-edges.json`](isolated-router-edges.json) - 8/8
- [`live-router-edges.json`](live-router-edges.json) - 8/8
- [`live-primary-regression-preflight.json`](live-primary-regression-preflight.json)
- [`live-vision-ocr-preflight.json`](live-vision-ocr-preflight.json)
- [`mini-client-vision-acceptance.json`](mini-client-vision-acceptance.json)

The edge artifacts deliberately retain admitted/overflow/malformed status,
SSE termination, and grounded tool-call results without retaining the media
payloads.
