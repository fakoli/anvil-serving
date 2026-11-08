# ADR-0046 — Scoped live route reconfiguration

- **Status:** Accepted for implementation
- **Date:** 2026-11-02
- **Relates to:** [ADR-0018](0018-router-transition-safety.md),
  [ADR-0038](0038-inference-owned-model-metadata.md),
  [router operations](../cli/router.md)

## Context

Replacing a configured model previously required restarting the shared router,
interrupting unrelated requests even when their model servers remained healthy.
The gateway already owns explicit route selection, readiness, admission and
streaming. Model lifecycle and qualification belong to the resource owner.

## Decision

Add an opt-in route authority to the existing stdlib router. Keep the boot TOML
unchanged and publish immutable runtime snapshots with a monotonic
`route_revision` and `effective_route_sha256`. Each admitted chat request retains
its selected snapshot through delivery. The existing `config_sha256` remains a
boot identity; consumers fence live observations with both new fields.

An alias-only update selects between unchanged, ready, admitting configured
tiers without draining existing requests. A tier replacement persists an
independent hold, stops new bindings to its scope, drains transport leases and
durable unresolved usage records, commits the next revision, publishes it,
checks exact served identity and durably readmits. Other tiers keep serving.
Ordinary operator quiescence remains independent of the transaction's hold.

The single bounded JSON authority retains the current projection, one previous
projection and the current transaction receipt. A stable process lock,
restricted file custody, atomic replacement, file and directory synchronization,
and strict startup validation protect the authority. Missing or unsafe enabled
state prevents startup. Initialization is explicit. Failed or uncertain changes
stay scoped and require a fresh recovery preview; rollback publishes a newer
revision rather than moving the revision counter backward. Cancelling a prepared
change verifies the still-current original target before releasing its hold.

Management requires operator credentials and an expiring, content-bound preview.
Managed promotion prepares and drains before model unload, then resumes with
campaign and observed container evidence. Advertised model names and configured
fingerprints do not prove loaded weight bytes. No route operation itself
authorizes model lifecycle, qualification or promotion.

## Considered options

- Restarting the router preserves a single startup configuration but interrupts
  unrelated streams during promotion.
- Replacing the gateway with a proxy/control-plane stack adds a runtime and
  migration boundary while leaving model qualification and usage accounting to
  integrate separately.
- Extending the existing router preserves its protocol and ownership contracts
  and needs no new runtime dependency. This is the selected implementation.

## Consequences

- This first version requires POSIX file locking and directory synchronization.
  Routers without `server.route_state_path` retain their existing behavior.
- Alias and tier sets, authentication, listeners and protocol adapters remain
  restart-only. Replicas, upstream-owned metadata and automatic concurrency
  sizing are excluded from hot replacement. Shared non-chat endpoints are
  rejected because a chat drain cannot prove their owner is idle.
- Usage schema v4 captures the route generation at invocation start without
  rewriting historical records. Unresolved legacy associations block upgrade
  or destructive drain conservatively.
- Preview caches and drain waits are bounded. The implementation adds no file
  watcher, automatic rollback, fallback selection or automatic promotion.
- Source verification uses synthetic serves and fault injection. Installation
  and live continuity acceptance require a separate managed deployment.

## Addendum — 2026-11-08: scoped direct-tier request settings and authority v2

The existing preview/apply authority also governs a deliberately narrow class
of direct-tier request-setting updates. Only `reasoning_effort`, `temperature`,
and `top_p` in the tier's `extra_body` and defaults are eligible. The mutation
validator rejects every other key change. Hard overrides retain priority over
caller values; soft defaults yield to caller values. Thus a hard
`reasoning_effort = "medium"` may be moved to a soft
`reasoning_effort = "max"` default through the existing preview/apply and
affected-scope drain transaction, without a model restart.

The route authority projection is versioned. A restored legacy state loads into
the runtime while retaining its original pinned boot bytes, revision, and
effective-route hash; restore does not rewrite its authority. The first
confirmed new route mutation writes the v2 envelope and emits a v2 projection.
A legacy pending transaction requires explicit resume and is not implicitly
migrated; its resume or cancellation retains the legacy projection and hash
exactly. An old router binary cannot recover a state after the v2 write.
Recovery is fix-forward on a v2-capable router image, never replacement with a
legacy projection.

The initial software upgrade that supplies v2 is a managed router restart with
unchanged boot TOML. After that one-time upgrade, the allowlisted settings
changes are hot route transactions. This addendum does not widen hot mutation
to model lifecycle, route backends, aliases, listeners, authentication,
protocol adapters, or any unlisted request setting.
