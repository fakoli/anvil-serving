# GLM-5.3-Flash SGLang SM120 benchmark evidence

This directory is the sanitized evidence bundle for the 2026-09-02 local
qualification of the pinned community SGLang SM120 recipe. Native benchmark
artifacts are authoritative; the narrative finding summarizes them without
turning upstream or Discord claims into local measurements.

## Campaign boundary

- **Campaign:** `2026-09-02-glm53-sglang-sm120-qualification`
- **Candidate:**
  `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO@c3cbb9891b67c741bcbf6b176dd7af9265b069db`
- **Runtime:** `ormandj/sglang-glm53-flash-sm120` `v0.1.1-rc.14` at
  `sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`
- **Hardware:** two RTX PRO 6000 Blackwell Max-Q cards, exclusive TP=2 over
  PCIe without NVLink, Windows 11/Docker Desktop/WSL2
- **Selected recipe:**
  [`configs/glm53-flash-ormandj-sglang-sm120-tp2-240k-c1-adaptive-mtp-recipe.toml`](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-240k-c1-adaptive-mtp-recipe.toml)
- **Decision:** locally `verified` challenger, `no-promotion`

This directory preserves the qualification decision as observed. A later
operator-approved reserve waiver reclassified the already-passing 393K/C1
profile as `verified` and authorized its promotion. See the
[promotion finding](../2026-09-02-glm53-sglang-sm120-393k-promotion.md) and
[`vram-policy-reclassification.json`](vram-policy-reclassification.json); the
original raw artifacts below are unchanged.

## Result index

- [`summary.json`](summary.json) — bounded decision and headline metrics
- [`promotion-publication-summary.md`](promotion-publication-summary.md) —
  derivative copy for the later human-approved 393K promotion
- [`publication-summary.md`](publication-summary.md) — compact human-readable
  result
- [`source-registry.json`](source-registry.json) and
  [`upstream-source-lock.json`](upstream-source-lock.json) — date-aware source
  provenance and stable-versus-rc.14 claim boundary
- [`configuration-and-identity.json`](configuration-and-identity.json) —
  immutable local candidate identity
- [`friction-log.md`](friction-log.md), [`nccl-cumem-probe.md`](nccl-cumem-probe.md),
  and [`log-audit.md`](log-audit.md) — failures, fix-forward decisions, and
  bounded final log review
- [`restoration.json`](restoration.json) — exact before/after reconciliation;
  [`final-restoration-recheck.json`](final-restoration-recheck.json) — fresh
  goal-closure health, identity, routed-smoke, GPU, candidate-absence, and
  shared-memory assertions

## Selected 240K/C1 artifacts

- [`safe240k-preflight-all-low.json`](safe240k-preflight-all-low.json) — all
  ten thinking-disabled functional checks
- [`safe240k-preflight-thinking-enabled.json`](safe240k-preflight-thinking-enabled.json)
  and [`safe240k-thinking-control.json`](safe240k-thinking-control.json) —
  verified bidirectional thinking control
- Capacity r3 at [4K](safe240k-capacity-c1-4k-r3.json),
  [120K](safe240k-capacity-c1-120k-r3.json), and
  [230K nominal](safe240k-capacity-c1-230k-r3.json)
- [`safe240k-quality-coding-agent-v2-disabled-r3-visible2048.json`](safe240k-quality-coding-agent-v2-disabled-r3-visible2048.json)
  — five deterministic coding-agent checks, three repetitions each
- [`safe240k-multimodal-image-c1.json`](safe240k-multimodal-image-c1.json) —
  deterministic image/OCR corpus
- [`safe240k-endurance-c1-4k-r60.json`](safe240k-endurance-c1-4k-r60.json) —
  60-request endurance run
- [`post-workload-state.json`](post-workload-state.json) — health, counters,
  adaptive MTP state, and 3 GiB reserve gate

## Controls and rejected envelopes

- The 131K [no-speculation](nospec-131k-capacity-c1-4k-r3.json) and
  [adaptive-MTP](adaptive-131k-capacity-c1-4k-r3.json) controls provide the
  matched speculation A/B.
- The exact 499,712-token/C4 profile passed functional work and short
  throughput, but failed the reserve policy; its `full499k-unsafe-*` artifacts
  remain retained.
- The 393,216-token/C1 profile passed functional, quality, image, endurance,
  and deep-context work, but fell to 2,101 MiB free per card after workload;
  its `safe393k-*` artifacts retain the physical measurement that was later
  reclassified under the explicitly recorded model-only reserve waiver.
- Early failed/partial preflights are preserved rather than overwritten.

Real GPU UUIDs, operator paths, credentials, and unsanitized logs remain
outside this public evidence bundle. This qualification did not change a route
or authorize promotion; the later promotion is a separate dated decision.
