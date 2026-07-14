# Control plane & integrations

[CLI overview](../CLI.md) · [Host & setup](host.md) · [Router](router.md)

Use these families to declare where operations belong, connect an operator
harness, expose the bounded management protocol, and publish reviewed tailnet
routes. None of them sits in the router request path: routing remains an
in-process data-plane operation.

## Choose a workflow

| Goal | Start here | Then |
| --- | --- | --- |
| Check deployment ownership | `topology validate` | Use `topology show` for the declaration or `topology resolve` for one command. |
| Update OpenClaw integration | `harness sync openclaw --dry-run` | Apply with `--confirm`, then check `harness status openclaw`. |
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
anvil-serving harness sync openclaw --config configs/example.toml --gateway-host fakoli-mini --base-url http://100.87.34.66:8000/v1 --skills --confirm
```

The router configuration supplies presets and context limits. `--base-url`
defaults to `http://127.0.0.1:8000/v1`; when OpenClaw runs on another host, set
the router address that gateway can reach. Credential flags name environment
variables—secret values are never written into an operator command.

A remote sync uses strict-host-key OpenSSH, reads the existing configuration,
merges only Anvil-owned provider/agent/skill/voice keys, backs up the target,
and writes it back. `--overwrite` deliberately replaces instead. Add `--voice`
for the Anvil Voice Talk provider and `--restart` only when the applied target
is the gateway's real configuration.

Lifecycle and status remain separate:

```bash
anvil-serving harness restart openclaw --dry-run
anvil-serving harness restart openclaw --gateway-host fakoli-mini --confirm
anvil-serving harness status openclaw
anvil-serving harness status openclaw --topology operator-topology.toml --target host:mini --json
```

Restart issues one bounded command. Status is read-only, defaults to a
120-second process deadline, caps stdout and stderr at 64 KiB each, and marks
truncation explicitly.

## MCP

Inspect the exact management surface before connecting a client:

```bash
anvil-serving mcp tools
anvil-serving mcp tools --json
```

The catalog comes from the same declarations used by the HTTP controller. Tool
listing does not invoke a tool, read a credential, or contact a remote service.

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
mode accepts only loopback/private/tailnet controller URLs, forwards only
`tools/list` and `tools/call`, and verifies that the remote catalog is a valid
subset of the local contracts.

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
catalog to the declared operations.

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
