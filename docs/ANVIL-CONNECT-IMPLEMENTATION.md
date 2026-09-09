# Anvil Connect implementation evidence

Delivery record dated 2026-09-09. The implementation and isolated qualification
passed independent review, and the owner accepted all 23 Anvil tasks with strict
evidence checks on 2026-09-09 and authorized the source merge. This record
does not authorize or claim a public deployment, DNS change, VPN replacement,
router restart, or model-serving change.

## Requirement coverage

| Requirement | Implemented boundary | Evidence surface |
|---|---|---|
| R001: qualify existing transport | Locked wstunnel 10.7.1, verified WSS, explicit CONNECT proxy | `connect/lab/transport_test.go`; [transport finding](ANVIL-CONNECT-TRANSPORT.md) |
| R002: closed resource declarations | Exact host/path/method, connector, limits, local envelope; Python/native parity | `connect/internal/config/`; `tests/connect/test_render.py` |
| R003: ordinary SDK key | Hashed expiring grants, exact resource/method access, stripped caller key and origin token delegation | `connect/internal/access/`; `connect/lab/api_test.go`; `connect/lab/runtime_test.go` |
| R004: managed human login | Strict OIDC, PKCE/state/nonce, response issuer, opaque browser sessions | `connect/internal/session/`; browser fixture and real-edge qualification below |
| R005: native security | Separate native credentials, cookies, CSRF and action grants | `connect/internal/httpedge/browser_test.go`; `tests/connect/test_migration.py`; Observatory suite |
| R006: unique installation | Local key generation, bounded invitations, fingerprint approval, signed proof | `connect/internal/identity/`; `connect/internal/control/`; runtime connector tests |
| R007: revocation/expiry | Epoch/generation checks, active cancellation, renewable origin leases | Access/session/identity/credential/tunnelgate tests; API/browser stream fixtures |
| R008: streaming and no replay | Incremental SSE, classic WebSockets, deadlines and bounded admission | `connect/lab/api_test.go`; transport/relay/httpedge tests |
| R009: product management | Closed renderer, pinned component validators, CLI, owned lifecycle, offline recovery | `tests/connect/`; native CLI/runtime tests; installed-wheel CLI test |
| R010: origin isolation | Fixed destination and independently declared local envelope | `connect/internal/origin/`; identity/control/tunnel restriction tests |
| R011: browser origin | One canonical host, native cookie/redirect confinement, gated upgrades | HTTP-edge/browser tests; migration preview and app-side route-switch fixture |
| R012: ownership/privacy | Generic templates; private state and secret references; unchanged Tailscale command metadata | Renderer tests; CLI edge-manifest regression; source review |
| R013: failure/recovery evidence | Partial-result classification, private-file checks, safe restore, independent review | Store/runtime recovery tests; manager failure tests; review record below |

The paths identify reproducible evidence, not a claim that every optional test
ran in every invocation. Required environment-dependent commands must run with
the declared pins to count as transport or edge qualification.

## Recorded qualification

| Surface | Observed result | Practical limit |
|---|---|---|
| Initial transport experiment | Real pinned processes passed mTLS, proxy, streaming, restrictions and cancellation | Loopback and explicit opaque-TLS proxy; not an arbitrary corporate network |
| Complete Go test command | All 20 packages with tests passed, including the real pinned transport lab | Browser fixtures are activated separately by Playwright; platform/privilege-dependent negatives retain their explicit skips |
| Native runtime API path | Real gateway/connector lifecycle and pinned tunnel passed one-key API access and owned cleanup | Synthetic TLS front and origin; not Caddy/Authelia |
| Browser adapter | Four Chromium tests passed session/cookie/native-CSRF/upgrade/logout controls with a synthetic issuer | Adapter evidence; actual-edge qualification is recorded separately |
| Actual identity edge | Pinned Caddy/Authelia plus strict Chromium trust passed password/TOTP/consent, exact issuer/subject grants, native CSRF/cookie controls and logout cancellation | Manually assembled adapters with synthetic installation PKI |
| Assembled browser runtime | Gateway initialization/start, native admin invitation/enrollment/approval, connector start, real WSS tunnel and browser access passed; two occupied stream slots yielded 429; logout 303 closed both streams within 2 seconds, then access returned 401 | Gateway and connector runtimes share one test process/UID; Caddy, Authelia and wstunnel are separate verified children; no systemd or public deployment |
| Complete browser suite | All 6 Playwright tests passed; unknown CA negative and trusted CA positive; teardown requires clean exit and leaves no fixture children | Linux Chrome with private fixture DNS/trust/proxy; no arbitrary-network claim |
| Pinned edge configuration | Caddy 2.11.3 and Authelia 4.39.20 validators accepted generated declarations and rejected negative controls | Validation alone does not start services or prove TLS SAN coverage |
| Native artifact preflight | Real manager subprocess validated a connector using the built CLI and pinned wstunnel | Current non-root user; root-to-service-UID test is conditional |
| Recovery | Race-tested store/runtime/native CLI; historical keys/cookies and fresh logins using restored disabled grants denied | Connect authority only; Authelia/application backup is separate |
| CLI packaging | Installed wheel exposes Connect help and packaged pins without native discovery; Python runtime dependency set unchanged | Binaries are provisioned separately |
| Lifecycle | 31 manager tests passed, including supervisor failure/rollback, selected ownership and coordinated upgrades | Root-only UID-drop test skipped; actual systemd installation is not performed on the development host |
| Connect Python integration | 82 passed with actual pinned edge validators enabled; 1 root-only UID-drop skip | No host service changes; Windows/macOS runtime execution remains unsupported |
| CLI, documentation and Tailscale regression | 439 passed; 8 existing help-parser skips for commands without an argparse usage line | No live Tailscale mutation |
| Observatory migration | 664 Observatory/migration tests passed, including the real app-side fixed-origin route switch and rollback | External host, public TLS/DNS and cutover are not selected or qualified |

