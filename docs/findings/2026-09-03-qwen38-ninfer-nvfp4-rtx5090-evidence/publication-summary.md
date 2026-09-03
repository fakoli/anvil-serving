# Publication summary: Qwen3.8 27B NInfer NVFP4 MTP3 on RTX 5090

<!-- benchmark-publication-summary/v1 -->

This is derivative publishing copy. The
[dated finding](../2026-09-03-qwen38-ninfer-nvfp4-rtx5090.md) and linked raw
artifacts are authoritative.

## Canonical facts

- **Model identity:** `neroued/Qwen3.8-27B-nvfp4-NInfer` revision
  `204e3d92c30d9d05f3300d2f52e443ad1edf6ddf`, artifact SHA-256
  `bb3360522a06e136e0367f5703414d26272b7285c8a6ab6194135c17dbd81b32`
- **Runtime identity:** NInfer revision
  `e3aeaf8c0b6f83ae8f051780f0ad0d995d5a7bef`, source-built on the
  digest-pinned CUDA 13.1.2 development image recorded in the recipe
- **Local setup:** one RTX 5090, 32,607 MiB; NInfer NVFP4/row-scaled-FP8
  target, INT8 KV, MTP3, 252,928 tokens, C1, thinking disabled
- **Matched 4K result:** MTP3 held median TTFT near-flat at 0.430 versus
  0.421 seconds, raised median decode from 75.3 to 165.9 tok/s, and reduced
  median E2E from 1.085 to 0.720 seconds across five requests per arm
- **Context result:** the MTP3 arm returned the exact marker from a nominal
  244,480-token fixture containing 201,746 API-reported prompt tokens while
  accepting an 8,192-token completion cap; E2E was 70.4 seconds
- **Behavior result:** smoke, structured JSON, C1 tools, streaming tools,
  tool-result continuation, coding 3/3, triage 3/3, and repeated tools 3/3
  passed
- **Failed gate:** a 20-way shared-prefix tool burst completed 17/20 on both
  arms; three requests received explicit `429 server_overloaded` under the C1
  scheduler contract
- **Safety:** MTP3 left 2,354 MiB free by `nvidia-smi` after startup and the
  long request, above the campaign's explicit 1 GiB model-only floor but below
  the ordinary 3 GiB reserve
- **Decision:** preferred measured RTX 5090 text/tools performance challenger;
  `no-promotion`; no route, client catalog, or persistent serve changed

## Short post

```text
Qwen3.8 27B NVFP4 + MTP3 on one RTX 5090: 0.43s median TTFT, 165.9 tok/s decode, 0.72s E2E at 4K/C1; 201,746-token prompt + 8,192 output cap passed. Direct challenger only: 20-way burst was 17/20 and no route was promoted.
```

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| 0.430 s TTFT, 165.9 tok/s decode, 0.720 s E2E | five 4K-class requests, C1, thinking disabled, cold labeled run | [`capacity-mtp3-4k-c1.json`](capacity-mtp3-4k-c1.json) |
| 2.20x matched decode and 33.7% lower median E2E | same artifact/runtime/context/KV/C1; MTP3 versus no spec | [`capacity-nospec-4k-c1.json`](capacity-nospec-4k-c1.json) · [`capacity-mtp3-4k-c1.json`](capacity-mtp3-4k-c1.json) |
| 201,746-token prompt with 8,192-token completion cap passed | nominal 244,480 fixture, exact marker, thinking disabled | [`preflight-mtp3-needle-244k-output-reserve-8192.json`](preflight-mtp3-needle-244k-output-reserve-8192.json) |
| Repeated bounded coding/triage/tools passed | three attempts per deterministic check | [`quality-mtp3.json`](quality-mtp3.json) |
| Shared-prefix burst was 17/20 | 20 simultaneous attempts against the C1 recipe; three explicit 429 admissions | [`preflight-mtp3-tools.json`](preflight-mtp3-tools.json) |
| Exact incumbent restored | managed health/identity and fresh smoke | [`configuration-end.json`](configuration-end.json) · [`restoration-smoke.json`](restoration-smoke.json) |
