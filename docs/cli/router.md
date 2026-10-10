# Router

[CLI overview](../CLI.md) · [Model serves](serves.md) · [Models & recipes](models.md)

The `router` family operates the deployed capability meta-router data plane. Use it
to run the router directly, manage its service lifecycle, inspect its endpoint,
and perform guarded tier transitions.

## Command map

Use `anvil-serving router ACTION --help` for the exact usage, examples,
configuration precedence, behavior boundaries, global targeting options, and the
owning documentation link.

### Run and discover

| Command | Purpose |
| --- | --- |
| `router run` | Run the router in the foreground. |
| `router endpoint` | Show the listen address, port, and this node's Tailscale DNS name. |
| `router diagnose` | Inspect active requests or retained request/session metadata, without replaying it. |
| `router workloads` | Read bounded canonical workloads from one explicit router endpoint. |

### Deployment lifecycle

| Command | Purpose |
| --- | --- |
| `router up` | Start the deployed router. |
| `router down` | Stop the deployed router. |
| `router restart` | Restart the deployed router. |
| `router reload` | Reload router configuration. |
| `router install-config` | Atomically install a validated capability meta-router config, including tier-set migrations. |
| `router status` | Show bounded router status. |
| `router export-config` | Export one verified secret-free installed router configuration. |
| `router logs` | Read bounded router logs or explicitly follow new output. |

### Safe tier transitions

| Command | Purpose |
| --- | --- |
| `router transition-status` | Show current tier-transition state. |
| `router quiesce` | Stop admitting work to one router tier. |
| `router drain` | Wait for a quiesced tier to drain. |
| `router readmit` | Safely return one tier to service. |

### Credentials

| Command | Purpose |
| --- | --- |
| `router token` | Inspect router-token state without printing the token. |

## Run the router

```bash
anvil-serving router run
anvil-serving router run --config configs/example.toml
anvil-serving router run --config configs/example.toml --host 127.0.0.1 --port 8000
```

Without `--config`, the router uses `$ANVIL_SERVING_HOME/router.toml` (default
`~/.anvil-serving/router.toml`) before the legacy `./router.toml`. An explicit
path selects one exact capability-alias configuration. The router remains a stdlib-only
foreground service; use the lifecycle commands when the deployment is managed
by the operator substrate. The default bind is `127.0.0.1`; do not expose a
non-loopback bind without an operator-provided authentication layer.

## Inspect the deployment

```bash
anvil-serving router status
anvil-serving router endpoint
anvil-serving router logs --tail 200 --since 10m
anvil-serving --json router status
```

`router status --json` and the controller/MCP status report include a `custody`
observation: container/image IDs, the configured image reference and its current
local image ID, Compose project/service, start/restart metadata, and mount
names, paths, types, and read-only flags. Environment values, command arguments,
arbitrary labels, and file/key-store contents are excluded. Paths are private
operator evidence; sanitize them before publication. Missing or malformed
inspection is reported as `custody.available=false`, independently of health.
A missing local image reference has a null match result, not a mismatch.
The CLI JSON envelope keeps its existing fields, but `data` now contains the
structured status object instead of the former rendered-text string. Ordinary
human output is unchanged; existing MCP/controller status fields are retained.
This is a point-in-time observation, not a config hash, ownership authorization,
or a transaction lock; recheck before a controlled restart. Use `router
fleet-status --live` for the installed config's raw SHA and runtime reachability.

`router endpoint` reports the configured listen address and port. When available,
it also reports the current node's Tailscale DNS name; it does not change routing
or tailnet configuration.

Without `--follow`, logs are bounded and return after the selected window.
`router logs --follow` is an explicit foreground stream and does not support JSON.

Expected client disconnects produce one `event=client_disconnected` line instead
of a socket traceback. It includes the exception class, UTC timestamp, elapsed
request time in milliseconds, and `gateway_request_id`. Use that ID with
`router diagnose` to inspect retained timing and outcome metadata. Disconnects
before authenticated inference has started use `gateway_request_id=-`.
The event says the downstream connection closed; it does not establish whether
the caller cancelled, a proxy timed out, or an earlier delay caused the closure.
Upstream streaming failures remain separate `500 stream error after headers`
events. Neither log includes prompts, response text, tokens, or exception messages.

Token inspection is redacted by default:

```bash
anvil-serving router token
anvil-serving router token --reveal --confirm
```

Only the second form prints the local token value. Avoid using it in automation or
captured logs.

## Export installed configuration

On the router host, capture an exact promotion baseline with
`anvil-serving router export-config --expected-sha256 SHA256 --json`.
The expected digest is the file-byte SHA-256, not the public semantic digest
returned by `/v1/router/status`. The command verifies the running container,
its read-only `/etc/anvil/config.toml` bind from a regular `router.toml`, source
and installed hashes, bounded UTF-8 TOML, and the existing export secret checks.
It rejects links in the selected file's path and config dependency bundles.
Unrelated operator-home files are never enumerated or read. Whole-home
`host config inventory/export` guards remain unchanged. Output contains private
topology and belongs in private operator evidence. No service is changed.
The direct tier `extra_body.thinking_token_budget` setting is recognized as a numeric
inference limit only for integers from 0 through 1,048,576. Strings, booleans,
nested occurrences, and other credential-shaped fields retain the secret guard.
## Device API keys

