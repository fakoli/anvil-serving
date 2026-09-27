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
