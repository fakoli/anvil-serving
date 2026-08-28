# Media gateway source-merge readiness

**Date:** 2026-08-28

**Source base:** `be2c4e3e82c5c459d41a49c2b339de279309e2a4`

**Executable candidate tested:** `de4064ff5e6adf2338cdd5645d73e352860cc4d1`

**Package / bridge versions:** Anvil Serving `0.35.1`; bundled MCP bridge
`0.18.0`

**Disposition:** source merge candidate; live media enablement remains
`human_required`

This packet gates the new MCP/A2A media gateway, durable media operations,
managed worker lifecycle, pinned image and video workflows, and the narrow
Hermes skill. It authorizes neither a package publication nor a live gateway,
controller, client, route, workflow-promotion, or serving-state change.

The machine-readable companion is
[release-summary.json](2026-08-28-media-gateway-release-readiness-evidence/release-summary.json).
Functional and capacity measurements remain in the
[ComfyUI media qualification](2026-08-28-comfyui-media-qualification.md).

## Contract closure

| Boundary | Evidence | Source-merge result | Live result |
| --- | --- | --- | --- |
| Existing `/v1` router | Full router suite plus byte-comparison fixture with the gateway enabled and disabled | pass | unchanged |
| Modern MCP | Scoped catalog, schema, auth-before-dispatch, audience-filtered discovery and execution, and gateway protocol tests | pass | not deployed |
| Legacy stdio MCP | Node 20 bridge test against MCP SDK 1.29 and the same modern upstream controller contract | pass | Hermes not installed or changed |
| A2A | Agent Card, submission, polling, streaming, cancellation, canonical errors, ProtoJSON metadata, and cross-principal tests | pass | not deployed |
| Durable media operations | Atomic admission/idempotency, ordered transitions, cancellation, continuously running reconciliation, scoped artifact ownership, expiry, and range tests | pass | not enabled |
| Managed worker | Controller previews, cold-start approval wiring, confirmation gates, reservation ownership, lease-based crash recovery, cross-process prepare/release serialization, logs, and rollback tests | pass | isolated qualification ended absent |
| Image workflow | Exact graph/model/runtime pins; decodable PNG; 12,919 MiB peak; clean rollback | functional/capacity pass | quality `human_required`; unavailable |
| Video workflow | Exact graph/model/runtime pins; decodable H.264 MP4; 18,263 MiB peak; clean rollback | functional/capacity pass | quality `human_required`; unavailable |
| Hermes skill | Eight-tool allowlist, caller-only scopes, identical-request idempotency, truthful approval/unavailable handling, artifact retrieval smoke | pass | no real-client acceptance yet |

The image and video descriptors deliberately remain `available = false` and
`promoted = false`. Functional transport, decoding, and capacity evidence do
not substitute for independent perceptual review.

## Artifact identity and rollback

- The built wheel contains the packaged MCP bridge plus the registry, bundle
  lock, both descriptors, and both exact workflow graphs. The isolated wheel
  smoke imported outside the checkout and exercised the canonical router CLI.
- The image graph digest is
  `991b63b8c61ff4322d72b8ae81ef43656f4905ddf4b0709c1989b84cfb8f2e4f`.
- The video graph digest is
  `bd12b2de2a33bbedc91d7ad6120714f3c3adbd174694ac74d6f0213ecef9572e`.
- The managed worker qualification used ComfyUI `v0.33.4` at revision
  `7a131a3afadc8200120f67f9236311a2c48b7445`, a digest-pinned CUDA/PyTorch
  base, pinned custom nodes, and content-hashed model assets.
- Final teardown proved the worker container absent, health unset, GPU use
  returned to 448 MiB, and the media reservation ledger returned to zero
  committed MiB. The router was not changed.

Gateway, controller, and bridge source identity is closed in the source/wheel
candidate. Deployed endpoint parity is intentionally not claimed: none of
those components was rebuilt or deployed for live media traffic. A future
enablement packet must prove the exact merged revision and configuration at
every endpoint, then run real Hermes and artifact-delivery smokes before
exposure.

## Verification record

| Gate | Result |
| --- | --- |
| Full Python regression | `4308 passed, 9 skipped` in 178.70 seconds |
| Media/A2A/controller/router matrix | `586 passed` |
| Hermes-focused Python contract | `20 passed` with adjacent MCP/gateway coverage |
| Legacy + modern Node MCP bridge | 2 tests passed; generated bridge rebuilt |
| Node dependency audit | zero vulnerabilities after lock-only transitive updates to `fast-uri 3.1.6` and `hono 4.13.5` |
| Python lint | repository-wide Ruff passed |
| Strict documentation build | passed after replacing links outside the MkDocs source tree with repository-path literals |
| Markdown links | 420 tracked Markdown files passed |
| CLI reference audit | docs, skills, and full scopes passed; 806 full-scope files, zero violations |
| Semantic secret hygiene | self-test passed; 1,991 tracked files, zero findings |
| Signature secret hygiene | pinned Gitleaks image scanned the cleaned candidate with network disabled; zero findings |
| Distribution build | wheel and sdist built in an isolated environment |
| Distribution metadata | Twine passed both artifacts |
| Isolated wheel smoke | package data, bridge, all media workflow assets, and router CLI passed |

The gate retained three initially non-passing results instead of hiding them:

1. the full suite found stale expected MCP catalog and serve-group fixtures;
2. strict MkDocs rejected four new repository links outside its documentation
   source tree; and
3. `npm audit` found one high and one moderate transitive advisory.

The fixtures now characterize the actual explicit catalog/groups, the docs no
longer ask MkDocs to resolve non-doc files, and the lockfile resolves patched
transitives. Focused checks passed after each correction, followed by the clean
full-suite result above. A direct Gitleaks scan of the developer worktree also
reported seven ignored generated build/site copies. Those generated directories
and the pytest cache were removed; the pinned no-network scan then passed the
clean candidate with zero findings, matching CI's checkout shape.

The first independent adversarial review blocked release with ten findings:
MCP audience enforcement, production reconciliation, atomic admission,
lifecycle crash recovery, cold-start approval wiring, A2A conformance,
artifact read scope, CLI dry-run consistency, offline bundle recovery, and
stale release evidence. The executable candidate above closes those findings
with regression coverage. Source merge remains pending an independent re-review
of the final immutable public and private candidate revisions.

## Remaining live gates

Live enablement remains blocked until all of the following are independently
approved and recorded:

1. perceptual-quality dispositions for both exact workflow versions;
2. a merged-revision package/image build and exact gateway, controller, bridge,
   worker, workflow, runtime, and configuration parity check;
3. scoped real-Hermes discovery, image, video, cancellation, unavailable, and
   approval-required smokes with fallback disabled;
4. authenticated artifact range/download checks through the deployed origin;
5. a deployed rollback that restores the prior gateway/controller and leaves
   the worker absent when no job owns it; and
6. explicit human authorization for route exposure and workflow availability.

Until then, the safe closure is to merge the reviewed source only. No media
route is published, no workflow is made available, no model is promoted, and
no worker remains running.
