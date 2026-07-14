# Control plane & integrations

[CLI overview](../CLI.md) · [Host & setup](host.md) · [Router](router.md)

These commands describe where operations run and expose bounded integrations around
the router. The router itself remains in-process; it does not require the controller,
MCP, or a daemon to route requests.

## Commands

| Family | Commands | Purpose |
| --- | --- | --- |
| Topology | `topology show`, `validate`, `resolve` | Validate deployment ownership and resolve commands. |
| Harness | `harness sync`, `restart`, `status` with `openclaw` leaves | Manage harness integration. |
| MCP | `mcp serve`, `mcp tools` | Expose and inspect bounded management tools. |
| Controller | `controller serve`, `controller status` | Run or probe the private controller. |
| Collectors | `collectors configure`, `validate`, `capabilities`, `inspect` | Configure and inspect optional read-only adapters. |
| Tailnet edge | `edge render`, `status`, `up`, `down` | Own declared Tailscale Serve mappings. |

## Topology

```bash
anvil-serving topology validate --topology ~/.anvil-serving/operator-topology.toml
anvil-serving topology show --topology ~/.anvil-serving/operator-topology.toml
anvil-serving topology resolve --topology ~/.anvil-serving/operator-topology.toml --command "router status"
```

Use `--topology` and, when needed, `--topology-overlay` as global options. Resolution
identifies the resource owner before execution; it does not infer that loopback on one
host refers to a service on another.

## Harness

```bash
anvil-serving harness sync openclaw --help
anvil-serving harness status openclaw
anvil-serving harness restart openclaw --help
```

Harness commands keep integration configuration and lifecycle behind the supported CLI
surface. They do not install model workloads on a host that the topology declares
model-free.

## MCP

```bash
anvil-serving mcp tools
anvil-serving mcp serve
```

`mcp tools` lists the bounded management surface. `mcp serve` runs the bridge in the
foreground. Tool results follow the same resource-owner and confirmation rules as the
corresponding CLI operations.

## Controller

```bash
anvil-serving controller status
anvil-serving controller serve
```

The private controller provides remote execution where declared by topology. A proven
pre-dispatch controller failure may use verified SSH recovery only when
`--allow-ssh-fallback` is explicit.

## Collectors

```bash
anvil-serving collectors configure --name local-gap --endpoint http://127.0.0.1:9100/capabilities --capability gpu-gap
anvil-serving collectors configure --name local-gap --endpoint http://127.0.0.1:9100/capabilities --capability gpu-gap --output ./collector.json --confirm
anvil-serving collectors validate --config ./collector.json
anvil-serving collectors capabilities --config ./collector.json
anvil-serving collectors inspect --config ./collector.json
```

Collectors are optional, read-only observability adapters. They do not own or mutate
the services they inspect. `configure --output PATH` is the only leaf that writes a
collector configuration; preview the validated JSON without `--output` first.

## Edge

```bash
anvil-serving edge render
anvil-serving edge status
anvil-serving edge up --dry-run
anvil-serving edge up --confirm
anvil-serving edge down --confirm
```

The edge family manages only the Tailscale Serve mappings declared as anvil-owned.
`up` is additive and idempotent; `down` removes only those managed mounts. Render and
inspect before applying changes.

## Related references

- [Tailnet endpoint runbook](../TAILNET-ENDPOINT-RUNBOOK.md)
- [OpenClaw integration specification](../OPENCLAW-INTEGRATION-SPEC.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
- [Device topologies](../DEVICE-TOPOLOGIES.md)