On the router owner, configure a dedicated, untracked credential directory in
`router.toml` once. Keep the existing `auth_env`: that credential retains its
existing administrative access and is identified as `_legacy` in traces.

```toml
[server]
auth_env = "ANVIL_ROUTER_TOKEN"
api_keys_path = "/var/lib/anvil-serving/router-keys/keys.sqlite3"
```

For the standard Compose deployment, use the dedicated durable
`anvil-router-keys:/var/lib/anvil-serving/router-keys` mount from the updated
Compose template. The image seeds it with the router user and private permissions.
For an existing installation, update only the router image to `anvil-serving:1.5.0`
and add the following entries to the existing operator-home `docker-compose.yml`
once, retaining its other services, ports, and settings. Do not rerun `init` over
customized configuration to acquire this mount.

```yaml
services:
  router:
    volumes:
      - "anvil-router-keys:/var/lib/anvil-serving/router-keys"
volumes:
  anvil-router-keys:
```

This is a partial configuration delta; merge these entries into the existing
`services.router.volumes` list and top-level `volumes` mapping. After building or
installing the 1.5.0 image, preview and recreate the router while retaining its
current installed router configuration:

```bash
anvil-serving router up --recreate --dry-run
anvil-serving router up --recreate --confirm
```

Then run these commands against the host candidate `router.toml` shown above:

```bash
anvil-serving router keys init --config PATH --container anvil-router
anvil-serving router keys create --config PATH --container anvil-router --name laptop --model llm.primary --path /v1/chat/completions --rpm 60 --expires-days 90 --out ~/.config/anvil-serving/device-secrets/laptop.key
anvil-serving router keys list --config PATH --container anvil-router
anvil-serving router keys usage --config PATH --container anvil-router --key-id KEY_ID
anvil-serving router keys revoke --config PATH --container anvil-router --key-id KEY_ID
```

The commands select the normal operator-home `router.toml`; `--config PATH`
selects another file. In container mode, its absolute `api_keys_path` is the
**container-visible** path. The command verifies the running Compose router and
its writable persistent mount, then executes as the router user. The generated
secret travels through a captured pipe into the host `--out` file; it is never
printed or passed in process arguments. Keep issued files out of Git.

After initializing storage, preview and install the candidate configuration
with `router install-config --config PATH --dry-run`, then repeat with
`--confirm`. This activates device keys using the existing guarded config
installation and restart. Store initialization alone does not change live auth.
Managed router lifecycle commands preserve this credential volume. Back it up
privately; never use `docker compose down --volumes` on this deployment. Volume
loss destroys issued-key authority and configured startup fails closed. Do not
copy independent stores to replicas.

For a native router, omit `--container` and use an absolute host path owned by
the router process and CLI operator. Initialization creates the final directory
with mode 700 and database with mode 600 on POSIX. Windows ACLs must restrict
access to the process identity and administrators; unsafe storage is refused.
Secret output receives the same protection. SQLite sidecars stay in the private
directory. After activation, creation, expiry, and revocation apply on subsequent
requests without reload. Revocation does not cancel already admitted requests.
A missing or unreadable store rejects device access; the configured master
credential retains its existing access.

Creation writes the random secret once to the new `--out` file and prints only
public metadata, including `key_id`. It refuses to overwrite an existing file.
If host delivery fails, the command revokes the new key. If the container also
becomes unavailable during revocation, it reports the public `key_id` with
`cleanup_required: true`; revoke that ID before retrying creation.
Install that file using your protected credential distribution mechanism; send
its value as `Authorization: Bearer ...` or `x-api-key`. Device labels are
operator-supplied labels, not cryptographic proof of a physical device. Issue a
separate key for each device or application. Rotate by issuing a replacement,
installing it, checking usage, then revoking the old ID.

Repeat `--model` and `--path` for additional grants. Both must authorize a
request. Chat grants use the router's trimmed, case-insensitive aliases;
embeddings and reranking use exact served-model names. Supported inference
paths are `/v1/chat/completions`, `/v1/messages`, `/v1/responses`,
`/v1/embeddings`, and `/v1/rerank`. `/v1/models` is granted automatically and
returns only granted chat aliases. Other metadata, operator, admin, audio,
media, MCP, and A2A endpoints are denied to device keys. Purpose models remain
available through their dedicated endpoints, as before; the chat catalog does
not advertise them.

Each key has a persistent token bucket: `--rpm 60` allows a burst of 60 requests
and replenishes one request per second. All permitted endpoints, including
catalog reads, share the budget. Over-budget requests return 429 with
`Retry-After`; grants denied before rate admission do not consume tokens.
SQLite transactions serialize admission across threads and local processes
sharing the store, and restart does not reset the bucket. This is a single-host
store, not a distributed quota service; do not place it on a network filesystem.
For long-running chat requests, the existing `[server.client_limits]` can also
cap concurrent requests by the generated key ID. Tier admission still protects
model capacity independently. Token-per-minute quotas would require reserving
estimated input/output tokens before dispatch and reconciling measured usage
at completion; this release does not claim such quotas.

