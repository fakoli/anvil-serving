# Router

[CLI overview](../CLI.md) · [Model serves](serves.md) · [Models & recipes](models.md)

The `router` family operates the deployed OpenAI-compatible data plane. Use it
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

### Deployment lifecycle

| Command | Purpose |
| --- | --- |
| `router up` | Start the deployed router. |
| `router down` | Stop the deployed router. |
| `router restart` | Restart the deployed router. |
| `router reload` | Reload router configuration. |
| `router install-config` | Atomically install a validated direct-router config, including tier-set migrations. |
| `router status` | Show bounded router status. |
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
path selects one exact direct-alias topology. The router remains a stdlib-only
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

Token inspection is redacted by default:

```bash
anvil-serving router token
anvil-serving router token --reveal --confirm
```

Only the second form prints the local token value. Avoid using it in automation or
captured logs.

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

`--recreate` is available only for `router up`. It maps to Docker Compose
`--force-recreate` while retaining `--no-deps`, so the operation recreates only the
selected router service and does not start or recreate model services, alter router
configuration volumes, or modify the host outside Docker's requested router action.

Install a complete direct-router config with the same preview-first boundary:

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

## Related references

- [Thin capability gateway](../THIN-CAPABILITY-GATEWAY.md)
- [Configuration](../CONFIGURATION.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
- [Troubleshooting](../TROUBLESHOOTING.md)
