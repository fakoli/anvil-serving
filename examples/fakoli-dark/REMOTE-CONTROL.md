# Operate Fakoli Dark from Fakoli Mini

This runbook covers the deployed split-host control plane:

- **Fakoli Mini** runs the `anvil-serving` CLI, the MCP stdio bridge, and
  OpenClaw.
- **Fakoli Dark** runs the hardened Linux controller container, Docker model
  serves, the router, and both GPUs.
- **Tailscale Serve** carries controller traffic. Mini never receives Dark's
  Docker socket.

The Dark controller supports MCP `2026-07-28` only. The packaged Mini-side
TypeScript bridge accepts both the initialize-based era through `2025-11-25`
and stateless `2026-07-28`, then uses `2026-07-28` downstream. Dark does not
implement the removed `initialize` lifecycle.

## Current status

The deployment is ready for direct Anvil CLI and OpenClaw use from Mini.
OpenClaw `2026.7.1-2` can use its initialize-based MCP client against the
Mini-side bridge without exposing a legacy network endpoint on Dark.

```mermaid
flowchart LR
    CLI["anvil-serving CLI on Mini"] --> TCP["Tailnet TCP 8766"]
    MCP["OpenClaw or MCP 2026 client on Mini"] --> B["Packaged stdio bridge"]
    B --> HTTPS["Tailnet HTTPS /anvil-controller"]
    TCP --> C["Controller container on Dark"]
    HTTPS --> C
    C --> D["Docker socket"]
    C --> G["Both Dark GPUs"]
    C --> R["Router and model serves"]
```

## Use it now from Mini

SSH to Mini and load the owner-only service environment into the current
shell. This exports the existing token; it does not print it:

```bash
ssh mini-host.example
set -a
source "$HOME/.openclaw/service-env/ai.openclaw.gateway.env"
set +a
```

Confirm the installed topology and controller:

```bash
anvil-serving topology validate
anvil-serving controller status \
  --url http://100.64.0.10:8766 \
  --auth-token-env ANVIL_CONTROLLER_TOKEN
```

The private topology at
`~/.anvil-serving/operator-topology.toml` already declares Mini as the command
host, Dark's container as the execution runtime, and the controller transport.
Normal commands can therefore execute on Dark:

```bash
anvil-serving router status --transport controller
anvil-serving router transition-status --transport controller
anvil-serving router logs --tail 50 --transport controller

anvil-serving serves status --transport controller
anvil-serving serves mode status --transport controller
anvil-serving serves logs primary --tail 50 --transport controller

anvil-serving host gpus \
  --target host:fakoli-dark \
  --transport controller
```

Add `--json` to any command for a machine-readable envelope that records the
command host, resource owner, execution runtime, transport, and result.

## Preview before mutation

Remote lifecycle commands retain their normal safety gates. Preview the exact
operation first:

```bash
anvil-serving router restart --dry-run --transport controller
anvil-serving serves up primary --dry-run --transport controller
anvil-serving serves down primary --dry-run --transport controller
anvil-serving serves mode preview <tp2-serve> \
  --restore-group split-stack \
  --transport controller

anvil-serving voice audio up \
  --config /etc/anvil/voice.toml \
  --profile dark-audio \
  --dry-run \
  --transport controller

anvil-serving eval preflight \
  --base-url http://127.0.0.1:8000/v1 \
  --model agents-a1-fp8-mm-262k \
  --api-key-env ANVIL_ROUTER_TOKEN \
  --checks smoke \
  --dry-run \
  --transport controller
```

In controller operations, `127.0.0.1` is rewritten to the explicit
container-to-Dark alias. It does not point to Mini.

Apply only after reviewing the preview and accepting the affected serve:

```bash
anvil-serving router restart --confirm --transport controller
anvil-serving serves up primary --confirm --transport controller
```

Live `serves mode enter|leave` additionally requires the controller tool's
`human_approved=true` gate after the structured preview. Do not use
`--confirm` speculatively. Promotion, native Windows repair,
Docker/WSL restart, cache deletion, GitHub publication, and SSH-authenticated
work remain outside this controller.

## Router transition sequence

Transition commands require an exact tier:

```bash
anvil-serving router quiesce \
  --tier primary-local \
  --dry-run \
  --transport controller

anvil-serving router transition-status --transport controller
```

`router drain` is meaningful only after the tier has actually been quiesced.
A dry-run quiesce does not change admission state, so a subsequent drain may
correctly report that the tier is still admitting. A live quiesce/readmit
sequence is a maintenance action and requires explicit confirmation.

## MCP client configuration

OpenClaw's initialize-based client and an MCP `2026-07-28` client can launch
the same Mini-side stdio bridge. Remote controller mode requires Node.js 20 or
newer; the bridge is bundled in the Python package and does not require an
`npm install` on Mini:

