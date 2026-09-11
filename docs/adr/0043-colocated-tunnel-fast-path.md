# ADR-0043 — Co-located connector tunnel fast-path over loopback

- **Status:** Proposed
- **Date:** 2026-09-11
- **Relates to:** ADR-0019 (tailnet edge ownership), PR #484 (dispatcher
  connection reuse), `.tickets/2026-09-11-observatory-502-dispatcher-connection-reuse.md`,
  `docs/ANVIL-CONNECT-TRANSPORT.md`

## Context

Anvil Connect's browser and API resources reach their origins through a
reverse tunnel. The gateway (fakoli-dark) is the `wstunnel` server
(`127.0.0.1:17081`); the connector dials it as a client over
`wss://connect-tunnel.sekoudoumbouya.app`. Because that hostname resolves
through Cloudflare and terminates at cloudflared **on the same host**, every
inner request round-trips the loop:

```
gateway → CF edge → cloudflared → connector → origin
```

Measured on the dashboard path (2026-09-11): the tunnel adds ~150-330 ms per
serialized connection setup. PR #484 removed the per-request handshake cost
for steady-state traffic (pooled h2, 2-connection budget), so a warm page
load multiplexes over one connection. What remains is the **connection
lifecycle cost**: every new inner TLS connection pays the CF-loop round-trip
several times (tunnel dial, inner mTLS handshake), cold bursts serialize at
~330 ms per connection, and pool retires (idle timeout, connector restart,
leaf rotation) re-observe the latency. The connector and gateway are
co-located on fakoli-dark, so the traffic between them never needs to leave
the machine — but today it does.

The same geometry applies to the API path (`dashboard-api` → router 8000)
and will apply to any future co-located connector.

## Considered options

1. **Accept the latency.** The browser path is healthy after PR #484. Keeps
   zero code; cold bursts and reconnects stay ~150-330 ms per connection.
2. **Move the browser/API edge onto the tailnet directly** (Tailscale serve,
   as in ADR-0019). Rejected: breaks public browser access for non-tailnet
   devices (the primary Observatory consumer path), moves the edge authority
   off the Connect gateway, and duplicates the auth model.
3. **Co-located loopback fast-path (chosen).** A co-located connector
   additionally dials a second tunnel session over loopback
   (`wss://127.0.0.1:<gateway tunnel port>`), reusing the same mTLS
   envelope, restrictions, and identity model. The gateway prefers the
   local session for forwarding and keeps the CF session as failover.
   Remote (non-co-located) connectors keep the CF-only path unchanged.
4. **Unix-socket tunnel** between gateway and connector. Cleanest transport
   but wstunnel's pinned binary and the relay contract are TCP/WebSocket
   shaped; deferring socket transports keeps this change small.
5. **Observatory asset bundling** (reduce request counts). Orthogonal; still
   worth doing later for cache-friendliness, but it does not address the
   tunnel RTT that every inner request pays.

## Decision

Adopt the co-located loopback fast-path (option 3):

- The connector's deployment may declare a loopback tunnel target
  (`wss://127.0.0.1:17081`) alongside the public `tunnel_host`. The loopback
  session uses the identical tunnel protocol, mTLS envelope, restriction
  rules, and admission headers; nothing about identity, revocation, or the
  Active recheck changes.
- The gateway's tunnel bridge selects the loopback session for forwarding
  when one is live and falls back to the public session otherwise. Both
  sessions stay established; failover is per-connection, not a mode switch.
- Only co-located connectors (same-host gateway and connector) declare the
  fast-path; remote connectors (ai-mbp25, mid-mod) continue CF-only. The
  declaration stays explicit in `deployment.json`; no auto-discovery.
- Cloudflare and cloudflared remain the public ingress and the connector
  control plane; the fast-path only moves the **tunnel data path** off the
  CF loop.

Expected effects (measured baseline from 2026-09-11): inner connection setup
drops from ~150-330 ms to ~1-3 ms; cold bursts stop serializing on CF
round-trips; pool retires (idle timeout every 30 s of inactivity, connector
restarts, rotation) stop re-observing multi-second tails. Steady-state
behavior from PR #484 is unchanged.

## Consequences

- Gateway session management gains multi-session preference/failover logic
  (select loopback when live, public otherwise) — the largest new code
  surface, and the main review focus. It must preserve the existing
  per-session admission verification and restriction checks.
- Two live tunnel sessions per connector instead of one; connector restart
  re-bridges both. Restart/reconnect evidence tests must cover both legs.
- The loopback dial must not bypass the tunnel authority: the connector
  still presents its installation identity; the gateway still verifies the
  peer against `ConnectorPeer(id)` on every session, and restrictions still
  apply per remote address (`127.0.0.1:17001/17002` unchanged).
- Latency-class tests (cold burst, failover, retirement) gain a
  loopback-vs-CF comparison harness in `connect/lab/`.
- Asset bundling (option 5) remains future work for cache-friendliness, not
  latency.
- If the fast-path is ever extended to non-loopback transports (option 4),
  supersede this ADR rather than editing its decision.