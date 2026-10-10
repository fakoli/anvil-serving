# Supplemental memory

The Capability Gateway exposes Hindsight through `POST /v1/memory` and
`POST /v1/memory/mcp`. Existing harness memories remain authoritative. The
gateway does not ingest transcripts automatically or change model providers.

## Configure a principal-bound route

Create a device key granting the memory alias and both endpoint paths. Keep
the secret in the protected output file; the returned key ID is the principal.

```sh
anvil-serving router keys create --name memory-client --model memory.supplemental --path /v1/memory --path /v1/memory/mcp --out /protected/memory-key
```

In private router configuration, alongside the existing authenticated server
and device-key database:

```toml
[[router.memory_routes]]
alias = "memory.supplemental"
principal = "key-id-from-key-creation"
backend = "hindsight"
bank = "client-bank"
base_url = "http://127.0.0.1:8888"
auth_env = "HINDSIGHT_GATEWAY_TOKEN"
timeout = 120
```

The upstream credential belongs only to the router and the operator. Provide
it through a protected environment file. Consumers receive their scoped router
key, never the upstream administrative credential. Loopback is relative to the
router process. The upstream origin must be a declared private address, with
no path, query, userinfo, or redirect. The deployment owns verified TLS when
the upstream crosses hosts.

Each `(alias, principal)` has one bank and one backend. Repeating an alias for
different principals is supported. An explicitly shared bank requires an
operator binding for every allowed principal. Unknown aliases return 404;
another principal's alias returns 403. Missing credentials or unavailable
upstreams return 503. Selecting the reserved `hermes` backend returns 501;
no adapter reads or writes existing Hermes/Pi stores.

## Operations

The following limits also apply to per-user routes described below.

The REST body contains exactly `alias`, `operation`, and `arguments`:

```json
{"alias":"memory.supplemental","operation":"recall","arguments":{"query":"What is the release checklist?"}}
```

| Operation | Arguments | Limits |
| --- | --- | --- |
| `retain` | `content`, optional `context`, `tags` | 32,768 characters; synchronous |
| `recall` | `query`, optional `budget`, `max_tokens`, `tags` | 4,096 query characters; 8,192 output tokens |
| `reflect` | `query`, optional `budget`, `max_tokens`, `tags` | 4,096 query characters; 4,096 output tokens |

Budgets are `low`, `mid`, or `high`. Tags allow at most 16 identifiers of
128 characters. Fixed principal-bound routes reject caller-selected banks. Endpoints, models, defense policies,
metadata, asynchronous retention, document administration, and extra arguments
are rejected. The response names the selected alias, backend, operation, and
upstream result. A content rejection stays a rejection without echoing the
rejected material in the error.

Recall defaults to 1,024 fact-text tokens. It excludes entity summaries, raw
chunks and source-fact expansion, and returns a ranked prefix of whole facts
within 32 KiB of serialized JSON, preserving each fact's source references.
`truncated` and `omitted_results` report facts excluded by this byte cap; an
oversized first fact produces an empty, explicitly truncated result. Narrow
the query when results are truncated. The cap also applies with an explicit
token override because upstream token budgets exclude metadata. MCP returns
one text representation of the result instead of duplicating memory content.

The gateway bounds request bodies to 64 KiB, upstream responses to 2 MiB,
concurrent memory requests to four, and total upstream duration to the route
timeout (1–300 seconds). Deadline expiration returns 504 and releases the
permit, including when an upstream drips bytes. This is separate from model
admission and the device key's request budget. No automatic retry or fallback
occurs; an interrupted retain has an uncertain outcome.

## MCP clients

The stateless JSON MCP endpoint exposes only `memory_retain`, `memory_recall`,
and `memory_reflect`. Their schemas contain the aliases authorized for the
authenticated device key. MCP supports protocol versions `2025-11-25` and
`2026-07-28`; tool notifications never execute writes.

Clients supporting Streamable HTTP can connect directly to the authenticated
router endpoint. Stdio clients can reuse the packaged bridge:

```sh
anvil-serving mcp serve --controller-url https://router.example.test/v1/memory/mcp --auth-file /protected/memory-key
```

The bridge and router must have matching package versions. Register this as an
additional server; retain existing memory plugins and settings. Provisioning
owns protected files and client setup. Neither a browser login nor tailnet
membership grants access to the router memory endpoint.

## Deployment and recovery

### Per-user banks

A dynamic route resolves the authenticated Connect owner through a protected
bank policy. API keys must still grant the alias and both endpoint paths.
Unowned device keys cannot use this route; attribution headers and caller-supplied
user IDs do not grant access. Existing fixed principal routes keep their bindings.

```toml
[[router.memory_routes]]
alias = "memory.user"
backend = "hindsight"
base_url = "http://127.0.0.1:8888"
auth_env = "HINDSIGHT_GATEWAY_TOKEN"
access_file = "/run/anvil-memory/access.json"
timeout = 120
```

Mount the root-owned policy **directory** read-only so atomic replacements become
visible. Connect writes the host policy; the router and browser read it for every
operation. The dedicated directory uses mode `0750` and the configured reader
group; `access.json` uses mode `0640`. Keep bank creation receipts root-only.
Policy subjects are verified Connect `human:` principal hashes. Each entry has
`default_bank`, an `admin` boolean, and explicit `banks` operation grants. Members
can access only their granted banks; administrators can select and list all banks.

