# ADR-0043 implementation PRD — colocated tunnel fast path, v1

Status: implementation specification; qualification pending.  
Date: 2026-09-12.  
Authority: `.tickets/adr-0043-implementation-brief.md` and `docs/adr/0043-colocated-tunnel-fast-path.md`.

This document specifies required behavior and evidence. It does not claim implementation or live acceptance.

## Outcome and scope

An operator can explicitly move a colocated connector’s outer tunnel from the public Cloudflare WSS route to one managed loopback TLS entry. Connection establishment and reconnection avoid the public-edge round trip while preserving the existing tunnel gate, restricted wstunnel backend, authenticated inner transport, and origin envelope.

Each connector selects exactly one path for all its resource clients. Remote connectors remain public-only. Removing the local selection and applying the managed generation restores public operation. There is no runtime fallback, second registration, request replay, or stream migration.

Public browser ingress, Cloudflare and Authelia authentication, public control renewal, resource rules, and installation authority remain in place. Loopback reachability grants no access and extends no lease.

## Verified implementation baseline

The brief’s schema map needs a correction: deployment settings belong to runtime structs in `connect/internal/runtime/config.go`, rather than the policy structs in `connect/internal/config/config.go`.

| Existing deployment field | Verified Go consumer |
|---|---|
| `schema: anvil-connect.deployment/v1` | Python deployment reader; no equivalent Go deployment struct |
| `gateway.schema: anvil-connect.gateway-runtime/v1` | `runtime.GatewayConfig` |
| `gateway.gateway` | Embedded `config.Gateway`, schema `anvil-connect.gateway/v1` |
| `gateway.gateway.listen` | `config.Gateway.Listen` |
| `gateway.tunnel_listen` | `GatewayConfig.TunnelListen`: private wstunnel backend address |
| `gateway.tunnel_host` | `GatewayConfig.TunnelHost`: public tunnel identity |
| `gateway.control_host` | `GatewayConfig.ControlHost`: public control identity |
| `connectors[].schema: anvil-connect.connector-runtime/v1` | `runtime.ConnectorConfig` |
| `connectors[].tunnel_host` | `ConnectorConfig.TunnelHost` |
| `connectors[].public_trust_file` | `ConnectorConfig.PublicTrustFile` |
| `connectors[].http_proxy_url` | `ConnectorConfig.HTTPProxyURL` |
| `gateway.gateway.resources[].tunnel_address` | `config.Resource.TunnelAddress`: dispatcher reverse destination |
| `connectors[].resources[].reverse_address` | `runtime.ConnectorResource.ReverseAddress` |
| `connectors[].resources[].envelope.listen` | `config.Envelope.Listen` |
| `connectors[].resources[].envelope.origin_url` | `config.Envelope.OriginURL` |

Neither `config.Gateway` nor `config.Connector` currently declares tunnel endpoint selection.

`runtime.StartGateway` constructs one `tunnelgate.Gate`, lease authority, restricted wstunnel server, and dispatcher. Public ingress dispatches the public tunnel Host to that gate. `upgradeHead` requires HTTP/1.1 GET `/acv1/events`, exact Host, a valid WebSocket head, and the authorized reverse descriptor.

`runtime.StartConnector` starts one `transport.StartClient` per resource with:

- `ViaGate: true`
- `ServerURL: "wss://" + declaration.TunnelHost`
- `TrustFile: declaration.PublicTrustFile`
- `ProxyURL: declaration.HTTPProxyURL`
- Fixed reverse and origin addresses and rotating authorization headers.

`transport.ClientOptions` has no separate TLS ServerName or HTTP Host today. The transport pin in `connect/transport.lock.json` is wstunnel 10.7.1.

Python’s `validate_manifest` uses exact allowed-key sets and cross-section validation. `render._render` emits runtime objects directly into `gateway.json` and `connectors/<id>.json`.

Two existing lifecycle gaps are relevant:

