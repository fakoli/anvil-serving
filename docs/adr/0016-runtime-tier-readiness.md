# ADR-0016 — Runtime tier readiness excludes stopped serves without config rewrites

- **Status:** Accepted
- **Date:** 2026-07-12
- **Relates to:** ADR-0002, ADR-0012, ADR-0013

## Context

The router config describes an ordered Fast/Heavy topology, but a configured model container may
be intentionally stopped to free a GPU, starting, or unexpectedly unavailable. Before this change,
the router discovered that state only by sending a real inference request. The resulting transport
failure consumed an attempt and contributed to a tier-global circuit breaker even when the stopped
container was expected operational state.

Rewriting router TOML on every serve transition would require a router restart because deployed
configuration is read at startup. It would also mix durable topology with transient readiness.
Teaching the router to inspect Docker would violate the router/serve ownership boundary and would
not work for remote or non-Docker upstreams.

## Considered options

1. Rewrite tier lists and restart the router on every `serves up/down`. Accurate but disruptive,
   topology-destructive, and coupled to one lifecycle implementation.
2. Have the router inspect Docker container state. Same-host-only and violates ADR-0012's product
   boundaries.
3. Keep topology static and add an injectable runtime-readiness seam backed by bounded HTTP health
   probes. Works across hosts and engines and automatically detects both planned and unexpected
   downtime.

## Decision

Adopt option 3 as the basic readiness layer.

An optional local-tier `health_path` enables cached HTTP readiness. `[router]` controls the positive
probe interval and timeout. Before inference, the routing backend snapshots readiness for the
quality-approved, bound candidates. An unavailable tier is recorded as `skipped-unavailable`, does
not call its inference backend, does not consume retry budget, and does not mutate circuit state.
The next ready candidate remains eligible. After cache expiry, a successful health probe
automatically readmits the recovered tier without config mutation or router restart.

Cloud tiers and tiers without `health_path` remain implicitly available for backward compatibility.
The readiness implementation is injected through the typed seam catalog. It reports availability
only; it never starts, stops, or repairs a serve.

## Consequences

- A stopped Fast container no longer causes a failed model request before Heavy fallback.
- Runtime readiness, quality verification, and circuit health are separate signals.
- Generated router-tier stubs opt local serves into `/health` readiness automatically.
- Existing configs behave exactly as before until `health_path` is added.
- This first slice observes readiness rather than recording explicit administrative intent. A later
  lifecycle-state publisher can add `running/draining/stopped` to the same seam without changing
  the routing contract.
- A configured health endpoint becomes operationally significant and must reflect the data-plane
  serve's ability to accept requests; an incorrect path will keep that tier out of rotation.
