# Evidence manifest: Qwen3.8 27B NVFP4 RTX 5090 128K qualification

The [dated finding](../2026-08-17-qwen38-27b-radixark-nvfp4-rtx5090-128k.md)
is authoritative. This directory retains the sanitized artifacts used for the
format-only result-card and publication-summary migration; no benchmark was
rerun and no serving state was changed.

| Artifact | Role | Supported claims | Boundary |
|---|---|---|---|
| [`preflight-functional-requested-120k.json`](preflight-functional-requested-120k.json) | functional, requested-context, and tool-call preflight | coding/JSON pass and 20/20 tool calls | requested 120K generator produced 99,049 actual prompt tokens; not the long-context proof |
| [`preflight-needle-119675-tokens.json`](preflight-needle-119675-tokens.json) | separate long-context gate | exact retrieval marker at 119,675 prompt tokens plus 14 completion tokens | one direct c1 request; not a throughput distribution |
| [`preflight-multimodal.json`](preflight-multimodal.json) | direct image, OCR, and video preflight | required labels returned with accepted finish state | bounded smoke cases, not the full corpus |
| [`multimodal-c1.json`](multimodal-c1.json) | established deterministic multimodal qualification | 30/30 total: image 12/12, mixed 4/4, video 14/14; modality latency distributions | direct c1; old multimodal evidence schema; no routed acceptance |
| [`count-boundaries-c1.json`](count-boundaries-c1.json) | corrected media-count boundary qualification | 4/4 total: two eight-image and two two-video attempts | validates only the declared eight-image/two-video boundary at c1 |
| [`count-boundaries-invalid-expectations.json`](count-boundaries-invalid-expectations.json) | retained invalid-expectation failure | 2/4 total; video 2/2, image 0/2 | image rubric required labels absent from the source assets; not a model-quality failure |
| [`source-registry.json`](source-registry.json) | dated source and identity registry | exact checkpoint/runtime context and external comparator provenance | sources are priors or identity evidence, not extra local measurements |
| [`publication-summary.md`](publication-summary.md) | derivative publishing copy | bounded copy and claim ledger reconciled to the artifacts above | not an independent evidence source |

The generic evidence inspector does not recognize the retained multimodal
schema. The finding and publication copy therefore derive their counts and
latency values directly from the sanitized JSON and preserve the unsupported
schema as an explicit evidence boundary.