- `_up_selected` restarts active selected units even when the generation is current.
- Gateway listener errors enter shared fatal supervision. `_gateway_ready` checks the admin socket, while `manage.status` reports unit metadata and drift without proving tunnel readiness.

These behaviors require changes to satisfy the ADR.

## Required behavior

1. **Omission preserves existing deployments.** With local declarations absent, normalization, rendered bytes, and generation identity remain unchanged. Supported isolated deployments activate without new configuration, credentials, or enrollment.
2. **Selection is explicit.** Declaring a connector’s local endpoint makes every resource tunnel dial that literal loopback endpoint exclusively.
3. **The entry is narrow.** The dedicated TLS listener exposes only tunnel admission. It cannot serve browser, API, control, OIDC, admin, or general proxy requests.
4. **Authority and limits remain shared.** Both entries use the same gate, leases, restrictions, backend credentials, and admission accounting.
5. **Failure is bounded and visible.** Local failure produces an explicit degraded state and bounded request failure without establishing a public replacement session.
6. **Recovery is managed.** Preview identifies affected services and path changes. Apply validates readiness before committing and restores the previous managed configuration on failure.
7. **Repeated apply converges.** An unchanged, healthy deployment does not restart unnecessarily. A failed required listener cannot be mistaken for a successful no-op.

## Proposed schema delta

The following fields are **new proposals**, not currently accepted fields. Retain the existing v1 schema identifiers.

Add optional pointer fields with `json:"local_tunnel,omitempty"` to the runtime structs. Omission selects public operation. Reject null, partial, empty, unknown, duplicate, and incorrectly cased declarations.

| Proposed JSON field | Proposed Go field | Consumer |
|---|---|---|
| `gateway.local_tunnel` | `GatewayConfig.LocalTunnel *LocalTunnelListener` | Gateway runtime |
| `.listen` | `LocalTunnelListener.Listen string` | Local TLS bind address |
| `.server_name` | `LocalTunnelListener.ServerName string` | Dedicated leaf identity and accepted SNI |
| `.http_host` | `LocalTunnelListener.HTTPHost string` | Exact local admission Host |
| `.certificate_file` | `LocalTunnelListener.CertificateFile string` | Server leaf/chain reference |
| `.private_key_file` | `LocalTunnelListener.PrivateKeyFile string` | Protected matching server key reference |
| `.trust_file` | `LocalTunnelListener.TrustFile string` | Dedicated local CA reference for validating entry material |
| `connectors[].local_tunnel` | `ConnectorConfig.LocalTunnel *LocalTunnelEndpoint` | Selected connector runtime |
| `.address` | `LocalTunnelEndpoint.Address string` | Literal loopback dial address |
| `.server_name` | `LocalTunnelEndpoint.ServerName string` | TLS verification name and SNI |
| `.http_host` | `LocalTunnelEndpoint.HTTPHost string` | Exact HTTP Host |
| `.trust_file` | `LocalTunnelEndpoint.TrustFile string` | Local outer TLS trust reference |

The gateway object appears only in `gateway.json`; each connector object appears only in that connector’s JSON. Python validates and renders references, never credential values.

Do not add an enabled flag, priority list, arbitrary URL, path override, or active-path enum. Presence of the connector object is sufficient selection.

The path remains the existing `tunnelgate.UpgradePath`, `/acv1/events`. ServerName, HTTP Host, and dial address remain distinct concepts even when the operator chooses equal identity names.

### Validation requirements

