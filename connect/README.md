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
  supplied. Unknown-length HTTP/2 requests have a bounded reader, but wire-level
  HTTP/2 integration is still pending. This restriction does not apply to SSE
  responses or the already qualified tunnel's WebSocket framing.
- Forwarded API headers are an explicit allowlist: content type/length, Accept,
  Accept-Encoding, User-Agent, Anthropic-Version/Beta, OpenAI-Beta, Last-Event-ID,
  X-Request-ID and WebSocket handshake headers. Credentials, proxy identity,
  route-override and other custom headers are removed. Standard HTTP transports
  still own hop-by-hop framing. Native token delegation is a later connector
  adapter stage; caller Connect keys are never native application credentials.

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

## Local verification

```sh
go -C connect test ./internal/... -count=1
# The lab also requires the exact binary identified in transport.lock.json:
ANVIL_CONNECT_WSTUNNEL=/path/to/verified/wstunnel go -C connect test ./lab -count=1 -v
```

Dependencies are pinned in go.mod/go.sum: [bbolt](https://github.com/etcd-io/bbolt)
(MIT), [go-jose](https://github.com/go-jose/go-jose) (Apache-2.0),
[coder/websocket](https://github.com/coder/websocket) (ISC), and
[Go system interfaces](https://pkg.go.dev/golang.org/x/sys) (BSD-3-Clause).
The initial runtime and transport qualification target Linux amd64.