The actual Caddy/Authelia browser fixture exposed two integration assumptions:
the callback includes `iss` and `scope`, and the user subject is an opaque UUID.
The callback now validates issuer metadata and the test provisions exact subject
identifiers. The final browser suite passed with both corrections.

The assembled runtime fixture also exposed header token casing at the edge:
wstunnel sends lowercase `Connection: upgrade`, while the original plain Caddy
matcher required `Upgrade`. The renderer now selects the HTTP/1 path with
case-insensitive token patterns, and the Go gate still validates the handshake
and installation authority. This follows the pinned
[Caddy matcher implementation](https://github.com/caddyserver/caddy/blob/v2.11.3/modules/caddyhttp/matchers.go).

## Reproduce the gates

The Linux browser gate requires Chrome or Chromium, `certutil`, Node.js, Go,
the three verified component binaries, and the pinned npm dependencies. Set
`ANVIL_CONNECT_CHROMIUM`, `ANVIL_CONNECT_GO`, `ANVIL_CONNECT_EDGE_CADDY`,
`ANVIL_CONNECT_EDGE_AUTHELIA`, and `ANVIL_CONNECT_WSTUNNEL` to their explicit
locations when they differ from the lab defaults. Fixtures use their own NSS
trust database and first require an untrusted browser to reject the certificate;
they do not disable certificate validation or change the user's browser trust.

```sh
# Native unit/runtime checks. Set the exact qualified transport explicitly.
ANVIL_CONNECT_WSTUNNEL=/absolute/verified/wstunnel go -C connect test ./... -count=1

# Real component validators require the opt-in qualifier.
ANVIL_CONNECT_EDGE_QUALIFY=1 python scripts/run_tests.py tests/connect/ -x -q

# Browser tests use pinned Playwright; see their fixture environment requirements.
npm --prefix connect ci
npm --prefix connect run test:browser

# Existing Observatory authorization and migration invariants.
python scripts/run_tests.py tests/connect/test_migration.py tests/observability/ -x -q
```

Pinned artifacts and provenance live in
[`transport.lock.json`](https://github.com/fakoli/anvil-serving/blob/main/connect/transport.lock.json) and
[`edge-tools.json`](https://github.com/fakoli/anvil-serving/blob/main/connect/lab/edge-tools.json). Go and browser dependency
locks are separate. The Python package contains the byte-identical edge-tool
pin manifest for installed lifecycle verification.

The Linux CI job runs native internal/command contracts and Python Connect tests.
Its deliberately smaller environment does not qualify the tunnel or browser lab.
The general Python matrix retains help/schema collection on unsupported native
platforms, while actual Connect lifecycle operations return a typed platform
error before accessing a deployment manifest.

## Independent review and unresolved qualification

An independent reviewer approved the renderer, upstream-validator pins, CLI
packaging, native runtime/control/browser-adapter slices, lifecycle, coordinated
upgrades, recovery, migration preview and the final combined fixture after
corrections. Independent checks included the real runtime browser test, Go race
tests and pinned edge validators. No unresolved high-severity finding remains
in the reviewed scope. Owner acceptance was recorded through Anvil's formal
review gate; it does not establish public deployment qualification.

Review found and corrected issues at the boundaries: callback grammar,
installation fingerprint format, validator service identity, selected-role
secret prerequisites, foreign unit ownership, configuration rollback, executable
pin persistence, restored grants, backup namespace completeness, directory
durability, artifact verification, teardown failures, HTTP token casing and
distinguishing logout cancellation from a natural idle timeout.

Open qualification includes a selected public host, actual systemd deployment,
public certificate/SAN coverage, adverse external proxies, non-Linux native
clients, long-duration renewal and sustained load/RSS measurements. The two-slot
runtime test proves its configured admission bound; it does not establish a
whole-process memory budget. No public reachability claim follows from these
isolated tests or a process-start event.
