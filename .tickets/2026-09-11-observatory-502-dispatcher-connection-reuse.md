# 2026-09-11 — Observatory dashboard 502: dispatcher opened a fresh inner TLS connection per request

## Symptom

Browser page loads of `https://dash.example.test/observatory/` returned
502 on 5-8 of ~15-20 static assets per reload (HTML shell loaded, assets
failed). Persisted after the dashboard resource `concurrent` limit was raised
8→32 at both gateway and connector (private runbook checklist item: "Resolve
the remaining operations.js 502 … after the dashboard request limit increased
from 8 to 32").

## Root cause

`connect/internal/transport/transport.go` (`dispatch`) built the ordinary
h2 dispatcher transport with `DisableKeepAlives: true` and
`MaxConnsPerHost: resource.Rule.Limits.Concurrent`:

1. **A fresh inner TLS connection to the connector envelope per request.**
2. Every inner TLS handshake transits the reverse-tunnel loop
   (`gateway → wstunnel listener 17001 → wstunnel server → bridge → gateway
   wss server → ingress/caddy → cloudflared → CF edge → connector wss client
   → connector envelope`), measured serialized at ~330 ms per connection.
3. `TLSHandshakeTimeout: 5s`. A browser asset burst (~15-20 requests) queued
   the tail beyond 5 s → handshake timeout → ReverseProxy ErrorHandler → 502.

The 8→32 limit bump deepened the queue length without touching the
per-request handshake cost, which is why it did not help. The api resource
survived only because `concurrent: 8` rejected overflow with a prompt 429
before the doomed queue could build.

## Evidence (live, primary-node, 2026-09-11)

- Loopback capture on the dashboard tunnel leg during two reloads: 59
  connections completed full request/response; 20 sent ClientHello (1527 B)
  and received zero bytes before the gateway closed them — exactly the 502s.
- Go burst probe replicating the gateway dispatch transport through 17001
  (20 concurrent): 16 completed (403 = probe carried no browser assertion),
  4 failed at exactly 5.00 s `net/http: TLS handshake timeout`; per-request
  latencies ramped 0.07→4.87 s (~330 ms serialized per new TLS conn).
- Direct envelope handshake ~20 ms loopback-local vs ~330 ms through the loop.

## Fix (this change)

In the ordinary h2 dispatcher transport only:

- Connection reuse enabled (`DisableKeepAlives` removed); a cold burst now
  multiplexes over a bounded connection budget (`dispatchConnections = 2`)
  instead of dialing one serialized handshake per request.
- `upgradeTransport` (h1, WebSocket) explicitly re-pinned to
  `DisableKeepAlives: true` with its admission-sized `MaxConnsPerHost` —
  `Clone()` would otherwise have silently inherited reuse.
- Pooled connections are tracked per resource binding by connector leaf DER
  (`connRegistry`). When a pooled connection's certificate loses
  installation authority (revocation/rotation) — detected either at the
  per-request `GotConn` recheck or the 250 ms `access.Active` sweep — the
  offending connection is closed so the pool cannot re-offer it; proactive
  retirement also fires `NotAfter − lifetime/10` before certificate expiry,
  so a same-key renewal cannot leave a stale pooled connection serving.
- `TLSHandshakeTimeout`, the 250 ms Active recheck, per-request
  `GotConn`/`VerifyPeer` re-verification, resource admission, and the
  envelope-side checks are unchanged.

The connector→origin transport (`connect/internal/origin/proxy.go`) has the
same per-request shape but is loopback-local (~ms), so it is intentionally
unchanged; eliminating the CF loop for the connector tunnel is a separate
architecture topic.

## Verification gates

- `connect/internal/transport/burst_test.go` (new, all in-package):
  - `TestColdBurstMultiplexesOverBoundedConnections` — 20-request cold burst
    over a delayed (300 ms/conn serialized) tunnel: every request 200, ≤3
    TLS handshakes, no 5 s breaches; warm burst dials nothing.
  - `TestSharedConnectionRetiresOnAuthorityRevocation` — revoked authority:
    prompt 502/503, offending connection closed and untracked; restore: fresh
    dial succeeds and re-pools.
  - `TestStreamCancellationIsolatesSiblings` — a cancelled slow request does
    not discard the shared connection.
  - `TestUpgradeTransportKeepsFreshConnections` — upgrade transport pins.
  - `TestProactiveCertificateExpiryRetiresPooledConnection` — synthetic
    short-lived leaf retires at `NotAfter − lifetime/10`.
  - Mutation check: restoring the old transport literal makes the burst
    tests fail; the fixed code passes.
- `go test -race -count=1 ./...` in `connect/` (with
  `ANVIL_CONNECT_WSTUNNEL=/opt/anvil-connect/releases/.../bin/wstunnel`): all
  packages pass, including the pinned-wstunnel lab suite.
- Python `scripts/run_tests.py tests/ -x -q`: pass (connect change is Go-only;
  suite run per repo verification policy).

## Adversarial review (fresh-context reviewer, addressed)

1. **bind() timer churn on h2 reuse (major)** — fixed: re-bind takes a fast
   path when the connection's leaf is unchanged and its retirement timer is
   already scheduled; no per-request timer allocation.
2. **POST landing on a just-died idle pooled connection (major)** — accepted
   residual, documented here: a non-idempotent request handed a pooled conn
   that died between requests returns 502 without a retry (no GetBody on the
   streamed proxy body). The previous per-request-dial design failed the
   same request identically when the tunnel was down (dial failure). Browser
   asset traffic is GET (auto-retried by the transport). Mitigation (body
   buffering) would contradict the streaming design that requires h2; left
   as-is deliberately.
3. **2-conn budget starvation (major)** — not reachable: h2 multiplexes
   concurrent streams per connection; a slow response does not occupy the
   conn. Regression test added
   (`TestSlowStreamMultiplexesSiblings`) proving 8 sibling requests ride the
   pooled connection with zero new dials while a slow stream is in flight.
4. **Dead certificateMargin const (minor)** — fixed: margin is
   `min(lifetime/10, maxCertificateMargin=10min)`; the constant now names the
   cap.
5. **Idle-conn retirement is lazy (minor)** — correct and accepted: the 250ms
   sweep only runs while a request is active; an idle conn with a revoked
   leaf is retired on the next request's GotConn recheck (one-time 502, no
   stale success) or its own expiry timer. No code change.
