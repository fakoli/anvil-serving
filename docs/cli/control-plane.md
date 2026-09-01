# Control Plane & Fleet

[Product families](../PRODUCT-FAMILIES.md#control-plane-fleet) ·
[CLI overview](../CLI.md) · [Fleet](fleet.md) · [Host & setup](host.md)

This family declares where operations belong, exposes bounded typed dispatch,
connects operator harnesses, inspects hosts and fleet state, and publishes
reviewed tailnet routes. None of it sits in the gateway request path: routing
remains a Capability Gateway operation, and resource owners retain lifecycle
authority.

## Choose a workflow

| Goal | Start here | Then |
| --- | --- | --- |
| Check deployment ownership | `topology validate` | Use `topology show` for the declaration or `topology resolve` for one command. |
| Check package parity | `fleet version` | Resolve skew or missing installations before a coordinated release. |
| Check operator-state drift | `fleet drift --repo PATH` | Review exact per-host file differences; do not copy one host's home onto another. |
| Update OpenClaw integration | `harness sync openclaw --dry-run` | Apply with `--confirm`, then check `harness status openclaw`. |
| Refresh Mini model limits | `harness sync clients --dry-run` | Review the exact router hash and per-alias limits, then apply with `--confirm`. |
| Give Hermes bounded media generation | `harness sync hermes-media --dry-run` | Review the profiles, skill digest, and eight-tool allowlist; apply with `--confirm`, then require an empty second preview. |
| Connect an MCP client locally | `mcp tools` | Configure the client to run `mcp serve` over stdio. |
| Operate a split host | `controller serve` | Probe it with `controller status`, then point `mcp serve` at it. |
| Add optional telemetry | `collectors configure` | Validate offline, then use `collectors inspect` for one bounded read. |
| Publish tailnet routes | `edge render` | Compare `edge status`, preview `edge up`, then apply with `--confirm`. |

## Command map

### Describe deployment ownership

| Command | Purpose |
| --- | --- |
| `topology validate` | Validate the base topology and optional overlay offline. |
| `topology show` | Render hosts, runtimes, resources, and transports. |
| `topology resolve` | Explain the owner and transport for one canonical command. |

### Connect the operator harness

| Command | Purpose |
| --- | --- |
| `harness sync openclaw` | Render, merge, or apply the OpenClaw provider integration. |
| `harness sync clients` | Reconcile local OpenClaw, isolated Hermes profiles, and Pi limits from authenticated router metadata. |
| `harness sync hermes-media` | Reconcile the packaged Anvil media skill and media-only MCP server for selected Hermes profiles. |
| `harness restart openclaw` | Restart one local or remote OpenClaw gateway. |
| `harness status openclaw` | Read bounded OpenClaw gateway status. |

### Expose the management plane

| Command | Purpose |
| --- | --- |
| `mcp tools` | List the bounded tool catalog and schemas. |
| `mcp serve` | Run local stdio MCP or proxy calls to a controller. |
| `controller serve` | Run the authenticated private HTTP controller. |
| `controller status` | Validate controller health and required capabilities. |

### Integrate read-only telemetry

| Command | Purpose |
| --- | --- |
| `collectors configure` | Normalize an adapter declaration and optionally write it. |
| `collectors validate` | Validate saved or inline configuration without network access. |
| `collectors capabilities` | Report declared capabilities offline. |
| `collectors inspect` | Perform one bounded, authenticated read. |

### Publish tailnet surfaces

| Command | Purpose |
| --- | --- |
| `edge render` | Render exact `tailscale serve` commands without applying them. |
| `edge status` | Compare live mappings with the resolved managed map. |
| `edge up` | Add or update only the resolved managed mounts. |
| `edge down` | Remove only live mounts still matching the managed targets. |

## Topology

Validate before using a topology for resolution:

```bash
anvil-serving topology validate --topology operator-topology.toml
anvil-serving topology validate --topology operator-topology.toml --topology-overlay deployments/dark.toml
```

Validation is offline. It does not contact a controller, SSH host, router, or
model serve. `show` returns the merged declaration:

```bash
anvil-serving topology show --topology operator-topology.toml
```

Use `resolve` when the important question is where one operation would run:

```bash
anvil-serving topology resolve --topology operator-topology.toml --command "host status"
anvil-serving topology resolve --topology operator-topology.toml --command "host status" --target host:dark --transport controller
```

`--command` must name a visible canonical leaf. The result records the resource
owner, runtime, transport, endpoint, capacity decision, and any override
warning, but never imports or executes the command handler. Loopback remains
host-relative; a topology never treats `127.0.0.1` on Mini as Dark.

## Harness

OpenClaw sync is a render-first workflow:

```bash
anvil-serving harness sync openclaw --config configs/example.toml --dry-run
anvil-serving harness sync openclaw --config configs/example.toml --out openclaw.json --base-url http://100.64.0.10:8000/v1 --confirm
```

The gateway configuration supplies capability aliases and their tier context limits. `--base-url`
defaults to `http://127.0.0.1:8000/v1`; when OpenClaw runs on another host, set
the router address that gateway can reach. Credential flags name environment
variables—secret values are never written into an operator command.

A local sync reads the existing output configuration, merges only Anvil-owned
provider/agent/voice keys, backs up the target, and writes it back.
`--overwrite` deliberately replaces instead. Run the command on the client
host that owns the file. Add `--voice` for the Anvil Voice Talk provider and
restart the gateway separately only after the applied target is its real
configuration.

Lifecycle and status remain separate:

```bash
anvil-serving harness restart openclaw --dry-run
anvil-serving harness restart openclaw --gateway-host mini-host.example --confirm
anvil-serving harness status openclaw
anvil-serving harness status openclaw --topology operator-topology.toml --target host:mini --json
```

Restart resolves the OpenClaw executable before invoking it. On macOS, when
the CLI is absent from the caller's non-interactive PATH, it verifies the exact
`ai.openclaw.gateway` LaunchAgent definition, fingerprints its program
arguments, restarts that label through `launchctl`, and requires loopback HTTP
health to return before success. A missing or mismatched definition fails
closed. Status is read-only, defaults to a 120-second process deadline, caps
stdout and stderr at 64 KiB each, and marks truncation explicitly.

For a model-swapping Mini, use the catalog reconciler on Mini itself:

```bash
anvil-serving harness sync clients \
  --base-url https://router.example.ts.net/v1 \
  --clients openclaw,hermes,pi \
  --hermes-profiles all \
  --restart-openclaw-on-change \
  --restart-hermes-on-change \
  --dry-run
anvil-serving harness sync clients \
  --base-url https://router.example.ts.net/v1 \
  --clients openclaw,hermes,pi \
  --hermes-profiles all \
  --restart-openclaw-on-change \
  --restart-hermes-on-change \
  --confirm
```

It authenticates using the environment variable named by `--api-key-env`,
cross-checks `/v1/router/status` against `/v1/models/capabilities`, and refuses
to write unless every routed tier declares both context and maximum output.
The command preserves provider credentials, unrelated client configuration,
and existing compaction policies; it verifies that compaction reserves fit the
smallest selected model context. With `--hermes-profiles all`, every discovered
Hermes profile is read through the Hermes CLI. Anvil-backed profiles receive
their independently routed context, maximum output, `vision.general`, and
compression-helper context. Their existing compaction threshold and target
ratio must remain enabled and safe; profiles backed by other providers are
reported but not modified. The command preserves provider credential
references, aligns the legacy Anvil provider's selected text aliases, and
removes stale alias metadata only inside the Anvil provider. It also removes
the legacy `chat_template_kwargs` request override when present because the
router contract does not accept that provider-specific field.
Changed files are atomically replaced only after a complete private backup
bundle is created. State is keyed by the
router's secret-free config hash plus full client-file hashes, so repeated runs
are no-ops while local drift is repaired. `--restart-openclaw-on-change`
restarts the gateway at most once per router config hash and retries a failed
restart on the next run. `--restart-hermes-on-change` restarts only the default
Hermes gateway and only when that active profile changed.

Install the separate bounded media capability without changing Hermes model
selection:

```bash
anvil-serving harness sync hermes-media \
  --hermes-profiles default,anvil-primary \
  --dry-run
anvil-serving harness sync hermes-media \
  --hermes-profiles default,anvil-primary \
  --confirm
anvil-serving harness sync hermes-media \
  --hermes-profiles default,anvil-primary \
  --dry-run
```

This operation installs the packaged `anvil-media` skill and one MCP server
whose catalog is restricted to the eight ordinary media tools. It stores only
the environment references named by `--mcp-url-env` and `--token-env`, backs
up every changed profile, validates through the Hermes CLI, and verifies the
installed skill digest. It never grants worker lifecycle or operator tools.
The default token reference is `ANVIL_ROUTER_TOKEN` because the media MCP URL
is the router gateway; the separate controller credential is not valid for
this caller-facing connection.
The last preview must report no changes.

Hermes profiles have independent environment files. The catalog reconciler
never copies credential values between them. Before acceptance, authenticate a
metadata-only router probe from each profile's own environment and require the
expected success status; a fallback-produced answer is not proof that the
profile reached Anvil.

## MCP

Inspect the exact management surface before connecting a client:

```bash
anvil-serving mcp tools
anvil-serving mcp tools --json
```

The catalog comes from the same declarations used by the HTTP controller. Tool
listing does not invoke a tool, read a credential, or contact a remote service.

The HTTP controller supports MCP `2026-07-28` only. Every controller request
includes protocol version, client capabilities, and optional client identity
metadata. Controller clients begin with `server/discover`; `initialize` and
`initialized` are not accepted at `/mcp`. HTTP clients send matching
`MCP-Protocol-Version` and `Mcp-Method` headers, plus `Mcp-Name` for a tool
call.

For a local operator process, run stdio MCP directly:

```bash
anvil-serving mcp serve
```

It reads newline-delimited JSON-RPC from stdin and writes protocol responses to
stdout until EOF. To keep the MCP client on one host while executing management
operations on another, proxy the tool protocol to the private controller:

```bash
anvil-serving mcp serve --controller-url http://100.64.0.10:8765 --auth-env ANVIL_CONTROLLER_TOKEN
```

The URL and token environment-variable name must be provided together. Proxy
mode requires Node.js 20+ and launches the packaged official TypeScript SDK
bridge. Its stdio side serves initialize-based clients through `2025-11-25`
and stateless `2026-07-28` clients from the same tool registrations. Its
downstream client accepts only `2026-07-28`, bearer-authenticates every
request, verifies the `anvil-serving` controller identity and exact installed
version, and fetches the controller's restricted catalog before serving a
client. A controller base URL with no path is resolved to `/mcp`.

OpenClaw stores this stdio declaration under its native `mcp.servers`
configuration. OpenClaw `2026.7.1-2` bundles MCP TypeScript SDK `1.29.0`,
advertises `2025-11-25`, and sends `initialize`; that exact client generation
is covered by the bridge regression tests. Keep the controller token in the
OpenClaw service environment. Pass `--auth-env ANVIL_CONTROLLER_TOKEN` in the
server arguments and set the server environment entry to the literal
`${ANVIL_CONTROLLER_TOKEN}` reference. OpenClaw filters ambient stdio child
environments, then resolves that explicit reference during activation. Do not
save the token value in OpenClaw's MCP JSON.

## Controller

Set the token environment variable, then start the private controller:

```bash
anvil-serving controller serve --host 127.0.0.1 --port 8765 --auth-token-env ANVIL_CONTROLLER_TOKEN
anvil-serving controller serve --host 100.64.0.10 --allow-operation host_summary --auth-token-env ANVIL_CONTROLLER_TOKEN
```

The default bind is `127.0.0.1:8765`, and all public CLI binds require the token
named by `--auth-token-env`. Private and tailnet addresses are allowed with
authentication. A public or wildcard address also requires
`--allow-public-bind`. `--allow-operation` is repeatable and reduces the served
catalog to the declared operations. `--state-db` places the durable idempotency
store at an explicit path, which the controller image maps to its named state
volume.

Probe identity and capabilities without calling a management tool:

```bash
anvil-serving controller status --url http://127.0.0.1:8765
anvil-serving controller status --url http://100.64.0.10:8765 --require-operation host_summary
```

Status performs authenticated reads of `/health` and `/tools/list`. Its request
timeout must be greater than zero and no more than 60 seconds; response capture
defaults to 64 KiB. Every repeatable `--require-operation` must be present.

## Collectors

Collectors are optional observability adapters. Start by normalizing an inline
declaration without writing it:

```bash
anvil-serving collectors configure --name local-gap --endpoint http://127.0.0.1:9100/capabilities --capability gpu-gap
```

Write only after reviewing the normalized JSON:

```bash
anvil-serving collectors configure --name local-gap --endpoint http://127.0.0.1:9100/capabilities --capability gpu-gap --output collector.json --confirm
```

Use either `--config` or inline fields, never both. Endpoints must contain an
explicit loopback, private, or tailnet IP. A non-loopback endpoint requires an
`--auth-env` name. Saved files and response bodies are each capped at 256 KiB.

The offline verbs distinguish declaration from live evidence:

```bash
anvil-serving collectors validate --config collector.json
anvil-serving collectors capabilities --config collector.json
anvil-serving collectors capabilities
```

Bare `capabilities` reports the explicit `not-configured` state. It does not
claim the external service is reachable. `inspect` is the only network read:

```bash
anvil-serving collectors inspect --config collector.json --timeout 5
```

Inspection performs one GET, disables redirects and proxies, caps the request
deadline at 60 seconds, and redacts bearer-token values. Missing capabilities
or invalid responses produce a degraded result; collectors never mutate the
services they observe.

## Edge

The edge owns only the Tailscale Serve mounts declared by Anvil. Resolve the
plan and compare live state first:

```bash
anvil-serving edge render
anvil-serving edge render --config edge.toml --map /dashboard=8766
anvil-serving edge status --config edge.toml --json
```

Configuration precedence is built-in defaults, optional `[edge]` TOML, then
repeatable `--map` overrides. `MOUNT=off` removes one resolved route. Port-only
targets use `--host`, which defaults to `127.0.0.1`; the HTTPS listener defaults
to 443. The built-in map publishes `/v1` to the router and `/comfyui` to
ComfyUI.

Preview and apply use the same resolved plan:

```bash
anvil-serving edge up --dry-run
anvil-serving edge up --config edge.toml --confirm
anvil-serving edge down --dry-run
anvil-serving edge down --config edge.toml --confirm
```

`up` is additive and idempotent. `down` removes a path only when its live target
still exactly matches the configured Anvil-owned target. It never runs
`tailscale serve reset`, so absent, changed, and operator-owned mappings remain
untouched. Each planned subprocess is attempted once with a 15-second timeout.

## Related references

- [Tailnet endpoint runbook](../TAILNET-ENDPOINT-RUNBOOK.md)
- [OpenClaw integration specification](../OPENCLAW-INTEGRATION-SPEC.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
- [Device topologies](../DEVICE-TOPOLOGIES.md)