- A selected connector requires a gateway local-listener declaration. Its address, ServerName, and HTTP Host must match the corresponding gateway declaration.
- Use `config.LoopbackAddress` and the matching Python grammar: canonical `127.0.0.1:<port>`, port 1–65535. Reject DNS-derived destinations, wildcard binds, non-loopback addresses, IPv6, ambiguous numeric forms, port zero, URLs, userinfo, queries, and fragments.
- Apply existing canonical DNS-host validation to ServerName and HTTP Host. Reject collisions with public control, tunnel, resource, IdP, and existing inner or backend service identities.
- Reject collisions with declared gateway/backend listeners, reverse addresses, connector envelopes and origins, client listeners, IdP, and edge listeners. Detect wildcard edge-port collisions rather than relying only on address-string equality.
- Treat native bind failure as authoritative for undeclared occupied ports. Never evict a foreign listener.
- Explicit selection asserts colocation. Activation must verify the owned gateway on the applying host and its matching local entry. Do not infer colocation from a DNS name or accept another host’s loopback implicitly.
- Keep public `tunnel_host`, `control_host`, `public_trust_file`, and existing public-host equality checks mandatory.
- Preserve `http_proxy_url` for public control and public recovery. Never pass it to local tunnel dialing. The local object accepts no proxy field.
- File references must be clean absolute paths outside rendered output. Validate ownership and readability under the consuming service identity.
- Native preflight and startup must verify certificate/key match, server EKU, validity, exact dedicated identity, and chain to the local root.
- Gateway and connector local trust must identify the same dedicated CA, even if their file paths differ. Different filenames alone do not establish separate trust.
- Reject reuse of public-edge, inner-transport, or private-backend trust. The operator PKI provisions the dedicated root and leaf. Neither daemon consumes a root private key.
- Permit the gateway listener to remain declared while all connectors use CF, supporting staged enablement and recovery.

Extend Python’s exact allowed-key sets and normalized output together with the Go structs and validators. Preserve closed decoding through `config.Decode`.

## Go surface changes

### Gateway listener and gate binding

Extend `runtime/config.go` and `runtime/gateway.go` to load verified entry material and bind one additional TCP4 TLS listener within the existing gateway service identity and limits.

Add a narrow entry-binding seam in `tunnelgate/gate.go`. Public and local handlers validate their respective expected Host before entering the same admission/proxy implementation on the same Gate instance. Do not make both entries accept both identities through a global Host allowlist.

The local listener must:

- Require TLS 1.3 and the declared entry SNI.
- Expose HTTP/1.1 tunnel admission only.
- Retain `/acv1/events` and all existing upgrade/descriptor validation.
- Avoid mounting the general ingress handler.
- Retain backend gate-only mTLS and per-resource credentials.

Reuse `Restrictions`, lease checks, and relay cancellation and upgrade handling. Update the existing Unix-only gate-mount comment to describe the new verified TLS mount.

Keep gate Active sizing and per-resource slots shared across entries. Preserve dispatcher `MaxConcurrent`, its two-connection ordinary h2 budget, separate upgrade transport, per-request peer verification, and DER-based retirement.

The gate’s derived transport capacity and the dispatcher’s application-request cap are different existing limits; neither may double.

### Connector selection and transport adapter

Resolve the selected outer endpoint once before creating resource clients.

Local mode preserves `ViaGate`, reverse and origin addresses, rotating authorization headers, and the private empty trust-directory contract. It changes only outer dial address, TLS identity, HTTP Host, and outer trust.

Extend `transport.ClientOptions` with a bounded local binding containing address, ServerName, and Host. Retain existing public options.

The current wrapper does not prove that the pinned binary supports this identity separation. Before acceptance, demonstrate against the exact wstunnel pin that the adapter:

- Dials the literal loopback address without DNS resolution.
- Independently supplies and verifies ServerName/SNI and HTTP Host.
- Retains certificate verification and dedicated trust.
- Refuses redirects and proxy-mediated local dialing.

Do not present unverified wstunnel flags as established APIs. If the pin cannot satisfy the contract, block acceptance and review the implementation approach. Do not silently substitute DNS, a proxy, insecure TLS, a new transport, or a different pin.

Control enrollment and renewal continue through the existing public client, public trust, and public proxy configuration. Persisted installation and invitation formats do not acquire local endpoint fields.

Same-path reconnect attempts remain bounded and never become cross-path failover.

### Failure containment and events

An optional entry failure must not enter shared fatal supervision and terminate a healthy sibling. Use entry-scoped cancellation and health tracking, including cancellation of its accepted upgraded sessions.

