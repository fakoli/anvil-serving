# Ticket: Observatory workbench ↔ controller catalog-contract drift is invisible until a controller recreate

Date: 2026-09-15. Status: open (fix partially shipped; release-readiness gap remains).

## Symptom

After any controller container recreate, the Anvil Workbench dashboard can silently
degrade to static, stale declarations: serve status shows `unknown`, controls are
absent, and the evidence view freezes at its last write. The dashboard keeps
rendering, so the failure looks like "stale data" rather than an outage.

## Root causes observed (stacked, all fail-closed by design)

1. **Operation allowlist drift.** The controller was recreated with a command line
   whose `--allow-operation` set no longer covered every tool the dashboard adapter
   declares. The adapter's catalog gate computed a digest over the visible tools,
   mismatched its pinned `expected_catalog_digest`, and failed closed to an empty
   catalog. Serve status and operate controls depend on that catalog.
2. **Transport response cap.** The `/tools/list` catalog outgrew the transport's
   64 KiB default read limit (observed: ~71.5 KB with the full adapter catalog).
   Both the dashboard adapter's transport and the controller container healthcheck
   (`controller status`) cap reads at 64 KiB by default, so even a correctly
   allow-listed controller reported `response_too_large` / unhealthy.
   Fix merged in PR #516: `controller.max_response_bytes` observatory-config
   passthrough (the transport rejects non-integer or out-of-range values at
   construction; the ControllerTransport ceiling is 8 MiB), plus an operator
   healthcheck flag. Activation on the live workbench is pending a release flip.
3. **Stale declarations.** The workbench config pinned a serve and recipe that no
   longer existed after a promotion, so even a healthy catalog rendered dead
   resources. Declaration realignment is operator config work; the gap is that
   nothing compares declarations against live state.
4. **Serve manifest validation gap.** A promoted dual-GPU-exclusive serve was
   missing its `rollback_router_config`, which made the controller refuse to load
   the entire serves manifest (`bad_manifest`) — every serve's status, not just the
   incomplete one, became unavailable through the workbench.

Causes 3 and 4 do not require a controller recreate to bite: any manifest edit or
promotion that leaves declarations or validation inconsistent disables workbench
serve telemetry on its own. The recreate is what reliably surfaces causes 1 and 2.

## Release-readiness gap

None of these failures surface until a controller recreate, and the recreate is
exactly what coordinated deployments do. A deployment that changes the controller
command line, the tool catalog, or the serves manifest can break the dashboard with
no gate noticing. Proposed doctor/deployment checks:

- Compare the pinned `expected_catalog_digest` against the live catalog digest and
  report drift with the observed value (the adapter already computes it; expose it
  in `controller status` output).
- Verify every tool the dashboard adapter declares is covered by the controller's
  `--allow-operation` set before activation, not after.
- Validate the serves manifest loads (`serves_status` succeeds) as part of
  deployment readiness — a manifest that fails validation silently disables all
  serve telemetry.
- Healthcheck and client transport response caps must be sized to the live catalog;
  a catalog-size regression should be visible in `controller status` output.

## Digest recipe note

The pinned digest is computed by the adapter over the **full tool objects**
(name, description, input schema) filtered to the adapter's declared tool set and
sorted by name — not over tool names alone. Recomputing it from names only
produces a digest that never matches. Use the adapter's own observed digest
(`controller status` should expose it; see proposal above) rather than
reimplementing the recipe.

## Operator-side remediation applied (private repo)

- Restored the full `--allow-operation` set on the controller command line.
- Raised the healthcheck response cap; pinned the recomputed catalog digest and
  raised the workbench transport cap in the observatory config.
- Realigned stale serve/recipe declarations to the promoted serve; appended the
  promoted recipe to the production catalog registry.
- Added the missing `rollback_router_config` to the promoted serve manifest.
- Extended the evidence view with benchmark artifacts through 2026-09-14 that the
  workbench evidence window had not yet ingested.

## Rollback values (pre-activation)

- Previous workbench release: `07fed164` (wheel-sha256 `c5932dd5d2be7bb955b84299af408032a658f18faf5b77936e40087c2fb69e69`).
- Candidate release: `d2eff1ef` (wheel-sha256 `e147930ce4f45492983ffb9ff423bc713f5c425eb6d62875c9223a79c354557a`, 426/426 RECORD entries hash-verified).
- The workbench config's `build` field now advertises the candidate wheel; revert
  it together with the `current` symlink when rolling back.