`keys usage` reports a bounded recent access list;
`--limit` controls returned rows and `--key-id _legacy` selects master traffic.
The store retains at most 10,000 access records and 1,024 key records. At key
capacity, expired and revoked records are pruned before issuing replacements;
their retained audit rows remain searchable by key ID. HTTP status describes transport
response status: a stream can begin with 200 and subsequently fail. Join its
`request_id` to `router diagnose --request-id ...` for inference outcome,
selected tier, latency, and observed token usage. Those existing decision logs,
active request records, and optional trace exports carry the trusted `client_id`
for chat; purpose-model decision records carry it as well. A caller-provided
client-ID header or JSON field cannot override it. Upstreams receive the
router-generated request ID, not the device credential.

Generated keys use the reserved `ask_` prefix; keep separately configured scoped
operator credentials outside that namespace. Anonymous requests and health
probes do not consume the retained per-key usage history or generate per-request
authentication logs. Authenticated device denials are retained under the key ID.

The access audit records timestamps, key IDs, request IDs, allowed endpoint
labels, status, and elapsed time. Unknown paths collapse to a fixed label;
query strings, prompts, outputs, and credentials are excluded. Failure to
write access history emits `event=key_audit_unavailable`; it does not turn a
completed inference into a retryable failure. Decision-log retention remains
controlled by `decision_log_path`; access history is not a billing ledger.

## Workloads

Read active and recent router work from one explicitly authenticated router:

```bash
anvil-serving router workloads --router-url http://127.0.0.1:8000/v1 --auth-env ANVIL_WORKLOAD_TOKEN --expected-node node-a --active-only
anvil-serving router workloads --router-url http://127.0.0.1:8000/v1 --auth-env ANVIL_WORKLOAD_TOKEN --expected-node node-a --recent-seconds 3600 --limit 200 --json
```

The command does not discover a router, resolve topology, or borrow the normal
data-plane bearer. The named credential must carry `workloads:read`, and the
reported node must match `--expected-node`. `--host` is only a record filter;
it does not select the endpoint. See [workload visibility](../WORKLOAD-VISIBILITY.md)
for filters, canonical records, partiality, timestamps, and authorization.

## Diagnose

Use the `X-Anvil-Request-Id` from an inference response:

```bash
anvil-serving router diagnose --request-id req_0123456789abcdef0123456789abcdef
anvil-serving router diagnose --request-id req_0123456789abcdef0123456789abcdef --router-url http://127.0.0.1:8000 --json
anvil-serving router diagnose --active --json
anvil-serving router diagnose --session-id SESSION_ID --json
```

The command reads the router credential from `ANVIL_ROUTER_TOKEN` or the
environment variable selected by `--auth-env`. It retrieves one terminal
decision and separately labeled current build metadata using bounded GETs.
`--timeout` is a per-read socket timeout, at most 30 seconds. A missing record
does not prove the request never ran: active requests, buffer eviction, older
processes without retained JSONL, and unsupported lookup can all explain its absence.
Use `--active` with `--session-id` to filter current requests for one session.

See [request diagnostics](../ROUTER-DIAGNOSTICS.md) for timing, usage provenance,
correlation, retention, and interpretation limits.

## Lifecycle

```bash
anvil-serving router up --compose deployment/docker-compose.yml --service router --env-file deployment/router.env --dry-run
anvil-serving router up --compose deployment/docker-compose.yml --service router --recreate --confirm
anvil-serving router reload --confirm
```

Lifecycle mutations are guarded. Preview them first when `--dry-run` is available,
then repeat with `--confirm`. Compose operations resolve `--compose` first, then the operator-home
Compose file, then the packaged deployment example. Container lifecycle operations
default to `anvil-router`.

`router up --dry-run` reports the resolved Compose file, environment file, service,
container, and exact Docker Compose command; it does not invoke Docker. Confirmed
`router up` reports the same selected target after it completes. An explicit
`--compose` path always wins over the operator-home default; the command never
changes or removes that operator-home file.

For credential-shaped `${NAME}` references declared by the selected Compose file,
values in the selected `--env-file` are authoritative over same-named ambient
process values. This prevents an unrelated shell or harness environment from
silently rotating router credentials during a recreate. Non-credential variables,
including a per-invocation `ROUTER_IMAGE`, retain normal Compose override behavior.
The lifecycle output never includes resolved credential values.

`--recreate` is available only for `router up`. It maps to Docker Compose
`--force-recreate` while retaining `--no-deps`, so the operation recreates only the
selected router service and does not start or recreate model services, alter router
configuration volumes, or modify the host outside Docker's requested router action.

Install a complete capability meta-router config with the same preview-first boundary:

```bash
anvil-serving router install-config --config deployment/router.toml --dry-run
anvil-serving router install-config --config deployment/router.toml --confirm
```

The confirmed command quiesces and drains the current tier set, validates and
atomically writes the config, restarts the router, and succeeds only after the
router reports the exact desired tier IDs. It returns `tier_status` and
`unavailable_tiers` so stopped or unhealthy model serves remain visible without
turning a successful config installation into a false failure. Use
`router readmit` and `eval preflight` for readiness and qualification; installing
a config does not promote an unavailable model or claim that every serve is ready.

