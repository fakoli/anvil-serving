# Anvil Connect native component

Implementation in progress. The separate Go module contains the qualified
transport lab and access-control libraries; it does not yet provide a deployable
gateway, browser login, or a replacement for an installed Tailscale edge.
The Python router and controller gain no runtime dependencies from this module.

The [transport finding](../docs/ANVIL-CONNECT-TRANSPORT.md) records the pinned
wstunnel release, measured behavior, and environments still requiring testing.

## Initial declaration and API contract

- [Gateway declaration](examples/connect.json): one exact lowercase DNS hostname
  per resource, a path prefix matched on segment boundaries, explicit methods,
  connector identity, loopback tunnel endpoint and positive bounded limits.
- [Connector declaration](examples/connector.json): an independently provisioned
  local rule, fixed loopback HTTP origin and environment reference to the native
  application token. Remote declarations cannot widen this envelope. Non-loopback
  origins and their DNS/TLS trust contract are deferred.
- API clients use either `Authorization: Bearer <connect-key>` or
  `X-Api-Key: <connect-key>`, exclusively. The key grants exact resource/method
  access. API requests with Origin, Cookie or proxy credentials are refused;
  browser sessions have a separate implementation stage.
- Public hostnames have no explicit port or trailing dot. Paths reject escapes,
  non-ASCII characters, dot segments and duplicate separators. Applications
  requiring those URL forms need an explicit compatibility extension.
- HTTP/1 uploads require a known body length. Transfer-coded requests are denied
  because Go's parser discards Content-Length when both framing mechanisms are
  supplied. Unknown-length HTTP/2 requests have a bounded reader and pass the
  real tunnel API test. Ordinary inner requests require HTTP/2; classic WebSocket
  upgrades use a separate HTTP/1 transport. This restriction does not apply to SSE
  responses or the already qualified tunnel's WebSocket framing.
- Forwarded API headers are an explicit allowlist: content type/length, Accept,
  Accept-Encoding, User-Agent, Anthropic-Version/Beta, OpenAI-Beta, Last-Event-ID,
  X-Request-ID and WebSocket handshake headers. Credentials, proxy identity,
  route-override and other custom headers are removed. Standard HTTP transports
  still own hop-by-hop framing. The connector supplies the declared native token
  only after its independent rule and authenticated gateway peer checks pass;
  caller Connect keys are never native application credentials.

The API integration test uses the managed pinned tunnel, verified inner TLS,
native-token delegation, immediate SSE delivery, WebSockets, upload deadlines,
and cancellation without replaying an ambiguous POST. TLS verifies the deployment
CA and exact service names. Binding those certificates to the currently enrolled
installation generation and closing access on revocation remain required before
deployment. A valid certificate name alone is not proof of a current installation.

The Linux process wrapper executes the digest-verified binary inode and passes
opened immutable certificate, key, trust and restriction files by descriptor.
Rotating header files are confined beneath an opened, owned mode-0700 directory.
It supplies an explicit environment, discards upstream logs that could contain
credentials, and reaps only its owned children. It does not claim readiness from
process start or listener existence.

## Authority state

API keys default to 24 hours and are bounded to one minute through 30 days.
Only hashes of random credentials are stored. Principal changes increment a
generation, invalidating earlier keys; key revocation persists across restarts.
Authority reset changes an epoch so historical credentials fail validation.
Copying an old database file alone is not a safe restore procedure.

State is a separate bbolt database in a directory owned by the service user with
mode 0700, with a mode-0600 database. Opened handles are checked for ownership,
type, inode identity and unwanted links. OS permissions protect this database;
it is not encrypted or tamper-authenticated. The privileged wrong-owner test has
not run in the unprivileged development environment.

New installations generate keys locally. Invitations expire within ten minutes;
redemption verifies possession and creates a pending installation. Activation
requires an administrator to check its public-key fingerprint independently.
The signed installation-control proof has a maximum 30-second lifetime, fixed
role/resource bindings and persisted replay markers. This identity is distinct
from a human dashboard session or an API key. Endpoint integration and active
connection revocation remain implementation stages.

The browser-session library now uses the managed OIDC issuer with single-use
state, nonce and PKCE transactions, exact callback hosts, explicit human grants,
and opaque host/resource sessions. Pending transactions and sessions are bound
to the authority epoch. Logout invalidates that human's Connect sessions across
resources; it does not remove the application's cookies or the IdP session.
The authority caps active sessions at 1,024 globally and 32 per human and reclaims
expired or invalid records before issuance. Its owned OIDC client requires TLS
1.3 and the same HTTPS origin for discovery, authorization, token and key URLs.
These are library tests against a synthetic issuer; live Authelia, browser
cookies, callback routes and native dashboard controls remain to be qualified.

## Local verification

```sh
go -C connect test ./internal/... -count=1
# The lab also requires the exact binary identified in transport.lock.json:
ANVIL_CONNECT_WSTUNNEL=/path/to/verified/wstunnel go -C connect test ./lab -count=1 -v
```

Dependencies are pinned in go.mod/go.sum: [bbolt](https://github.com/etcd-io/bbolt)
(MIT), [go-jose](https://github.com/go-jose/go-jose) (Apache-2.0),
[go-oidc](https://github.com/coreos/go-oidc) (Apache-2.0),
[Go OAuth2](https://pkg.go.dev/golang.org/x/oauth2) (BSD-3-Clause),
[coder/websocket](https://github.com/coder/websocket) (ISC), and
[Go system interfaces](https://pkg.go.dev/golang.org/x/sys) (BSD-3-Clause).
The initial runtime and transport qualification target Linux amd64.
