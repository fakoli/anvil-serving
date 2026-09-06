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
| `router diagnose` | Explain one request using bounded metadata, without replaying it. |
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

Token inspection is redacted by default:

```bash
anvil-serving router token
anvil-serving router token --reveal --confirm
```

Only the second form prints the local token value. Avoid using it in automation or
captured logs.

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
```

The command reads the router credential from `ANVIL_ROUTER_TOKEN` or the
environment variable selected by `--auth-env`. It retrieves one terminal
decision and separately labeled current build metadata using bounded GETs.
`--timeout` is a per-read socket timeout, at most 30 seconds. A missing record
does not prove the request never ran: active requests, buffer eviction, older
processes, and unsupported lookup can all explain its absence.

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

- [Capability meta-router](../META-ROUTER.md)
- [Meta-router request path](../THIN-CAPABILITY-GATEWAY.md)
- [Configuration](../CONFIGURATION.md)
- [Operator playbooks](../OPERATOR-PLAYBOOKS.md)
- [Troubleshooting](../TROUBLESHOOTING.md)