## Tier transitions

A safe tier transition is explicit:

```bash
anvil-serving router quiesce --tier primary-local --dry-run
anvil-serving router quiesce --tier primary-local --confirm
anvil-serving router transition-status --tier primary-local
anvil-serving router drain --tier primary-local --timeout 120
anvil-serving router readmit --tier primary-local --confirm
```

Use `transition-status` between steps. The commands preserve the distinction between
stopping new admissions, waiting for active work, and returning a tier to service.

## Fleet status

`router fleet-status` answers one question: **is every installed capability
actually served from the router's own runtime perspective?**

```bash
anvil-serving router fleet-status
```

By default it asks the deployed router container to read its installed config
and probe every declared alias, purpose model, and audio route from inside that
same runtime. The operation is read-only: Docker is used only as the bounded
execution boundary, and no lifecycle or configuration state changes.
`--json` emits the same report structurally; `--timeout` bounds each probe.

To inspect a file that may not be installed, pass it explicitly:

```bash
anvil-serving router fleet-status --config candidate-router.toml
anvil-serving router fleet-status --config candidate-router.toml --probe-perspective router-runtime
```

That result is labeled `configured-file` and `command-host`. A file inspection
is configuration evidence, not proof of live installed health. The optional
`--probe-perspective router-runtime` streams the bounded candidate config over
stdin to the live router container, probes it from that runtime, removes the
short-lived runtime file, and returns only the sanitized report. The candidate
path and contents never enter the container process arguments. The normal live
command selects the installed config and that perspective automatically. The
controller exposes the same live-only behavior through the bounded
`router_fleet_status` tool.

It exits non-zero when a **declared alias** has no reachable backing serve,
so it works as a pre-promotion or monitoring check. Purpose models and audio
routes are reported but do not fail the command on their own.

Two behaviours worth knowing:

- **An authenticated endpoint answering `401` counts as reachable.** Something
  is serving and asking for a token; treating that as down would report every
  authenticated tier as broken.
- **Runtime-relative endpoints stay runtime-relative in live mode.** A
  `host.docker.internal` endpoint is probed unchanged from the router runtime.
  During explicit command-host file inspection it is translated to
  `127.0.0.1`; if that probe fails, the result is typed
  `probe_perspective_mismatch` instead of being presented as a definitive
  fleet outage. `localhost` is never substituted.
- **Reports do not contain endpoint URLs, IP addresses, or DNS names.** Rows
  retain only the capability name, selected tier/model, a coarse endpoint kind,
  probe perspective, HTTP/transport result, and typed failure class. A SHA-256
  identifies an inspected config without publishing its path or contents.

This exists because on 2026-08-08 the router advertised three routes whose
backing serves had been off for hours with no signal anywhere. See
[Strategy: make divergence loud](../STRATEGY-MAKE-DIVERGENCE-LOUD.md).

## Related references

