# Publication summary: Hermes image-quality production enablement

<!-- benchmark-publication-summary/v1 -->

## Canonical facts

- **Local result:** Real Hermes generated seven FLUX.2 Klein images through the
  production Anvil MCP gateway and a managed on-demand ComfyUI worker on one
  RTX 5090.
- **Profiles:** `draft` 512×512, `standard` 768×768, and `high` 1024×1024;
  four steps each; c1; prompt and optional seed are the only generation inputs.
- **Quality:** five of seven bounded samples over five prompts passed strict
  independent visual review. Two later draft samples are retained failures for
  origami/material fidelity and exact bird count. This is not a broad quality
  comparison or tier ranking.
- **Warm gateway E2E:** 1.352, 1.242, and 1.650 seconds for one draft,
  standard, and high request. Complete Hermes turns took 38.354, 24.420, and
  41.962 seconds and include agent reasoning/tool overhead.
- **Cold path:** explicit approval, managed build/start, identical idempotent
  replay, generation, artifact return, and teardown passed. Its 2,045.626-second
  E2E includes multiple fix-forward iterations and is not normal latency.
- **Final regression:** skill `1.0.2` returned a complete resume bundle,
  reattached with `created=false` to the same job, polled to terminal in one
  English turn, returned the native PNG, and measured 652.607 seconds E2E.
- **Safety:** incorrect and retired credentials returned 401; arbitrary
  dimensions failed before execution; video remains `quality_failed` with no
  fallback; the unrelated Qwen service remained in place.
- **Decision:** the exact image workflow is `available=true` at the three fixed
  profiles and remains `promoted=false`. Video is unavailable.
- **Evidence:**
  [canonical finding](../2026-08-28-hermes-image-quality-production.md) and
  [machine-readable summary](summary.json).

## X / short post

RTX 5090: Hermes made 7 FLUX.2 images via Anvil MCP at 512/768/1024px; 5 passed strict review and 2 draft prompts exposed fidelity/count limits. Cold resume/teardown passed; video stays blocked. https://fakoli.github.io/anvil-serving/findings/2026-08-28-hermes-image-quality-production/

## Reddit

**Title:** Local Hermes → Anvil MCP → ComfyUI image generation is live on one RTX 5090

**Body:**

I enabled a bounded production image path from real Hermes through the Anvil
MCP gateway to an on-demand ComfyUI worker on one RTX 5090.

- FLUX.2 Klein 4B FP8, ComfyUI v0.33.4, c1
- fixed profiles: 512×512 draft, 768×768 standard, 1024×1024 high
- five of seven images over five prompts passed strict bounded review; two
  draft failures expose origami/material and exact-count limits
- exact cold approval/build/resume/artifact/teardown path passed
- caller-controlled graphs, models, node paths, and arbitrary dimensions are
  rejected
- Wan2.2 video remains quality-failed and unavailable; there is no fallback

Warm gateway E2E was 1.242–1.650 seconds for one request per profile, while
full Hermes turns were 24.420–41.962 seconds because they include agent
reasoning and tool use. The one 2,045.626-second cold run includes every defect
found and fixed forward during deployment, so it is incident evidence—not a
normal cold-start benchmark. A final exact-resume regression completed in
652.607 seconds E2E.

Full configuration, caveats, latency layers, hashes, and fix-forward record:
https://fakoli.github.io/anvil-serving/findings/2026-08-28-hermes-image-quality-production/

## Accessible alt text

Result card for a local RTX 5090 image-generation deployment. Real Hermes
requested FLUX.2 images through an authenticated Anvil MCP gateway and managed
ComfyUI worker. Seven bounded samples covered all three profiles and four cold
paths; five passed strict visual review, while two draft samples missed
origami/material fidelity and an exact three-bird count. Warm gateway E2E
ranged from 1.242 to 1.650 seconds, and the final exact-resume cold regression
took 652.607 seconds E2E. Video remains blocked on quality.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Real Hermes completed all three fixed image profiles | one c1 warm request per profile; one shared prompt | [Warm profile results](../2026-08-28-hermes-image-quality-production.md#warm-profile-results); [summary](summary.json) |
| Five of seven bounded images passed strict review | five prompts; two retained draft failures; independent visual inspection | [Warm profile results](../2026-08-28-hermes-image-quality-production.md#warm-profile-results); [final regressions](../2026-08-28-hermes-image-quality-production.md#final-exact-revision-regressions) |
| Warm gateway E2E was 1.242–1.650 seconds | one request per profile; phase telemetry, not caller wall | [Result card](../2026-08-28-hermes-image-quality-production.md#result-card); [summary](summary.json) |
| Cold approval/build/resume/teardown passed | one cold draft; duration includes fix-forward iterations | [Cold lifecycle](../2026-08-28-hermes-image-quality-production.md#cold-approval-build-resume-and-teardown); [summary](summary.json) |
| Final skill reattached exactly and completed in one English turn | one cold draft; complete bundle; `created=false`; same job; 652.607-second E2E | [Final regressions](../2026-08-28-hermes-image-quality-production.md#final-exact-revision-regressions); [summary](summary.json) |
| Arbitrary dimensions and video fail closed | one width-override control and one video request; no execution/fallback | [Safety controls](../2026-08-28-hermes-image-quality-production.md#safety-and-negative-controls); [summary](summary.json) |
| Exact image workflow is available; video is not | three fixed c1 image profiles; Wan2.2 exact workflow remains quality-failed | [Product contract](../2026-08-28-hermes-image-quality-production.md#product-contract); [evidence boundary](../2026-08-28-hermes-image-quality-production.md#evidence-boundary) |
