# ADR-0043 — Co-located connector tunnel fast-path over loopback

- **Status:** Proposed (Astra consult 2026-09-11: accept-with-changes, incorporated)
- **Date:** 2026-09-11
- **Relates to:** ADR-0019 (tailnet edge ownership), PR #484 (dispatcher
  connection reuse), `.tickets/2026-09-11-observatory-502-dispatcher-connection-reuse.md`,
  `docs/ANVIL-CONNECT-TRANSPORT.md`, `connect/internal/tunnelgate/`

## Context

Anvil Connect's browser and API resources reach their origins through a
reverse tunnel. The connector dials the public tunnel URL
(`wss://connect-tunnel.sekoudoumbouya.app`), which resolves through
Cloudflare and terminates at cloudflared **on the same host**. The public
edge fronts the Go tunnel gate (`connect/internal/tunnelgate/`), which owns
admission: transport leases, exact reverse-descriptor validation, continuous
authority rechecks, gate-only certificate identity, and per-backend
credentials. The gate forwards to the private `wstunnel` backend on loopback.
Every connector tunnel connection therefore pays the public-edge loop:

```
connector → CF edge → cloudflared → edge caddy → gate (local) → wstunnel backend (local)
```

Measured on the dashboard path (2026-09-11): a direct gate/envelope
handshake costs ~20 ms while the same handshake through the CF loop costs
~150-330 ms serialized. PR #484 removed the per-request handshake cost for
steady-state traffic (pooled h2, 2-connection budget), so warm request
traffic multiplexes over one connection and no longer pays the loop. The
residual cost is **connection lifecycle**: cold bursts, pool retirement
(idle timeout, connector restart, leaf rotation), and reconnection each
re-establish inner connections through the CF loop. The connector and
gateway are co-located on fakoli-dark; the segment between them does not
need to leave the machine, but today the connector's tunnel dial does.

The same geometry applies to the API path (`dashboard-api` → router 8000)
and to any future co-located connector.

## Considered options

1. **Accept the latency.** Zero code. Warm traffic is healthy after PR
   #484; connection lifecycle stays on the ~150-330 ms-per-handshake loop.
2. **Move the browser/API edge onto the tailnet directly** (Tailscale serve,
   per ADR-0019). Rejected: breaks public browser access for non-tailnet
   devices, moves the edge authority off the Connect gateway, and
   duplicates the auth model.
3. **Loopback fast-path through the existing gate — one managed path
   (chosen).** Mount the existing tunnel gate on a dedicated local TLS
   entry point (its own trust root and entry identity), and let the
   deployment select that entry for co-located connectors. CF remains
   configured and is restored as a managed recovery action. No runtime
   session preference in this version.
4. **Dual-session automatic failover** (connector holds both CF and local
   sessions; gateway prefers local per connection). Deferred: the current
   implementation has no path-selection seam (dispatcher dials one declared
   reverse address; tunnel clients start per resource), and simultaneous
   registration of two clients on the same reverse ports has no defined
   ownership. Adopting this later requires a pinned-wstunnel experiment
   covering simultaneous registration, connection assignment, disconnect,
   and stale-listener behavior, plus explicit restriction/address mapping —
   it must be a separate ADR, not silent growth.
5. **Unix-socket tunnel** between gate and connector. Cleanest transport,
   but the pinned wstunnel and relay contract are TCP/WebSocket shaped.
   Deferred; supersede this ADR if adopted.
6. **Observatory asset bundling.** Orthogonal, cache-friendliness work; does
   not address the connection-lifecycle RTT. Independent, measured
   separately.

## Decision

Adopt option 3 — a single, explicitly selected local tunnel path through
the existing admission gate:

- **One entry, one gate.** The gateway mounts the existing tunnel gate on a
  dedicated loopback TLS entry point under a dedicated trust root, with its
  own entry leaf identity (`ServerName`, HTTP Host, and path defined
  separately from the dial address). Certificate verification is never
  disabled for the IP address. The local entry exposes only the tunnel
  admission surface; it is not a general ingress.
- **Managed selection.** A co-located connector's deployment may declare
  the local entry as its active tunnel path. `anvil-connect-ctl`
  preview/apply owns schema validation (a literal, approved loopback
  address and port only — DNS-derived, redirect, userinfo, and proxied
  targets are rejected), listener lifecycle, trust material, startup
  ordering, and rollback to the CF-only selection. There is exactly one
  active path at runtime; switching paths is a managed apply, not an
  automatic runtime decision.
- **Authority is global.** Both entries serve the same installation and
  resource authority: same epoch, generation, key binding, and lease rules.
  Revocation or lease expiry invalidates the local path exactly as it does
  the public path. The 250 ms Active rechecks, per-request peer
  verification, and PR #484's DER-based retirement apply unchanged.
  Aggregate admission (`MaxConcurrent`, Active) and connection budgets do
  not double — there is one gate and one set of limits. **Loopback
  reachability is never authorization**: loss of CF control-plane renewal
  still expires local access under the existing lease policy. The
  fast-path must not become an offline authorization extension.
- **Recovery contract.** Runtime failure of the local entry fails requests
  fast (no replay, no stream migration — recovery is a managed action that
  re-selects CF and re-applies). `anvil-connect-ctl` must prove, in
  evidence: partial-apply failure, either listener dying, stale reverse
  listeners, gateway restart, connector restart, rotation, and restoration
  to CF-only operation. A failed optional leg must not terminate a healthy
  leg through shared supervision; repeated apply converges without
  unnecessary restarts; rollback restores the previous managed
  configuration without restoring revoked identity state.
- **Scope boundaries.** Public browser ingress stays on Cloudflare plus
  Authelia. Remote connectors (ai-mbp25, mid-mod) stay CF-only.
  Deployment-specific identities and endpoints stay in private operator
  configuration.

## Expected effects

Baseline measurements (2026-09-11, fakoli-dark): direct gate/envelope
handshake ~20 ms; CF-loop handshake ~150-330 ms serialized. The fast-path
targets bringing connection-lifecycle handshakes to the ~20 ms class —
**projected, not yet measured**; acceptance must demonstrate the delta.
Warm steady-state traffic is unchanged by this ADR (PR #484 already
multiplexes it). Acceptance criteria: cold/warm burst latency, handshake
counts, errors, retirement behavior, restart/rotation recovery, path
failure, and POST execution counts on the selected path, plus revocation
tests proving the local path dies with authority.

## Consequences

- The gate gains a second mount surface (loopback TLS entry with dedicated
  trust root and identity) — the main new code surface. Review focus:
  entry identity, exposure (admission surface only), and no weakening of
  admission or revocation.
- `anvil-connect-ctl` gains local-entry schema validation and lifecycle
  ownership with the evidence gates listed above.
- Connector deployment gains an explicit active-path declaration; remote
  connectors are unchanged.
- Dual-session automatic failover (option 4) stays explicitly deferred
  behind a pinned-wstunnel experiment and a superseding ADR.
- Asset bundling (option 6) remains independent future work.
- Extending the fast-path beyond a literal loopback entry (option 5)
  supersedes this ADR rather than editing its decision.