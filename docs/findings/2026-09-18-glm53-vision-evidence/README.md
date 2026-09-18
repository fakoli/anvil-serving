# Vision enablement evidence

Functional acceptance on two RTX PRO 6000 Blackwell Max-Q 96 GB GPUs, native Linux.

- [Managed recipe reconstruction](recipe.toml): synthetic model-cache mount; operator GPU ownership and endpoints remain private.
- [Direct acceptance](preflight.json), [image corpus](multimodal.json), [long-context checks](long-context.json), [routed acceptance](routed-preflight.json).
- [Real Pi image response](pi-image-smoke.txt), [Mini client sync](mini-client-sync.json), [repeat sync](mini-client-sync-repeat.json).
- [Decision](summary.json), [final state](restoration.json), [friction](friction-log.md), [publication copy](publication-summary.md).

Raw schemas and results are preserved. Absolute operator evidence/product paths were replaced with EVIDENCE_ROOT/PRODUCT_ROOT; the private DNS name was replaced with example.ts.net. The runtime build ref is inherited from the pinned baseline metadata; startup reports vLLM 0.1.dev20111+g7f1e92bec.d20260827. No new source-to-image attestation was performed.

Additional negative evidence: [259,922-token retrieval refusal](context-250k.json); the earlier 231,045-token retrieval passed.

**User-requested retry:** the [same 259,922-token fixture passed](context-250k-retry.json) after the user reported an overlapping prompt. Both attempts are retained; concurrency causation is not established.

**Later eight-image follow-up:** four ordinary-size requests and one eight-image 2520×2520 request passed, with a matching Pi output transcript (uncorrelated request provenance). The large request used 63,456 input tokens and completed in 28.17 seconds. Limit now eight/request; no simultaneous eight-image soak. [Native evidence](eight-images/eight-images.json), [large request](eight-images/eight-images-large.json), [summary](eight-images/summary.json).
