# ADR-0043 implementation brief (context pack for the lead model)

## What is being built

The colocated tunnel fast path from `docs/adr/0043-colocated-tunnel-fast-path.md`,
scope v1 exactly as the ADR's "Start scope" states:

- ONE managed local path: a dedicated loopback TLS entry through the existing
  tunnel gate, with its own trust root, so the connector reaches the gateway's
  tunnel admission without traversing the public Cloudflare WSS leg.
- Remote connectors stay Cloudflare-only. The public path remains the fallback
  and the only remote route.
- The ADR's Consequences section lists the evidence gates that must pass
  before this counts as done. Read them as acceptance criteria.

## Why now (operational evidence from 2026-09-11)

The public WSS leg failed silently during heavy enablement work, and the
failure mode motivated this ADR's priority:

- The wstunnel server (the gateway's ingress, :17081) stalled NEW handshakes
  silently: clients TCP-connected, presented valid fresh credentials, and
  timed out with zero log output on either side. One pre-existing session
  kept serving, so observatory/pi-web kept working while every new tunnel
  establishment hung.
- The connector's establishment and renewal failures produce no distinct,
  loud events today (the observability gap is ticketed).
- A loopback diagnostic replica client was used to prove the stall existed
  with Cloudflare fully out of the path. The harness and its credentials
  discipline are documented in the ops-private runbook.

## Code map (where the work lands)

- `connect/internal/tunnelgate/gate.go` — the tunnel admission surface the
  local entry must reuse (restrictions, leases, admission). Do not fork this
  logic; the local path goes THROUGH the same gate.
- `connect/internal/relay/` — the relay IO and peer handling the fast path
  reuses.
- `connect/internal/transport/` — the wstunnel client/server wrapping; the
  local entry is a second transport binding, not a new transport.
- `connect/internal/config/config.go` — the manifest schema: the gateway's
  tunnel listen, the connector's tunnel_host. The v1 change needs a way to
  declare the local gate endpoint (e.g. a gateway-side loopback TLS listen +
  trust root, and a connector-side local endpoint preference) while keeping
  remote connectors CF-only.
- `anvil_serving/connect/manage.py` + `render.py` — the render/activate
  plumbing: the new rendered config fields, the unit/secret handling for the
  loopback TLS entry, and the manifest validation (closed, cross-section).
- `anvil_serving/connect/cli.py` + `commands/connect.py` — no new verbs
  expected; render/up/status surface the local path state.
- `docs/adr/0043-colocated-tunnel-fast-path.md` — the ADR itself; keep its
  scope v1 lines as the boundary. Do not widen scope.

## Constraints (house rules)

- Stdlib-only Python in `anvil_serving/`; Go additions stay inside the
  existing module set.
- The manifest stays a closed, strictly validated declaration; every new
  field must be rendered into exactly one consumer and validated cross-section.
- The loopback TLS entry uses its own trust root issued from the operator PKI
  (the edge cert rotation runbook already retains a fresh root key on host);
  the public edge continues to use the Cloudflare-facing PKI.
- No silent fallbacks: if the local path is declared, the connector dials it;
  the CF path remains for the undeclared/remote case. Connection-state and
  renewal failures must log as distinct loud events (the observability gap is
  adjacent, not in scope, but the local path must not add new silent modes).
- Every change rides the existing reversible transaction (render → review →
  activate) and the service-isolation contracts.

## Deliverable requested

A behavior-first implementation PRD: the manifest schema delta, the Go
surface delta (gateway listener + connector dialer selection), the Python
render/activate delta, the migration/back-compat story for the existing
deployment (the current production manifest must keep rendering and
activating unchanged), the evidence-gate checklist mapped to the ADR's
Consequences, and the test plan (Go contracts + Python manage tests + one
live qualification probe). Keep scope v1; list explicitly anything you are
pushing out of scope.