Preserve whole-runtime failure for genuinely shared authority or backend failure. If public ingress fails while local traffic continues, existing public-renewal lease expiry still applies.

Provide bounded, credential-free events distinguishing:

- Entry bind or TLS failure.
- Tunnel establishment failure and disconnect.
- Renewal failure and lease expiry.
- Managed recovery success or failure.

Events may contain path/resource identifiers and bounded reason codes. They must exclude raw upstream output, bearer headers, keys, and request bodies.

Child process existence is not establishment evidence. Broader observability redesign remains outside scope.

Use finite local dial, TLS, upgrade, and readiness deadlines. Qualification must show that each establishment attempt and a request stranded before origin dispatch terminates within five seconds plus recorded scheduling tolerance. Existing stream idle/duration and authority-expiry bounds remain applicable.

## Python render and activation changes

Extend `connect/config.py` validation and the existing direct runtime projection in `render.py`. A local-only delta must leave Caddy and Authelia output unchanged. Do not create another edge service.

Extend gateway and connector file validation and native preflight for local trust material:

- Python inspects file metadata.
- Native consumers securely open and validate material under their service UIDs.
- Leaf keys remain gateway-private.
- Connectors receive only public local trust roots.
- Existing isolated accounts, directory boundaries, capabilities, and resource limits remain intact.

Use existing `render`, `up --services`, `status`, `doctor`, and `logs` surfaces and their preview/confirmation policy. No new verb is required.

Preview must show selected paths, affected roles, trust references, and necessary restarts. Status must distinguish declared selection, observed listener/tunnel state, degraded or unknown state, and general unit activity.

### Transaction behavior

Extend readiness beyond `_gateway_ready`’s admin RPC:

1. Verify the configured local TLS entry before starting dependent connectors.
2. Verify admitted reverse-tunnel readiness before committing activation.
3. Do not equate TCP connectivity or a child PID with readiness.

Any new native status fields require matching closed Python decoding, including `_closed_gateway_status` if that response changes.

When gateway and connector configurations both change, require their explicit coordinated target set. Start and check the gateway before connectors.

For every path switch, terminate the old connector registration and verify release of owned reverse listeners before creating replacements. Reject stale or foreign listeners without killing arbitrary processes.

Boot ordering must also wait for the declared gateway without dependency propagation that unnecessarily kills a healthy sibling entry.

Preserve `_activate` ownership checks, locking and re-planning, binary verification, retained rollback artifacts, unit restoration, and delayed activation-record commit.

Change `_up_selected` so healthy unchanged services remain running. Restart only services whose required configuration/artifacts changed or whose required runtime state is unhealthy.

Use versioned trust-file references for rotation so the change appears in the reviewed generation and prior material remains available for rollback.

### CF recovery

Remove the connector local object and apply its selected role. The gateway listener may be removed in the same coordinated transaction or retained temporarily.

Stop local registrations before establishing public replacements.

Rollback restores configuration and service-running intent. It must not restore revoked installation state, epochs, generations, leases, or credential databases. If previous trust is no longer valid, report recovery failure and retain artifacts rather than reviving invalid authority.

## Compatibility and migration

Deploy compatible Python/native artifacts through the existing explicit upgrade transaction before enabling local declarations. Older closed readers must reject the new objects; mixed-version activation cannot bypass preflight.

An unchanged supported isolated manifest must retain normalized JSON, rendered bytes, ownership hashes, and generation identity. Do not insert local defaults when fields are omitted.

Legacy `service_user` declarations retain their existing inspection and migration behavior. This feature does not make them activatable.

Keep public host bindings and persisted installation state unchanged across path switches. Re-enrollment is unnecessary.

Use the current production manifest privately as a compatibility input. Do not publish its contents or claim production activation as part of document authoring.

## Evidence gates mapped to the ADR

Every row is required before marking implementation complete.

