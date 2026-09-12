# ADR-0043 Slice 2 — Go implementation summary

Date: 2026-09-12. Baseline: `e406a110`.

**Status: Go consumer implementation and contract tests added; acceptance blocked.**
The exact transport binary was not executed. Registration readiness and the full
five-second establishment/pre-origin contract are not proved or fully implemented.
This is not a deployment, promotion, or completed ADR qualification.

## Implemented

- Extended consumers of Slice 1's existing `local_tunnel` objects without changing
  their schema, closed-reader validation, invitation, or installation formats.
- Added verified local leaf/key/root loading under the consuming process identity:
  bounded regular-file reads, protected private key, symlink rejection, matching
  key, current validity, server EKU, exact single DNS identity, dedicated self-signed
  CA, and inner/backend-root key separation. Connector loading rejects public and
  inner trust reuse by material, not filename. `VerifyLocalTunnelTrust` compares
  two role declarations' actual root certificates and rejects public-root reuse;
  activation integration of this native helper belongs to Slice 3.
- Added the TCP4 local TLS mount to the gateway, with TLS 1.3 only, required declared
  SNI, HTTP/1.1 only, and the gate's admission handler exclusively. General ingress
  is never mounted there. `Gate.Bind` checks its own expected Host before entering
  the same admission/proxy code as the public handler. Path, descriptor, upgrade,
  lease, gate-only backend mTLS, restrictions and per-resource credentials remain.
- Retained one Gate, shared Active/route slots and the existing derived transport
  capacity. Local sockets are additionally capped before TLS at that existing
  derived capacity. Dispatcher application admission, two ordinary h2 connections,
  separate upgrade transport, per-request peer verification and DER retirement
  remain unchanged in size. A five-second timer now bounds inner connection
  acquisition (pool wait plus dial/TLS), ending at verified `GotConn`.
- Resolved connector outer selection once before resource clients. Local selection
  changes the literal outer URL/address, SNI, HTTP Host and trust; it clears only
  the tunnel proxy. Public control enrollment/renewal retain their existing public
  client, trust and proxy. ViaGate, reverse/origin addresses, rotating authorization
  files, verified child inode, private empty trust directory and isolated child
  environment remain. Local rotating files must contain exactly one bounded
  authorization line: a headers-file Host cannot override the bound CLI Host.
  Every managed replacement uses the control client's validated token. No
  alternate path or new transport/pin was introduced.
- Added entry-specific cancellation and native listener-health snapshots. Losing an
  entry cancels its hijacked sessions while a healthy sibling survives. Losing the
  last entry fails the runtime; shared authority/backend supervision remains fatal.
- Added bounded credential-free event snapshots (64 records) and daemon log codes,
  rate limited to one per path/reason per second. Codes distinguish listener bind,
  TLS and capacity failures, verified backend upgrade establishment, establishment
  failure/disconnect, renewal failure/resumption, authority denial and lease expiry.
  Expiry observation does not require traffic. No raw child/TLS error output,
  bearer headers, keys or request bodies enter these events.

Go-owned establishment bounds are: three seconds total for the connector's TLS
entry verification; one second for the local server TLS handshake and one for its
HTTP head; three seconds total for local gate backend dial/TLS/validated 101; and
five seconds total for dispatcher connection acquisition. Established streams keep
the existing idle/duration and authority checks. These individual bounds are not
claimed to establish the end-to-end five-second requirement.

## Exact wstunnel pin status

The lock remains **wstunnel 10.7.1**. The tagged source commit inspected was
`d0f1e1ac3f340653a856e7aad66b886c59bf6e8e`.

