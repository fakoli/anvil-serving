# ADR-0012 — Serve & router management flows through anvil-serving verbs

- **Status:** Accepted
- **Date:** 2026-07-04
- **Relates to:** ADR-0002 (serves are compose-defined), ADR-0004 (router as a service), ADR-0009 (profile write-back loop)

## Context

Operating the fakoli-dark deployment repeatedly required RAW `docker` / `docker compose` for
things that are conceptually "manage a serve": bring up an experiment model not in `serves.toml`,
retire an ad-hoc container squatting a port, restart the deployed router, and — most importantly —
**promote** a measured profile into the running router. ADR-0002 gave `anvil-serving serves
{status|up|down}` for the model backends, and ADR-0004 made the router a container, but nothing
managed the router container itself, and there was no verb for the containerized write-back
(ADR-0009). Every raw-docker workaround is an inconsistent, unrepeatable, and dangerous surface:
promotion in particular meant hand-writing a root-owned file into a read-only-mounted config volume
with no validation and no rollback — a malformed profile silently mis-routes every request, or
crash-loops the router.

The guiding principle: **if managing a serve needs a raw CLI tool other than anvil-serving, that is
a product gap.** Close the gaps with verbs.

## Considered options

- **Keep using raw docker + a runbook.** Rejected: inconsistent, no validation/rollback, invisible
  to `serves status`, and the promote path is genuinely hazardous by hand.
- **A generic `anvil-serving exec`/passthrough.** Rejected: re-exposes raw docker with no safety.
- **Typed verbs with the safety baked in (chosen).** Each management action is a first-class verb
  that funnels through the same tested docker seams and adds the guardrails the raw path lacked.

## Decision

Add management verbs; **the deployed router's config + profile is a MUTABLE docker volume**
(`anvil-router-cfg`), owned by `anvil-serving router promote`, not a repo bind-mount.

- `serves rm` (retire any container, incl. a non-manifest port squatter), `serves adopt` (recreate
  an externally-started serve under compose), `serves up --compose <file>` (an experiment serve not
  in the manifest). `serves down` honors `--dry-run`.
- `anvil-serving router {up|down|restart|reload|status|token}` manages the ADR-0004 container.
- `anvil-serving router promote --profile [--config]` is the containerized write-back:
  **(1) validate** the profile against the DEPLOYED image's OWN loader (version-safe — a newer local
  checkout must not re-verdict a profile the deployed router would reject); **(2) back up** the
  current profile/config inside the volume; **(3) atomically write** the new file via a root
  side-container (`--entrypoint sh`, temp+`mv` within the volume); **(4) reload** (restart) and
  **verify it stays up** (settle + consecutive `running` + `RestartCount` unchanged); **(5) roll
  back** to the backup — or remove a first-ever profile — on a crash-loop.

## Consequences

- The write-back moat (ADR-0009) now has an operator-facing, safe entry point: measure with
  `calibrate` → `router promote`. No more hand-editing a root-owned volume.
- The router config model changes from an immutable repo bind-mount to a mutable volume; the
  fakoli-dark compose `router` service pins the deployed image + mounts `anvil-router-cfg` (a fresh
  `build:` would produce a schema-incompatible image and diverge from what is deployed). Redeploying
  to a freshly-built image is a deliberate, separate step.
- `promote` is pinned to the deployed image tag for validation/writes; bump `--image` when the
  router is redeployed, or validation runs against the wrong loader.
- Remaining raw-docker for serve management is now a regression to be fixed with a verb, not a
  workflow to be documented.