6. **Microsecond post-verify-failure write window (minor)** — accepted
   residual: cancel() is asynchronous; the connector additionally enforces
   its own revocation checks envelope-side.
7. **Wall-clock burst assertion (minor)** — kept (codes==200 is the primary
   gate; the 4s wall-clock bound has >4x headroom over the ~0.9s worst case).

## Residual risks (documented)

- POST over a pooled connection that died between requests: 502 without
  retry (equivalent to the old dead-tunnel dial failure; see review item 2).
- Idle pooled connections with revoked certificates retire lazily (one-time
  502 on next use, never stale success; expiry timer self-cleans).
- Microsecond write window after a failed authority recheck (see item 6).

## Deployment notes (release-readiness flow, after merge)

- Gateway and connector are affected by the same dispatcher build; deploy
  gateway first or together — old connector + new gateway and vice versa are
  protocol-compatible (transport shape change only affects gateway→envelope
  behavior on the gateway side; the connector envelope already speaks h2).
- Evidence burst behavior on the live dashboard after deploy: page loads must
  show zero 502s; inner handshake count per page load drops to ~1-2 (verifiable
  by the connector tunnel traffic or gateway logs).
- Resource `concurrent` limits stay at dashboard 32 / api 8 (Asta: do not
  auto-revert to 8; admission is separate from the connection budget).
- Rollback: revert this commit and redeploy; no state migration.