Source inspection supports the adapter spelling and identity separation:
[`config.rs`](https://github.com/erebe/wstunnel/blob/v10.7.1/wstunnel/src/config.rs),
[`lib.rs`](https://github.com/erebe/wstunnel/blob/v10.7.1/wstunnel/src/lib.rs),
[`client/config.rs`](https://github.com/erebe/wstunnel/blob/v10.7.1/wstunnel/src/tunnel/client/config.rs),
and [`websocket.rs`](https://github.com/erebe/wstunnel/blob/v10.7.1/wstunnel/src/tunnel/transport/websocket.rs).
The contract test asserts the complete local argv, including
`--tls-sni-override`, `--http-headers` with the separate Host, certificate verification,
the literal loopback WSS URL, unchanged reverse descriptor and rotating headers,
and exact dedicated trust/empty-directory environment. These are source-backed
adapter contracts, **not verified behavior of the release binary**.

Live qualification against the digest-verified binary must still demonstrate:

1. Literal loopback dialing without DNS resolution or DNS-based substitution.
2. Independent verified ServerName/SNI and HTTP Host, including negative cases.
3. Dedicated trust verification with the empty trust-directory environment.
4. Redirect refusal and absence of explicit or inherited proxy-mediated local dialing.
5. Admitted reverse-registration readiness, establishment/disconnect evidence,
   finite reconnect attempts and the required five-second bound under injected faults.

## Blocked items and PRD pushbacks

- **Child readiness/deadlines:** the pin has a hard-coded ten-second connect setting,
  a WebSocket handshake without its own enclosing timeout, and a continuously
  retrying reverse-registration loop with no readiness IPC. The existing one-second
  pool/retry settings are not a demonstrated registration deadline. Native TLS
  verification is only entry verification; a returned Connector handle owns started
  processes and is not readiness evidence. Gate-observed valid backend 101 is also
  not proof that the backend completed reverse-listener registration. Acceptance
  requires resolving this readiness/deadline contract, not merely running a happy
  path. No guessed timeout/readiness flags or substitute binary were introduced.
- **Pre-origin deadline:** the new dispatcher timer stops at verified `GotConn`.
  It does not bound a later stall before the origin handler runs. The acquisition
  test is explicitly scoped to that boundary. Proving/enforcing the stronger bound
  requires an independently observable dispatch/readiness boundary; an arbitrary
  five-second response timeout would change valid long-running application behavior.
- **Managed recovery events:** renewal resumption is labeled `renewal_resumed`, not
  managed recovery. Success/failure events for configuration rollback and public-path
  restoration require the Slice 3 activation transaction, excluded from this work.
- **Activation/status:** native trust-comparison and health methods are not added to
  the closed admin wire response. Slice 3 must wire colocation, cross-role preflight,
  admitted-registration readiness, status, startup ordering and recovery into the
  existing managed activation surfaces. A listening entry or admin socket alone
  must not commit activation.
- **Sandbox:** native bind conflicts and the full TCP-based regression suite still
  need an environment that permits sockets. No live serving or model changes were made.

Independent Sol review recommended **HOLD** for the readiness and deadline gaps.
Review findings about last-listener failure, event visibility, misleading renewal
recovery labels and pre-TLS socket capacity were addressed. The acquisition test
was narrowed to its actual evidence boundary rather than advertised as origin proof.
Final review also confirmed fixes for headers-file Host override and empty public
trust material; their regression tests pass.

## Verification

All commands below ran from `connect/` with `GOCACHE=/tmp/adr0043-go-cache` because
this sandbox cannot write the configured shared Go cache.

| Check | Result |
| --- | --- |
| `go test ./internal/config ./internal/runtime ./internal/tunnelgate ./internal/transport ./internal/relay` | **Blocked/failing:** config and tunnelgate pass; existing runtime, transport and relay tests cannot create TCP listeners (`socket: operation not permitted`). No full-suite pass claimed. |
| `go test -race ./internal/runtime ./internal/tunnelgate ./internal/transport ./internal/relay -run '^(TestLocal\|TestEntry)'` | **Pass** for the new contracts and existing Slice 1 local schema tests. Native bind-conflict test explicitly skips when the sandbox denies sockets. |
| `go vet ./internal/config ./internal/runtime ./internal/tunnelgate ./internal/transport ./internal/relay ./internal/testpki` | **Pass.** |
| `go test ./internal/config ./internal/runtime ./internal/tunnelgate ./internal/transport ./internal/relay -run '^$'` | **Pass:** all requested packages and their tests compile; no tests executed by this check. |
| `gofmt -l internal/runtime internal/tunnelgate internal/transport internal/relay internal/testpki` | **Clean**, no output. |
| `git diff --check` | **Pass.** |

New in-memory tests exercise real TLS and backend mTLS, SNI/Host separation,
admission-only requests, descriptor denial, shared admission, revocation/expiry
cancellation on both entries, individual and last-entry failure, TLS material
rejection, root comparison, pre-TLS socket saturation/release, exact local argv,
proxy/environment isolation, empty trust-directory rejection, acquisition/admission
deadlines, same-key/different-DER retirement, relay teardown, and bounded events.
`testpki.PipeListener` supplies actual TLS/HTTP byte streams without OS sockets;
it supplies no DNS, TCP-bind or binary qualification evidence.

Working logs are retained outside Git in `/tmp/adr0043-slice2-go-test.log` and
`/tmp/adr0043-slice2-race.log`. Python, CLI verbs, activation/rendering, transport
pin, persisted formats and model-serving surfaces were not changed.