- [Supplemental memory](../HINDSIGHT.md)
- [Capability meta-router](../META-ROUTER.md)
- [Meta-router request path](../THIN-CAPABILITY-GATEWAY.md)
- [Configuration](../CONFIGURATION.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
- [Troubleshooting](../TROUBLESHOOTING.md)


## Caller accounting

Use the supported operator commands:

```bash
anvil-serving router usage active
anvil-serving router usage recent
anvil-serving router usage query --granularity cumulative
anvil-serving router keys bind --key-id key_fixture --kind human --owner-id human_fixture --expected-revision 0 --dry-run
anvil-serving router keys backup --out /protected/snapshots/router.sqlite3
anvil-serving router keys restore --snapshot /protected/snapshots/router.sqlite3 --out /protected/restore/router.sqlite3
```

`router-diagnostics.toml` loads automatically from the operator configuration home.
Save the non-secret router origin, timeout and a protected raw-token file reference:

```toml
router_url = "https://example.test"
timeout = 5
credential_file = "secrets/usage-admin.token"
```

Installation owns creating that owner-only token file and enrolling its existing
`workloads:read` authority. The CLI rejects links, unsafe ancestors and shared
home `.env`; it never reads the diagnostic dotenv file for accounting. A selected
process credential (`--auth-env NAME`) may override the file; credentials are never
command arguments. This scope must be restricted to administrators and the dedicated
scraper before sensitive usage is enabled. Inference/device/profile authority is
insufficient. No read command replays inference or changes configuration.

Default output is exact protected API JSON. Native `--json` adds the standard CLI
envelope and its existing secret-key redaction, including `credential_id`; use
default output for complete exact identity readback. Exact integers are never
converted to floats or capped by the generic diagnostic projector.

`recent` is the last24 UTC hours of detail, not a completeness claim. Exact `query`
accepts `--granularity detail|daily|cumulative`, `--from-utc`, `--to-utc`, JSON
`--filters '[["actor_kind","service"]]'`, JSON `--group-by '["model"]'`, `--limit`,
`--cursor` and `--require-complete`. Null, boolean and integer filter values keep
their native JSON types. Detail/daily require both UTC Z bounds; daily boundaries
must be midnight. Cumulative forbids windows. Active allows only filters and limit
(default50/max200). Coverage, retained floors, unknown/partial/not-applicable tokens,
truncation, omissions, cursor and fixed refusal codes retain their API meanings.
Missing/invalid activity samples or clock rewind produce null age; polling/phase
updates never reset activity. See [the retained contract](../ROUTER-USER-USAGE.md).

Binding is an operator-local ordinary-key revision CAS. `--dry-run` performs the
same validation without writes; omit it to apply and read back the new actor revision.
It preserves grants and immutable history, refusing stale revisions, expired or
Connect-owned keys. No binding or snapshot command migrates schema automatically.
Backup/restore reuse consistent protected SQLite snapshots and require an absent
destination. Existing/newer/substituted files are never overwritten. Container
operations use `--container NAME` with paths inside verified durable writable mounts;
backup and restore outputs stay inside that container. Restore does not install,
activate, re-admit or claim that active requests have drained.

## Managed client attribution

```sh
anvil-serving router clients preview
anvil-serving router clients install --confirm
anvil-serving router clients readback
```

These offline commands load `client-identity.json` beside the conventional operator-home
router config. `--config PATH` selects an explicit managed declaration. Install without
`--confirm`, or with `--dry-run`, previews only. Default JSON includes the exact desired
and currently read owner bindings. The global `--json` envelope captures this complete
JSON as its `data` string; the default output is the direct, structured binding document.

For a managed container, installation also supplies a protected `client-namespace.json`
beside that declaration. The same short commands then select its dedicated offline Compose
worker; `--compose PATH` explicitly selects another accepted worker profile. The worker
opens the existing mounted KeyStore inside its namespace and holds native producer and
writer custody continuously through owner enrollment, WebUI approval publication and
declared recipient staging. The host never opens the container's database or its volume.
Running-router preview/readback uses the exact current container incarnation and performs
fresh native owner, grant and expiry checks. Credentials travel on the private stdin pipe,
never arguments or output. A prior successful readback is not later authority.

The namespace schema `anvil.client-namespace/v1` has exactly `compose`, `container`,
`expected_image_id` and `declaration_path` in addition to `schema`. Installation binds the
full immutable image ID and fixed `/run/anvil-client-worker/client-identity.json` location.
Its `anvil.client-worker/v1` job declares the canonical declaration digest, accepted helper
digests, fixed recipient operations and individual material directories. The dedicated
profile uses the existing named key volume, readonly declared inputs, and only explicit
private feature directories writable at their same paths. Its inert `/bin/false` entrypoint
prevents accidental service startup; only the bounded native enrollment command overrides
it. It has no network, ports, GPU, shared environment or account mounts.

Closed, live, competing or unknown retained ownership still refuses unsafe custody. The
operator separately holds legacy producers stopped; this command does not prove upstream
completion, drain work, activate WebUI, restart a router or authorize an interruption.
Partial publication stays available for independent readback and is not blindly rolled
back. Installation and current-grant validation remain distinct from live qualification.

T015's owner adapter supplies a bounded, protected nonsecret declaration; credentials
and signer material remain in protected referenced files. The closed declaration schema
`anvil.managed-client-identity/v1` contains `router_config`, `installed_path`, `bindings`
and `webui`. Each binding contains `client_id`, `key_id`, `kind` (`human` or `service`),
`owner_id`, `expected_revision`, and `policy` (`direct`, `service`, `webui`, or explicit
`detached_service_only`). Non-direct policies require service ownership. Installation
uses ordinary-key owner revision CAS and preserves the entire existing grant. Connect
account-owned keys are refused. Storage must already have the accepted accounting
migration; this command does not initialize, migrate, issue or widen credentials.

Each WebUI entry contains `client_id`, `instance`, `native`, `approved_recipients` and
`request_paths`. The instance must join the actual router's native signed-forwarding
profile: matching device key, required user, and dedicated protected signer-file reference.
The direct enrollment command does not open signer material. The managed worker's fixed
WebUI preparation validates and publishes its dedicated protected signer and approval
inputs under the same custody. `native` is T015's nonsecret projection
of `openai.api_base_urls`, `openai.api_configs` and explicit `other_recipients`. The config
map must include every URL index, with exact boolean `enable` and `custom_header_names`
fields. Header **names only** are projected, never values or API keys. Every enabled URL
and every plugin/tool/other recipient must equal the approved destination set. Custom
forwarded-user header overrides are refused because native custom headers apply last.
`request_paths` must explicitly map all four `foreground`, `title`, `tag` and `background`
paths to enabled native connection indices. Unknown fields, duplicate fields, nonfinite
JSON, absent inventory entries and invented completeness flags are refused.

This validates the declared native inventory, not discovery of live account state. The
adapter must produce the complete actual recipient/task-model/custom-header projection
before global WebUI forwarding can be enabled. Independently selected providers, models,
routes, active grants and sessions are not rewritten or contacted. Native no-user omission
and plaintext signer-error fallback must still be rejected by the router's require-user
verifier; detached service-only clients use their separate explicit policy.

Install writes only the protected feature binding document at an **absent** destination,
then independently rereads it and current key ownership. Matching repeats are idempotent;
a differing existing document is never overwritten. No client adapter or service is
activated. If file installation fails after enrollment, some keys may already be bound:
retry validates the exact original CAS and readback reports failure/incomplete state;
there is no unsafe grant rollback. Corrected declarations need a separately chosen absent
feature destination and appropriate new owner revision.

Readback checks actual installed bytes and current unexpired, unrecalled key ownership,
not an echo of the requested declaration. Unmigrated/unconfigured state is incomplete or
a safe failure. `configuration_present_live_pending`, `declared_inventory_validated_live_pending`
and `live_status: unqualified` distinguish source/configuration proof from live behavior.
T015 native adapter provisioning and independent T020 foreground/title/tag/background,
direct/service, recipient privacy and provider-continuity qualification remain required
before rollout. The exact CLI accounting ledger remains separate from sampled dashboards.

## Whole-router admission

`anvil-serving router quiesce --scope router --confirm` closes the configured
native owner's complete inference admission and prints its opaque barrier token.
Use `anvil-serving router drain --scope router --barrier-token TOKEN --timeout 30`
for bounded actual ownership readback. The timeout is an integer from 1 to 900;
a timeout leaves work and admission closure intact. Readmission uses
`anvil-serving router readmit --scope router --barrier-token TOKEN --confirm`.
Tier/member commands retain their existing scopes; they do not prove router drain.

The native single-owner producer requires explicit `server.router_owner_id`,
`router_owner_roster = ["owner_fixture"]`, `usage_domain_id`, an absolute protected
`admission_state_path`, the existing protected `api_keys_path`, and authentication.
The roster must name exactly that owner. The existing explicitly migrated usage
schema records enabled and disabled runs before traffic; `usage_enabled = false`
is the default. Sensitive metrics require separate `usage_metrics_enabled = true`
and scoped authorization. Installation owns these private settings. A second
process cannot acquire the same persistent native owner lock. Foreign/unknown
runs or unsupported shared authority refuse startup and authoritative coverage.

After independently holding every producer and proving terminal predecessor
work, a stopped first-upgrade router can migrate its existing protected store:

```bash
anvil-serving router keys migrate --compose docker-compose.yml --env-file router.env --backup-out /var/lib/anvil-serving/router-keys/pre-accounting.sqlite3 --offline --confirm
anvil-serving router up --compose docker-compose.yml --env-file router.env --confirm
```

Migration uses only the selected router service in its candidate image, with no
dependencies started. The mounted configuration selects storage and the declared
single owner. The absent backup destination must be in the durable router-key
directory. Native installations instead use `--config PATH` and a protected
`--backup-out PATH`, without `--compose`. A live `--container` migration is refused.
The producer and store-writer fences remain held through snapshot and migration;
the existing bounded SQLite writer transaction preserves keys, grants and audit.
Never restore that older snapshot over later committed authority or accounting.

Cold `up` distinguishes an exact absent/created/exited managed target from live
replacement. It verifies the complete Docker mount-consumer roster and executes
an offline candidate custody check under the same lifecycle lock as launch.
This is offline startup, not predecessor drain. Probe locks end before launch;
the actual new process must acquire its own producer fence and reject competing
or retained owners. External producer maintenance holds remain necessary.
Migration remains a first-bootstrap operation and refuses retained native
ownership. Cold startup can probe an existing owner only with matching protected
store binding, actual producer/writer exclusion and physically proven inactive
local runs. Actual startup rechecks that proof, reconciles local interrupted
requests through the ledger's recovery operation, preserves committed rows and
registers the real successor. The old closure stays durable through that
verification; transfer persists a new closed generation with a new barrier.
Live, foreign, incomparable or unknown old runs refuse before transfer.
The default `server.router_owner_backend="native-process"` requires the same
verified boot, PID/user namespaces and unrestricted procfs view. A replacement
namespace remains UNKNOWN in that mode. The opt-in `managed-container` mode uses
protected per-run custody recorded by the native managed Docker lifecycle. Every
initial and successor process is durably quiesced before listening or starting
background work. The native helper verifies its actual live PID/start and
container namespace, while the host verifies the exact full container ID, image,
incarnation, Docker daemon, selected Compose service and mounted-state consumers.
The helper holds the actual storage fence while the host repeats that readback;
only an exact staged-digest commit finalizes the anchor. Pending custody never
allows readmission. Managed observation recognizes only Docker's exact read-only
`/proc/sys` subtree on the same procfs superblock; other sensitive substitutions
and restricted or incomparable views still refuse.

Managed `down`, `restart` and `up --recreate` stop the drained old incarnation,
verify it is actually stopped under the lifecycle/storage fences and preserve its
immutable physical-death proof before starting a successor. Cold `up` can perform
the same check for an anchored crashed incarnation without inventing consumption
or upstream completion. Probe, recovery, changed-config registration and ongoing
scope share that exact proof. Recovery preserves committed accounting and records
unfinished local requests as interrupted with unknown upstream outcome. Transfer
keeps the old closure through verification, then persists a new closed generation
and binds the proof once to the actual registered successor. Prior barrier tokens
cannot admit the new owner. Current accounting write authority begins with that
new generation; queries of earlier configurations retain measured rows and report
coverage outside the current authoritative window as incomplete.

A missing live anchor cannot be fabricated after death. Legacy/unanchored runs,
removed or changed incarnations, inaccessible native metadata and a partially
registered unanchored successor remain HOLD. This includes a crash between ledger
registration, closure persistence and custody transfer; conservative refusal is
an explicit recovery limit. Releasing an old file lock alone is never proof.
The managed profile must use the selected supported Compose configuration;
`restart`/`reload` accept `--compose`, `--service` and `--env-file`. The direct
`install-config` operation refuses this mode before replacement writes because it
cannot preserve the predecessor config for custody; stage config changes while
managed `down`, then use managed `up` with the declared profile.

The successor remains quiesced. Use the existing management commands to obtain
its new barrier and request readmission after the assembled owner roster is safe:

```bash
anvil-serving router quiesce --scope router --confirm
anvil-serving router readmit --scope router --barrier-token NEW_BARRIER --confirm
```

Local inactive-run recovery does not prove remote execution completed. Configured
memory currently has no terminal owner readback, so its observer remains UNKNOWN:
local reconciliation can finish while successor readmission and managed close
stay held. An external pause or bounded interruption does not supply a native
remote terminal contract. Ordinary readmission for this topology remains held. A separately authorized
one-time native acknowledgement is described below; it accepts uncertainty,
not terminal remote completion. These commands do not
disable memory, erase closure or turn UNKNOWN into zero.

Router restart/reload/recreate/down/config install require the actual old
container's supported all-path barrier, owned zero and consumed persistent closure.
The running old version cannot acquire this capability from new source tests.
Missing old support is a bootstrap HOLD. Successor identity or configuration
changes retain closure and require reviewed owner transfer before readmission;
a stale old-owner token never automatically opens a replacement process. Current
remote memory transport supplies no native retained-inference drain readback and
therefore holds router replacement while configured. Ambiguous media submission
also remains HOLD even if ordinary recovery marks the local job failed.


## Recovery diagnostics

```bash
anvil-serving router recovery-status --json
```

When a router cannot stay running after an interruption, this native read-only
command inspects its protected custody sidecars through the actual Docker mount.
It may need administrator filesystem access. It prints bounded phase counts and
boot, container, image and configuration match counts, never keys, account data,
run identifiers or raw records. It does not open the credential database, change
custody, stop a container or authorize recovery. Unavailable permissions, changing
metadata and malformed records refuse with a fixed error.

Sidecar matches cannot exclude a partially registered successor. The report
therefore leaves ledger correlation and successor presence UNKNOWN and recovery
eligibility false. Any recovery operation still needs native ledger correlation,
storage exclusion and physical-death proof. Do not remove custody records or
restore an older database to bypass a startup refusal.

## Acknowledged router maintenance

```bash
anvil-serving router maintenance --config maintenance.json --preview-out preview.json
anvil-serving router maintenance --config maintenance.json --confirm
```

Installation prepares the protected declaration and authorization from the
explicit human decision, final artifact identities and the fresh native preview.
The declaration names the exact Compose profile and its SHA-256, the protected
authorization, and an absent protected receipt destination. Preview does not
stop or reopen the router. Confirmation requires the exact preview digest and a
short, unexpired one-time operation identity. Credentials never appear in argv
or output.

The first `legacy-stop` phase binds the actual admitted legacy profile, mounted
non-secret config, container/image/daemon/start identity and consumer roster.
Metadata rendering explicitly disables project dotenv and service env-file
resolution. It acknowledges UNKNOWN old submissions and remote work; it does
not claim submitters were paused or drained. A protected pending acknowledgement
precedes the exact container stop. Independently verified stopped identity
establishes the ingress barrier before offline storage operations. Existing
non-secret legacy config permissions are preserved.

The candidate can then import that stopped receipt with its newest exclusive
rollback snapshot and schema migration:

```bash
anvil-serving router keys migrate --compose router-worker.json --backup-out /var/lib/anvil-serving/router-keys/pre-accounting.sqlite3 --maintenance-receipt legacy-receipt.json --offline --confirm
```

Import requires actual producer and writer exclusion, the same stopped
incarnation, the same protected store, and no retained usage runs. Unanchored or
unresolved retained rows remain HOLD. It never retrofits a legacy run, anchor,
closure or terminal receipt. The supported cold-start probe verifies this
import before a fresh managed owner registers CLOSED and finalizes its live
container anchor.

The separate `successor-readmit` authorization binds that new generation,
owner/domain/store/gate, config/roster/policy revisions, live anchor and native
observation. Every local request, delivery, worker and writer must be terminal;
owner, storage, callback and untyped uncertainty all refuse. Only the native
remote-memory terminal gap is eligible. The protected operator helper publishes
permission over private stdin; the HTTP endpoint cannot mint it. The owner
rechecks the preview under real producer/writer fences and durably consumes the
operation before opening. Replayed, expired or changed inputs refuse. Failure
leaves the gate closed and consumes any already-published acknowledgement.
Crash/restart creates another CLOSED generation without inheriting permission.

Bind accounting and protected metrics in the final configuration before this
first CLOSED startup. Scoped admin/nonadmin/datasource isolation and metadata
readback can be checked while inference stays closed; let those counted reads
finish before the final maintenance preview. Status, health and retained query
coverage keep acknowledged legacy and remote uncertainty visible. Committed
rows and interruption metadata remain intact. Preserve the named store volume,
including protected custody and acknowledgement sidecars, for rollback; never
replace newer committed state with an older snapshot.

### Current instrumented owner replacement

```bash
anvil-serving router maintenance --config current-stop.json --preview-out current-stop-preview.json
anvil-serving router maintenance --config current-stop.json --confirm
```

The distinct `current-instrumented-stop` phase is for a managed-container owner
that is already durably quiesced and has a finalized live anchor. It requires
all eight local workload families to be exactly zero and only the typed remote
memory terminal gap to remain unknown. Ordinary drain, consume, down, recreate
and readmit still reject that gap. A successful stop receipt says
`local_frontier="instrumented-quiesced"`, `remote_memory_terminal="UNKNOWN"` and
`drained=false`; it never calls the current local frontier uninstrumented.

Installation prepares the existing `router-maintenance-config/v1` declaration
with the admitted Compose path/hash, protected authorization path and an absent
receipt destination. This phase requires the distinct
`router-current-stop-authorization/v1` schema, the normal exact old
container/image/configuration and operation/expiry bindings, a retained
`legacy_receipt_sha256`, `acknowledge_uncertainty="remote-memory-terminal-only"`
and `ingress_barrier="closed-generation-exact-container-stop"`. Its additional
closed `successor` object declares `image_id`, `configuration_revision`,
`compose` and `compose_sha256`. Preview verifies the successor's actual pinned
image and read-only mounted configuration using metadata-only Compose rendering;
it does not resolve project dotenv or service environment files. The stopped
owner's native preview binds its actual run, store, anchor, closed generation,
roster and policy, while host observation binds daemon/start identity and the
complete mounted-state consumer roster. Confirmation requires the exact fresh
preview digest and a new explicit human acknowledgement of remote uncertainty.
Neither a legacy-stop grant nor an earlier readmission grant can authorize it.

A fixed native helper uses the predecessor's existing maintenance-preview and
writer-exclusion primitives. It keeps the actual writer fence through the final
host checks and physical stop; unsupported old images refuse before stopping.
Confirmation durably publishes a canonical one-time operation record in the
protected store before any stop, then a host pending receipt. A changed output
path cannot replay that operation. An existing host pending receipt can only
finish evidence for the same already-stopped incarnation while the original
approval remains valid; it cannot issue another stop of a live, restarted or
successor process. Failure leaves admission closed and never starts a service.

After the exact incarnation stops, the existing offline producer/writer fences
and staged native custody commit establish its immutable death proof. Committed
accounting and historical gaps are preserved; no migration or old-snapshot
restore occurs. The intended successor is only an approved intent, not an
automatic activation: use guarded `router up` separately. It starts CLOSED and
requires a new successor-readmit preview and separate human acknowledgement.

This phase keeps the existing closure/anchor/death schemas. A predecessor that
supports native maintenance-preview can be observed without replacing its
running code. A successor supporting those custody schemas can recover the
stopped owner without understanding the new external stop receipt. That receipt
is operator evidence; existing runtime history continues to show its retained
legacy and remote UNKNOWN frontier. The phase neither rewrites that history nor
claims that an older runtime exposes the new stop receipt in its history API.

## Fixed native SQLite storage

The opt-in router runtime uses a verified fixed SQLite build and its native
`SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE` API. All file openers verify the runtime before
file SQL, guard retained sidecars, and retain FULL durability. Ordinary DELETE
stores stay unchanged. Explicit offline conversion uses the same custody and
newest rollback snapshot:

```bash
anvil-serving router keys migrate --compose router-worker.json --backup-out /var/lib/anvil-serving/router-keys/pre-wal.sqlite3 --journal-mode WAL --offline --confirm
```

WAL conversion binds a protected mode record to the actual database inode. A
pending or unsupported policy refuses rather than being guessed or downgraded.
Read leases exclude conversion; the native helper can read through only its
own still-held exclusive gate. Default checkpoint behavior stays enabled.
A held reader can defer checkpoint progress. The existing 64 MiB ceiling
refuses new mutations while allowing a consistent newest backup and explicit
offline checkpoint under custody. Backups are independent DELETE snapshots;
close never substitutes for remote completion or drops FULL durability.
The runtime and WAL mode alone do not prove the durable latency gate.

Offline unique client credentials can be issued into the same stopped named
store through the bounded selected worker:

```bash
anvil-serving router keys create --compose router-key-issuance.json --offline --confirm --name client --model llm.primary --path /v1/chat/completions --out /protected/clients/client-token
```

The output is exclusive, owner-only and never overwritten. Public output lists
only the new key ID and grants. Enrollment and fresh native authorization use
the existing namespace worker with the same KeyStore and current owner/grant
CAS; host processes never open the Docker volume as a second store.
