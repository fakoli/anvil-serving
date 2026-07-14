# Router

[CLI overview](../CLI.md) · [Model serves](serves.md) · [Models & recipes](models.md)

The `router` family operates the deployed OpenAI-compatible data plane. Use it
to run the router directly, manage its service lifecycle, inspect its endpoint,
and perform guarded tier transitions.

## Commands

| Command | Purpose |
| --- | --- |
| `router run` | Run the router in the foreground. |
| `router up` | Start the deployed router. |
| `router down` | Stop the deployed router. |
| `router restart` | Restart the deployed router. |
| `router reload` | Reload router configuration. |
| `router promote` | Promote a reviewed router configuration. |
| `router endpoint` | Show the listen address, port, and this node's Tailscale DNS name. |
| `router status` | Show bounded router status. |
| `router transition-status` | Show current tier-transition state. |
| `router quiesce` | Stop admitting work to one router tier. |
| `router drain` | Wait for a quiesced tier to drain. |
| `router readmit` | Safely return one tier to service. |
| `router logs` | Read bounded router logs. |
| `router token` | Inspect router-token state without printing the token. |

## Run the router

```bash
anvil-serving router run --config configs/example.toml
anvil-serving router run --mode agentic --host 127.0.0.1 --port 8000
```

Configuration can come from `--config` or the environment. The router remains a
stdlib-only foreground service; use the lifecycle commands when the deployment is
managed by the operator substrate.

## Inspect the deployment

```bash
anvil-serving router status
anvil-serving router endpoint
anvil-serving router logs
anvil-serving --json router status
```

`router endpoint` reports the configured listen address and port. When available,
it also reports the current node's Tailscale DNS name; it does not change routing
or tailnet configuration.

## Lifecycle and promotion

```bash
anvil-serving router up --dry-run
anvil-serving router up --confirm
anvil-serving router reload --confirm
anvil-serving router promote --profile ./candidate-profile.json --dry-run
anvil-serving router promote --profile ./candidate-profile.json --confirm
```

Lifecycle mutations are guarded. Preview them first when `--dry-run` is available,
then repeat with `--confirm`. Promotion never substitutes for the independent human
quality gate.

## Tier transitions

A safe tier transition is explicit:

```bash
anvil-serving router quiesce --tier heavy-local --dry-run
anvil-serving router quiesce --tier heavy-local --confirm
anvil-serving router transition-status --tier heavy-local
anvil-serving router drain --tier heavy-local --timeout 120
anvil-serving router readmit --tier heavy-local --confirm
```

Use `transition-status` between steps. The commands preserve the distinction between
stopping new admissions, waiting for active work, and returning a tier to service.

## Related references

- [Quality-gated router](../QUALITY-GATED-ROUTER.md)
- [Configuration](../CONFIGURATION.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
- [Troubleshooting](../TROUBLESHOOTING.md)
