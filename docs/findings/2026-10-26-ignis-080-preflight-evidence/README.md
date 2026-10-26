# Ignis 0.8 one-GPU preflight evidence

This directory is the sanitized public projection for the 2026-10-26 focused
functional preflight and supported tool-choice follow-up. Protected native
artifacts remain authoritative; the files here retain both phase outcomes,
public-safe identity, projection provenance, failure analysis, and explicit
gaps.

## Campaign boundary

- **Campaign ID:** `2026-10-26-ignis-080-preflight`
- **Capability:** direct text/tool functional preflight and mandatory-tool
  request-policy qualification
- **Executable preflight revision:** `dc576b25083d7cfdab1554db89379feee82346b5`
- **Phase-two harness patch:**
  `0c9b211fd6bf8d49493a2db46f5def4ebfe9a887d2470d8a74f6f37331b46206`,
  reviewed at `c727ff01f34d2d7564fab63d94203fec68890baf`
- **Evidence label:** `functional`
- **Outcome:** phase-one `tool_choice: "auto"` expectation unmet at 3/4; the
  same runtime/model/recipe later passed the separately frozen
  `tool_choice: "required"` normal-thinking path across 3/3 mechanism cells,
  5/5 full checks with 9/9 observations, and a 4/4 seed-18 repeat
- **Decision label:** `no-promotion`
- **Promotion boundary:** this packet does not authorize lifecycle, route,
  client, or promotion changes

The candidate used Ignis 0.8.0 with the pinned Qwen3.8 Flash Next artifact on
one RTX PRO 6000 Blackwell Max-Q. The second equal card remained protected and
had no model allocation in retained candidate snapshots. Phase two changed the
harness/request policy only; no engine, image, model, profile, or recipe changed.
This is not a performance, capacity, SWE, coding-quality, or engine-comparison
campaign.

## Common artifacts

- [`artifact-manifest.json`](artifact-manifest.json) - finalized ten-role
  ledger and exact public-byte identities
- [`source-registry.json`](source-registry.json) - dated official and local
  source provenance
- [`summary.json`](summary.json) - bounded machine-readable outcome
- [`friction-log.md`](friction-log.md) - failure, count correction, warnings,
  and deferred fix-forward work
- [`restoration.json`](restoration.json) - starting/ending state and independent
  post-run checks

## Workload, identity, and raw evidence

- [`plan.json`](plan.json) - corrected nine-request/six-phase execution shape,
  frozen controls, and zero-retry boundary
- [`configuration.json`](configuration.json) - public-safe model/runtime/recipe,
  hardware, and resource identity
- [`preflight-native.json`](preflight-native.json) - sanitized projection of
  the native `preflight/v2` artifact; synthetic reasoning excerpts and tool
  outputs are retained
- [`engine-request-summary.json`](engine-request-summary.json) - public-safe
  admitted/done event correlation for all nine requests
- [`resource-summary.json`](resource-summary.json) - bounded GPU, host, cgroup,
  and OOM observations
- [`restoration-smoke.json`](restoration-smoke.json) - sanitized native routed
  smoke projection after the phase-one incumbent reload and readmission
- [`restoration-smoke-phase-two.json`](restoration-smoke-phase-two.json) -
  sanitized native routed smoke projection after phase-two exact restoration
- [`independent-restoration-review.json`](independent-restoration-review.json) -
  public-safe independent Sol acceptance of operational restoration only
- [`tool-failure-analysis.md`](tool-failure-analysis.md) - request construction,
  `tool_choice` semantics, validator boundary, and engine correlation
- [`tool-usage-contract.md`](tool-usage-contract.md) - pinned Ignis 0.8 source
  contract for `auto`, `required`, thinking, and the request-policy boundary
- [`tool-choice-followup.json`](tool-choice-followup.json) - all three
  mechanism cells, selected full preflight, independent-seed repeat, exact
  request accounting, transport hashes, and interpretation limits
- [`prepared-followup.json`](prepared-followup.json) - separate 12-cell coding
  plan status; zero cells were submitted
- [`projection-ledger.json`](projection-ledger.json) - protected-source hashes,
  transformations, public paths, and disclosure boundaries

## Result boundary

The phase-one shared-prefix tools check missed its frozen expectation at 3/4.
The failed response was ordinary assistant prose with `finish_reason: "stop"`
and no `tool_calls`. Since the request used `tool_choice: "auto"`, the response
is not itself a protocol violation, but it fails the harness's explicit
instruction and all-four tool expectation. No root cause, engine defect, or
model defect is assigned.

The original private plan and pre-live review counted six HTTP requests. Six
was the number of sequential phases; the actual command made nine HTTP
requests because the tools phase submitted four requests and the tool-result
phase submitted two. Native observations and engine completions agree at nine.

Phase two retained that result and tested the documented mandatory-tool path.
All three seed-17 mechanism cells passed. The frozen selection rule chose
`required` with default thinking; its full run passed 5/5 checks and 9/9
observations, and the seed-18 tools repeat passed 4/4. Engine terminal IDs 1-16
formed a complete set. Literal HTTP 200 was retained for 12 captured tool
requests, not all 16 observations. The passing one-request seeded `auto` cell
does not rescore the older unseeded 3/4 batch or establish `auto` reliability.

The prepared coding follow-up remained unrun because its explicit evaluation
route and protected-source prerequisites were not available through a
supported path. It remains a separate zero-cell plan and contributes no model
measurement.

## Publication state

Phase-one operational restoration is complete: exact incumbent recipe/image/
revision/GPU-order identity, candidate absence, byte-identical before/restored
router custody objects, managed readmission, one passing routed smoke, idle
engine state, and zero OOM delta were retained. The first smoke command was
rejected locally for a missing explicit auth environment variable and made
zero requests; the r2 command used the supported credential resolver and made
the one passing request. Router configuration-file bytes were not inspected
and no router configuration write was attempted.

Phase two also passed exact operational restoration: all compared incumbent
identity fields matched, the candidate was absent, router custody was
unchanged, managed readmission and one routed smoke passed, the engine was
idle, and container OOM counters stayed at zero. The single smoke is not full
quality or strict-JSON acceptance, and mounted router configuration bytes were
not independently inspected. Independent exact-head publication review is the
remaining publication gate. No compact social publication was requested, so
the final `publication-summary` role is `not-applicable` with that reason.