```bash
anvil-serving mcp serve \
  --controller-url \
  https://fakoli-dark.<tailnet>.ts.net/anvil-controller/mcp \
  --auth-env ANVIL_CONTROLLER_TOKEN
```

The rebuilt dual-PRO reference controller exposes 16 restricted tools:

```text
operation_contracts
router_status, router_logs, router_manage, router_transition, decision_summary
serves_status, reservation_status, serves_manage, serves_mode, serves_logs
voice_manage, gpu_inventory
preflight_probe, benchmark_probe, workflow_packet_validate
```

OpenClaw's saved `mcp.servers.anvil-serving` entry uses this command:

```bash
openclaw mcp configure anvil-serving --enable
openclaw mcp status --json
openclaw mcp probe anvil-serving
```

The OpenClaw gateway service must inherit `ANVIL_CONTROLLER_TOKEN` from its
owner-only service environment. Because OpenClaw filters ambient stdio child
environments, the saved server must also map the variable to a literal
reference:

```json
{
  "env": {
    "ANVIL_CONTROLLER_TOKEN": "${ANVIL_CONTROLLER_TOKEN}"
  }
}
```

The reference is safe to store; the token value is not. The value is never
stored in the MCP server arguments or `openclaw.json`.

## One-time Dark setup

From an updated Anvil Serving checkout on Dark:

```powershell
$env:ANVIL_CONTROLLER_TOKEN = '<load from secret storage>'
docker compose `
  -f examples/fakoli-dark/docker-compose.controller.yml `
  up -d --wait --build controller

tailscale serve --bg `
  --set-path=/anvil-controller `
  http://127.0.0.1:8765

tailscale serve --bg `
  --tcp=8766 `
  tcp://127.0.0.1:8765

tailscale serve status
```

The HTTPS path is for MCP clients. The tailnet TCP listener gives the typed CLI
controller transport a literal `100.64.0.0/10` endpoint while Tailscale still
encrypts and restricts the connection to the tailnet.

The container mounts only declared manifests, its controller topology, the
Docker socket, and durable operation state. It does not mount a user home,
`.ssh`, GitHub CLI configuration, or raw GitHub credentials.

## Validation coverage

The table below is the pre-upgrade live record from Mini on 2026-07-29. It
predates `serves_mode`; rebuilding and revalidating the controller is a
separate live mutation and is not claimed by this topology change.

| Surface | Result |
| --- | --- |
| Controller discovery | Healthy; 15 restricted tools (pre-dual-PRO build) |
| MCP `server/discover` and `tools/list` | Passed with `2026-07-28` |
| All 15 controller tools | Exercised successfully as a live read or non-mutating preview |
| Direct typed CLI matrix | 19 passed; 3 state-dependent refusals described below |
| Router status, logs, lifecycle previews | Passed |
| Serve status, primary logs, lifecycle previews | Passed |
| GPU inventory | Both Dark GPUs visible |
| Preflight and capacity benchmark previews | Passed without sending model requests |
| Voice lifecycle preview | Passed |
| Voice status/logs | Correctly report unavailable while STT/TTS containers are absent |
| Live lifecycle mutation | Not exercised; human confirmation intentionally withheld |
| OpenClaw MCP probe | Passed through the Mini-side dual-era bridge |
| OpenClaw SDK tool calls | `router_status` and `gpu_inventory` passed against Dark |

This is not a claim that every command in the entire Anvil Serving CLI is
remote-capable. Only operations declared in the controller catalog and Mini's
topology are remote. Local-only recipe authoring, publication, native host
repair, promotion, and other excluded commands remain on their owning host.

## Troubleshooting

Check the path in this order:

```bash
tailscale status
anvil-serving topology validate
anvil-serving controller status \
  --url http://100.64.0.10:8766 \
  --auth-token-env ANVIL_CONTROLLER_TOKEN
anvil-serving router status --transport controller --json
```

- `missing_controller_token`: load the owner-only service environment.
- No controller transport: validate
  `~/.anvil-serving/operator-topology.toml`.
- Router drain refuses: quiesce the exact tier first; a preview is not a
  quiesce.
- Voice status/logs return unavailable: bring up the declared audio profile or
  treat the absent containers as the current expected state.
- OpenClaw reports `Connection closed`: confirm Node.js 20+ is on the gateway
  service `PATH`, the controller token is present in the owner-only service
  environment, the server `env` map contains the literal
  `${ANVIL_CONTROLLER_TOKEN}` reference, and the installed Mini package
  version exactly matches Dark's controller version. Then run
  `openclaw mcp reload` and `openclaw mcp probe anvil-serving`.
- OpenClaw `2026.7.1-2` doctor warns that the resolved token reference is a
  literal sensitive value: this is a version-specific false positive if the
  raw `openclaw.json` still contains only `${ANVIL_CONTROLLER_TOKEN}` and is
  mode `0600`. Do not print the resolved value while checking.