| ADR contract | Acceptance evidence |
|---|---|
| Dedicated second mount and identity | Correct local root/SNI/Host/path succeeds; wrong root/name/Host/path, plaintext and malformed upgrades fail |
| Admission-only exposure | Browser, API, control, OIDC, admin and arbitrary proxy traffic cannot enter through the local listener |
| Literal local destination | Instrumented tests prove no DNS, redirect or proxy egress from local selection |
| One gate and shared limits | Concurrent traffic through both entries shares aggregate and resource admission; inner connection budgets do not increase |
| Global authority | Installation/key/resource revocation and epoch/generation changes reject new sessions and terminate affected existing sessions on either entry |
| No offline extension | Block public renewal and measure existing connector 45-second and transport 60-second lease expiry, with 250 ms rechecks and explicit tolerance |
| Explicit selection and remote compatibility | Local-selected resources open no public tunnel session; undeclared remote resources remain CF-only; public control remains used |
| Lifecycle latency benefit | Paired CF/local cold and warm bursts record latency distributions, handshake counts, errors, retirement and origin executions |
| Failure isolation | Fail local entry while public serves, then public entry while local serves; healthy sibling survives subject to unchanged lease policy |
| Reversible lifecycle | Inject partial apply, bind conflict, TLS/readiness failure, stale reverse listeners, gateway/connector restart, leaf/root rotation and rollback failure |
| Recovery and convergence | Managed CF-only restoration works; repeated healthy apply preserves PIDs/start timestamps; rollback never revives revoked authority |
| No replay or stream migration | Origin request IDs prove successful POSTs execute once and every attempted POST at most once across faults; SSE/WebSocket termination remains explicit |

The ADR’s approximately 20 ms direct and 150–330 ms CF figures are historical inputs, not new measurements or an unconditional SLO. Require repeatable measured cold-lifecycle improvement toward the projected 20 ms class, with workload, sample counts and uncertainty recorded. Warm traffic must show no material regression.

## Test and delivery plan

1. **Go contracts:** Extend existing config/runtime, tunnelgate, transport and relay coverage. Include schema rejection, actual TLS verification, Host/SNI separation, shared admission, descriptor denial, authority expiry, DER retirement, environment/proxy isolation, deadlines and listener containment.
2. **Python contracts:** Extend `tests/connect/test_render.py`, `test_manage.py`, and `test_manage_isolation.py` for reader parity, omission byte stability, exact field consumption, target enforcement, trust ownership, startup order, readiness, convergence, rollback and CF recovery.
3. **Cross-language agreement:** Feed rendered JSON into the actual Go runtime readers. Python snapshots alone cannot prove schema compatibility.
4. **Focused checks:** Run:
   - From `connect/`: `go test ./internal/config ./internal/runtime ./internal/tunnelgate ./internal/transport ./internal/relay`
   - From repository root: `python scripts/run_tests.py tests/connect/ -x -q`
5. **One live qualification probe:** Extend the existing managed qualification harness with paired public/local/public phases using the pinned transport and independently counted origin requests. Cover cold/warm bursts, idle retirement, restarts, rotation, entry failure, stale registration, revocation and public-renewal loss. Use a dedicated qualification deployment for destructive faults.
6. **Recovery evidence:** The harness must restore its starting selection or explicitly report failed recovery.
7. **Artifacts and review:** Retain sanitized revisions, digests, workload, phase timings, handshake/POST counts, errors, expiry measurements and final recovery state. Keep real identities, endpoints and unsanitized logs private. Independent review and measured gates determine acceptance.

## Explicit exclusions

- Dual-session automatic failover or simultaneous registration of one resource on two paths.
- Runtime path preference, scoring or selection.
- Unix-socket transport or any non-loopback fast path.
- Remote connector acceleration.
- Silent wstunnel replacement or pin upgrade.
- Public ingress or authentication redesign.
- Offline authorization.
- Asset bundling.
- General observability redesign.
- New CLI verbs, unrelated cleanup, or model-serving changes.

---

