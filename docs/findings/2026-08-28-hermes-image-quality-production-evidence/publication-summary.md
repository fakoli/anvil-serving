# Publication summary: Hermes image-quality production enablement

<!-- benchmark-publication-summary/v1 -->

## Canonical facts

- **Local result:** Real Hermes generated four FLUX.2 Klein images through the
  production Anvil MCP gateway and a managed on-demand ComfyUI worker on one
  RTX 5090.
- **Profiles:** `draft` 512×512, `standard` 768×768, and `high` 1024×1024;
  four steps each; c1; prompt and optional seed are the only generation inputs.
- **Quality:** four of four bounded samples over two prompts passed independent
  visual review. This is not a broad quality comparison or tier ranking.
- **Warm gateway E2E:** 1.352, 1.242, and 1.650 seconds for one draft,
  standard, and high request. Complete Hermes turns took 38.354, 24.420, and
  41.962 seconds and include agent reasoning/tool overhead.
- **Cold path:** explicit approval, managed build/start, identical idempotent
  replay, generation, artifact return, and teardown passed. Its 2,045.626-second
  E2E includes multiple fix-forward iterations and is not normal latency.
- **Safety:** incorrect and retired credentials returned 401; arbitrary
  dimensions failed before execution; video remains `quality_failed` with no
  fallback; the unrelated Qwen service remained in place.
- **Decision:** the exact image workflow is `available=true` at the three fixed
  profiles and remains `promoted=false`. Video is unavailable.
- **Evidence:**
  [canonical finding](../2026-08-28-hermes-image-quality-production.md) and
  [machine-readable summary](summary.json).

## X / short post

RTX 5090: Hermes made 4/4 reviewed FLUX.2 images via Anvil MCP at 512/768/1024px. Cold approval/start and teardown passed; video stays blocked. https://fakoli.github.io/anvil-serving/findings/2026-08-28-hermes-image-quality-production/

## Reddit

**Title:** Local Hermes → Anvil MCP → ComfyUI image generation is live on one RTX 5090

**Body:**

I enabled a bounded production image path from real Hermes through the Anvil
MCP gateway to an on-demand ComfyUI worker on one RTX 5090.

- FLUX.2 Klein 4B FP8, ComfyUI v0.33.4, c1
- fixed profiles: 512×512 draft, 768×768 standard, 1024×1024 high
- four of four images over two prompts passed independent bounded review
- exact cold approval/build/resume/artifact/teardown path passed
- caller-controlled graphs, models, node paths, and arbitrary dimensions are
  rejected
- Wan2.2 video remains quality-failed and unavailable; there is no fallback

Warm gateway E2E was 1.242–1.650 seconds for one request per profile, while
full Hermes turns were 24.420–41.962 seconds because they include agent
reasoning and tool use. The one 2,045.626-second cold run includes every defect
found and fixed forward during deployment, so it is incident evidence—not a
normal cold-start benchmark.

Full configuration, caveats, latency layers, hashes, and fix-forward record:
https://fakoli.github.io/anvil-serving/findings/2026-08-28-hermes-image-quality-production/

## Accessible alt text

Result card for a local RTX 5090 image-generation deployment. Real Hermes
requested FLUX.2 images through an authenticated Anvil MCP gateway and managed
ComfyUI worker. Three warm samples at 512, 768, and 1024 pixels and one cold
512-pixel sample all passed independent bounded visual review. Warm gateway
E2E ranged from 1.242 to 1.650 seconds, while complete Hermes turns ranged from
24.420 to 41.962 seconds. The cold lifecycle passed approval, build, resume,
artifact return, and teardown but took 2,045.626 seconds because it includes
several defects fixed forward. Video remains blocked on quality.

## Claim ledger

| Public claim | Conditions | Evidence |
|---|---|---|
| Real Hermes completed all three fixed image profiles | one c1 warm request per profile; one shared prompt | [Warm profile results](../2026-08-28-hermes-image-quality-production.md#warm-profile-results); [summary](summary.json) |
| Four of four bounded images passed review | two prompts; one sample per cell; independent visual inspection | [Warm profile results](../2026-08-28-hermes-image-quality-production.md#warm-profile-results); [cold lifecycle](../2026-08-28-hermes-image-quality-production.md#cold-approval-build-resume-and-teardown) |
| Warm gateway E2E was 1.242–1.650 seconds | one request per profile; phase telemetry, not caller wall | [Result card](../2026-08-28-hermes-image-quality-production.md#result-card); [summary](summary.json) |
| Cold approval/build/resume/teardown passed | one cold draft; duration includes fix-forward iterations | [Cold lifecycle](../2026-08-28-hermes-image-quality-production.md#cold-approval-build-resume-and-teardown); [summary](summary.json) |
| Arbitrary dimensions and video fail closed | one width-override control and one video request; no execution/fallback | [Safety controls](../2026-08-28-hermes-image-quality-production.md#safety-and-negative-controls); [summary](summary.json) |
| Exact image workflow is available; video is not | three fixed c1 image profiles; Wan2.2 exact workflow remains quality-failed | [Product contract](../2026-08-28-hermes-image-quality-production.md#product-contract); [evidence boundary](../2026-08-28-hermes-image-quality-production.md#evidence-boundary) |
