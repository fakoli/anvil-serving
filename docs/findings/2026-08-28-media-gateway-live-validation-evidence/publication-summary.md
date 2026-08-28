# Publication summary: exact media gateway live validation

<!-- benchmark-publication-summary/v1 -->

This file is derivative publishing copy. The linked dated finding and raw
artifact record are authoritative.

## Canonical facts

- **Model identity:** FLUX.2 Klein 4B FP8 and Wan2.2 TI2V 5B at the revisions
  pinned by the Anvil workflow bundle.
- **Runtime identity:** Anvil Serving `0.35.1` merge `19320de6`; ComfyUI
  v0.33.4 at `7a131a3a`; CUDA 13.0; PyTorch 2.13.0+cu130.
- **Local setup:** one RTX 5090, isolated cross-host gateway/resource/client
  path, c1.
- **Recipe:** managed `serves.comfyui.toml` and immutable workflow graphs
  `991b63b8...2e4f` / `bd12b2de...9572`.
- **Measurement path:** authenticated modern MCP, bundled stdio bridge, real
  Hermes, A2A 1.0, artifact origin, independent decode, and visual review.
- **Headline result:** two of two FLUX.2 images passed prompt-adherence review;
  the one 17-frame Wan2.2 MP4 decoded correctly but failed perceptual review.
- **Capability result:** MCP, A2A, idempotency, artifact range/download,
  cold-backend, approval, and managed teardown controls passed.
- **Important caveat:** one short video cannot assess temporal smoothness, and
  the run was an isolated exact-build deployment rather than a production
  cutover.
- **Decision:** both workflows remain unavailable; no route, workflow, model,
  or client default was promoted.
- **Canonical evidence:**
  <https://fakoli.github.io/anvil-serving/findings/2026-08-28-media-gateway-live-validation/>

## X / short post

Preferred project limit: 260 literal characters including the URL. Hard limit:
280 characters. Recount immediately before posting.

```text
Local RTX 5090: Anvil media passed Hermes MCP/A2A; 2 FLUX.2 images passed visual review. Wan2.2 video decoded but failed prompt adherence. No promotion. https://fakoli.github.io/anvil-serving/findings/2026-08-28-media-gateway-live-validation/
```

## Reddit

Preferred title limit: 120 characters. Check the target community's current
rules before posting.

```text
Local RTX 5090 Anvil media: Hermes/MCP pass, FLUX.2 image pass, Wan2.2 video quality fail
```

```markdown
I tested the exact merged Anvil media gateway with FLUX.2 Klein 4B FP8 and
Wan2.2 TI2V 5B on one local RTX 5090.

- Anvil Serving 0.35.1 from merge `19320de6`
- ComfyUI v0.33.4, immutable workflow graphs, c1
- Authenticated MCP through the packaged Hermes skill, plus A2A and artifact
  retrieval controls

Headline results:

- Two of two FLUX.2 images passed independent prompt-adherence review.
- The 117,738-byte, 17-frame Wan2.2 H.264 artifact decoded correctly but failed
  visual prompt adherence because severe spatial artifacts dominated its
  frames.
- Cold-backend validation created no job, and managed teardown returned the
  worker and reservation ledger to empty.

The important caveat is that one contact sheet cannot establish temporal
smoothness, and this was an isolated exact-build deployment rather than a
production cutover. This is a bounded local result, not a general ranking.

Full methodology, failures, and raw record:
https://fakoli.github.io/anvil-serving/findings/2026-08-28-media-gateway-live-validation/

What matched or differed on your hardware?
```

## Screenshot alt text

Result card for an exact-build Anvil media gateway test on one RTX 5090. It
reports passing authenticated Hermes MCP and A2A controls, two prompt-adherent
FLUX.2 images, one decodable 17-frame Wan2.2 video that failed visual prompt
adherence, an unchanged unavailable/no-promotion decision, and complete worker
teardown.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Exact-build Hermes MCP/A2A path passed | isolated cross-host candidate; c1; authenticated | [Finding protocol controls](../2026-08-28-media-gateway-live-validation.md#protocol-and-lifecycle-controls); [raw record](live-validation.json) |
| Two FLUX.2 images passed prompt-adherence review | two 512×512/four-step samples; independent reviewer | [Finding artifact table](../2026-08-28-media-gateway-live-validation.md#artifacts-and-independent-review); [raw record](live-validation.json) |
| Wan2.2 decoded but failed visual prompt adherence | one 512×288/17-frame/eight-step sample; contact-sheet spatial review only | [Finding artifact table](../2026-08-28-media-gateway-live-validation.md#artifacts-and-independent-review); [raw record](live-validation.json) |
| No workflow was promoted | isolated candidate; public descriptors unchanged; worker torn down | [Finding evidence boundary](../2026-08-28-media-gateway-live-validation.md#evidence-boundary); [raw record](live-validation.json) |
