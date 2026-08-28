# Evidence manifest: Qwen3.8 27B NVFP4 RTX 5090 64K qualification

The [dated finding](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090.md)
is authoritative. This directory retains the sanitized artifacts used for the
format-only result-card and publication-summary migration; no benchmark was
rerun and no serving state was changed.

| Artifact | Role | Supported claims | Boundary |
|---|---|---|---|
| [`preflight-functional-60k.json`](preflight-functional-60k.json) | functional, long-context, and tool-call preflight | coding/JSON pass, approximately 60K retrieval marker, 20/20 tool calls | direct endpoint, c1; preflight summary rather than a throughput sweep |
| [`preflight-multimodal.json`](preflight-multimodal.json) | direct image, OCR, and video preflight | required labels returned with accepted finish state | bounded smoke cases, not the full corpus |
| [`multimodal-c1.json`](multimodal-c1.json) | deterministic multimodal qualification | 30/30 total: image 12/12, mixed 4/4, video 14/14; modality latency distributions | direct c1; old multimodal evidence schema; no routed acceptance |
| [`source-registry.json`](source-registry.json) | dated source and identity registry | exact checkpoint/runtime context and external comparator provenance | sources are priors or identity evidence, not extra local measurements |
| [`publication-summary.md`](publication-summary.md) | derivative publishing copy | bounded copy and claim ledger reconciled to the artifacts above | not an independent evidence source |

The generic evidence inspector does not recognize the retained multimodal
schema. The finding and publication copy therefore disclose that limitation
and derive their counts and latency values directly from the sanitized JSON.