REST accepts optional top-level `bank`; omission selects the user's default.
`operation: "banks"` returns the authorized inventory and default bank. It accepts
`limit` (1–100) and `offset` (0–1,000,000) for administrator inventory. MCP exposes
`memory_banks` alongside the existing tools and accepts optional `bank` on memory
operations. These options are exclusive to Connect-owned dynamic routes.

Declare optional `memory` settings in the private Connect deployment:
`base_url`, protected root-only `auth_file`, absolute `access_file`, numeric
`reader_gid`, `default_banks` and `shared_banks`. The last two map local usernames
to existing shared defaults and explicit shared operation grants. Every shared
default needs a matching grant. No shared bank is created, modified or adopted.

Use `users configure-memory` with protected outside-Git installation JSON containing
`memory` and `user_defaults`. Optional defaults use
`{"email":"owner@example.test","bank":"shared-bank","operations":["recall"]}`;
the command resolves each email to exactly one enabled account before writing.
Then use `users memory USER --confirm` for existing accounts. It preserves current
browser grants and rotates existing Connect credentials without restarting sign-in.
Once installed, `users create` automatically provisions a personal bank, including
accounts created without browser grants. Administrators come from the account's
admin group or its `hindsight-ui:admin` grant. A configured shared default still
retains the account's personal bank.

Provisioning verifies the bank ownership marker and sensitive-data defense before
publishing access. Interrupted creation retries from its protected receipt.
Provisioning failures leave access closed and preserve created banks. Suspension
and deletion remove the policy entry before native account revocation, retaining
bank contents and receipts. Permanent deletion also withdraws shared/default grants
from the private declaration before removing identity records, so a reused username
receives a fresh personal bank. Resume through `users access`; retry incomplete memory
setup with `users memory`. Package installation and managed router/browser
activation are separate steps and require their own authenticated readback.

Infrastructure provisions the digest-pinned upstream service, storage, bank
defense, extraction credentials and backups. Adopt that external Compose
container with the existing `host services adopt --external-compose` contract;
the Control Plane can then status, log and manage it through the declared
resource owner. This does not introduce a model engine or a new product family.

Keep native Hindsight MCP/admin endpoints private. The upstream UI remains
disabled until its authenticated exposure is independently verified. Anvil
Connect exposure must retain both its resource authorization and the router
device-key check; do not expose the upstream admin origin as a client shortcut.

Connect edge publication preserves the existing tunnel configuration and verifies
DNS, ingress and a healthy connected tunnel after applying changes. HTTPS origins
use the resource hostname for TLS SNI unless an explicit override is declared.

Managed gateway startup waits for the declared identity provider through the local
Caddy listener before starting the gateway. This bounded check uses verified TLS,
exact issuer discovery and the declared edge identity. Provided certificates are
pinned to the declared leaf; ACME uses system trust. Rollback checks the restored
generation before restarting its gateway. The resolved Python interpreter must
be executable by the edge service account; private development environments may
not meet that requirement. Existing healthy services remain running on a no-op.

Use upstream-consistent backups and verify recovery in a fresh isolated volume.
Retain exact image, configuration and archive hashes. Do not restore a snapshot
over active memory storage to test recovery. A package rollback also needs the
appropriate router key-database backup when crossing schema versions.

## Import existing curated memories

Preview an explicit import, then apply the same configuration:

```sh
anvil-serving harness memory-import --config /protected/memory-import.toml --json
anvil-serving harness memory-import --config /protected/memory-import.toml --confirm --json
```

The operator configuration lists only the Markdown files to copy:

```toml
schema_version = 1
base_url = "http://127.0.0.1:8888"
bank = "imported-memories"
auth_file = "/protected/hindsight-api-key"

[[sources]]
id = "codex-primary"
harness = "codex"
root = "/protected/curated-memory-copy"
files = ["MEMORY.md", "memory_summary.md"]
```

Supported source labels are `codex`, `pi`, `hermes`, `openclaw`, and `claude`.
Select curated memory files explicitly; exclude credentials, session databases,
transcripts, and recovery archives. A configuration permits 32 sources, 128
files, 4 MiB per file and 16 MiB total. Larger migrations use separate configs.
Preview reads no credentials and makes no network requests.

The importer requires the target bank's `block_sensitive_data` defense, then
retains deterministic chunks with source identity and SHA-256 provenance.
A completion tag is written only after synchronous extraction succeeds.
Identical reruns verify provenance, content hash and that tag before skipping;
unmarked matches resume retention with the same document ID. Changed files create new
versions; neither old Hindsight documents nor original harness files are
deleted or rewritten. A rejected chunk produces a nonzero result with its
document ID, without printing memory content. Investigate failures before
retrying; do not disable the bank defense to force an import.

Import uses an operator credential directly against the private upstream.
Bulk retain calls have an 1,800-second deadline because a chunk can require
several serialized extraction calls; lookups and completion updates allow
30 seconds. This does not extend the gateway's 300-second interactive limit.
Consumers receive only principal-bound router access to the imported bank.
Back up and verify recovery after migration before treating it as complete.
