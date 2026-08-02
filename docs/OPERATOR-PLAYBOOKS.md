# Operator playbooks

Use the control plane to operate named local serves and direct capability aliases. The router
does not infer intent, choose between models, or promote benchmark results.

`anvil-serving serves` owns lifecycle for declared local model processes. `anvil-serving eval`
checks and benchmarks one explicit endpoint. Both surfaces produce evidence for an operator;
neither selects a model at request time.

## Read-only inventory

1. Inspect the router and serving substrate:

   ```bash
   anvil-serving doctor --config <router.toml>
   anvil-serving router status
   anvil-serving serves status --manifest <serves.toml>
   anvil-serving serves mode status --manifest <serves.toml>
   anvil-serving serves logs <serve-name> --manifest <serves.toml> --tail 200
   ```

2. Confirm the caller alias in `[router.model_routes]` maps to the intended local tier.
3. Inspect `GET /v1/models` and `GET /v1/decisions` through the authenticated router when
   diagnosing discovery or request metadata.

The MCP equivalents are `doctor_summary`, `router_status`, `serves_status`,
`reservation_status`, `serves_mode`, and `decision_summary`.

## Start, validate, and benchmark a serve

1. Preview the exact lifecycle operation, then repeat with explicit confirmation:

   ```bash
   anvil-serving serves up <serve-name> --manifest <serves.toml> --dry-run
   anvil-serving serves up <serve-name> --manifest <serves.toml> --confirm
   ```

2. Run functional preflight directly against the served model:

   ```bash
   anvil-serving eval preflight \
     --base-url http://127.0.0.1:<port>/v1 \
     --model <served-model> --confirm
   ```

3. Record benchmark evidence only after preflight passes. Capture the model revision, engine,
   quantization, context, concurrency, hardware, failures, and raw artifact path. Publish a
   dated finding under `docs/findings/` and update the
   [benchmark portal](benchmarks/index.md) when the outcome changes a current recommendation
   or reference deployment.

4. Promote or roll back only through the guarded `serves promote` transaction with explicit
   human approval. A benchmark never updates an alias or serve automatically. See
   [Promote and roll back](MODEL-PROMOTION.md) for the full transaction, the quiesce/drain/
   readmit sequence, and what makes a rollback target real.

Use the transition commands to quiesce and drain a local tier before an operator-approved
serving change.

## Enter or leave exclusive TP=2 mode

The candidate must already be declared with both GPU roles,
`operating_mode = "dual-gpu-exclusive"`, and `tensor_parallel_size = 2`. A
routed target additionally declares its exact `router_tier`, complete
`router_config`, and complete `rollback_router_config`; an unrouted experiment
omits all three.
Preview the full blast radius first:

```bash
anvil-serving serves mode preview <tp2-serve> \
  --restore-group split-stack --manifest <serves.toml>
anvil-serving serves mode enter <tp2-serve> \
  --restore-group split-stack --manifest <serves.toml> --confirm
```

Entry quiesces and drains routed competitors, stops all active GPU inference,
rechecks both role ledgers, and only then starts the TP=2 owner. For a routed
target it then installs the declared complete router profile and guardedly
readmits the target tier, so success means the declared alias is actually
routable. A failed start, profile install, or readmission restores both the
rollback router profile and named split group. While exclusive mode is active,
ordinary serve starts and ad-hoc Compose experiments fail before a container
command. Other aliases whose backing serves are offline return unavailable;
they never fall back to the TP=2 serve.

Leave in the reverse order:

```bash
anvil-serving serves mode leave <tp2-serve> \
  --restore-group split-stack --manifest <serves.toml> --confirm
```

Leave quiesces and drains the routed exclusive tier before stopping it, installs
the rollback profile, restores the split group, and guardedly readmits its
tiers. The `serves_mode` controller tool returns a structured plan. Live `enter` or
`leave` additionally requires `confirm=true`, `dry_run=false`, and
`human_approved=true`. Selecting or qualifying the first TP=2 model is a
separate benchmark and promotion decision.

## Direct aliases

The router's `[router.model_routes]` maps a caller-facing alias to exactly one local tier.
Update that mapping only as an explicit configuration change, and only after the target serve
carries the required evidence. The gateway returns 404 for an unknown alias and an error for a
configured alias whose local tier cannot serve; it never falls back to another model.

## Voice topology

In the reference topology, Fakoli Dark owns LLM, STT, and TTS serves. Fakoli Mini owns the
OpenClaw Gateway and realtime proxy only. Use `voice audio` and `voice proxy` commands with an
explicit topology file; loopback addresses are host-relative.

## Operate Dark from Mini

Build and start the restricted controller on Fakoli Dark from the repository
root. Keep the token in the process environment or a host secret manager:

```powershell
$env:ANVIL_CONTROLLER_TOKEN = '<generated secret>'
docker compose -f examples/fakoli-dark/docker-compose.controller.yml build controller
docker compose -f examples/fakoli-dark/docker-compose.controller.yml up -d --wait controller
anvil-serving controller status --url http://127.0.0.1:8765
```

The deployment publishes only `127.0.0.1:8765`. Expose that loopback listener
to the tailnet from the Dark host, not from the container:

```powershell
tailscale serve --bg --set-path=/anvil-controller http://127.0.0.1:8765
tailscale serve status
```

On Fakoli Mini, install the same Anvil Serving revision and provide the same
token to the OpenClaw gateway's owner-only service environment. Register the
stdio bridge through OpenClaw's current `mcp.servers` surface:

```bash
openclaw mcp add anvil-serving \
  --command /Users/<operator>/.local/bin/anvil-serving \
  --arg mcp --arg serve \
  --arg=--controller-url \
  --arg https://fakoli-dark.<tailnet>.ts.net/anvil-controller/mcp \
  --arg=--auth-env \
  --arg ANVIL_CONTROLLER_TOKEN \
  --env 'ANVIL_CONTROLLER_TOKEN=${ANVIL_CONTROLLER_TOKEN}' \
  --no-probe
openclaw mcp probe anvil-serving
openclaw mcp doctor
```

The Mini-side process is a model-free stdio bridge using the packaged official
TypeScript MCP SDK. Its client-facing side accepts the initialize era through
`2025-11-25` and stateless `2026-07-28`; its controller-facing side is pinned
to `2026-07-28` and forwards the dynamically registered restricted tool
catalog to Dark's `/mcp` endpoint. OpenClaw deliberately filters the ambient
environment of stdio children, so the server declaration must include the
literal reference `${ANVIL_CONTROLLER_TOKEN}` in its `env` map. OpenClaw
resolves that reference from the gateway service environment when it activates
the server. Never put the token value itself in `openclaw.json`.

OpenClaw `2026.7.1-2` expands the reference before its `mcp doctor` credential
heuristic runs, so doctor may warn that the resolved entry contains a literal
sensitive value even when the raw owner-only JSON still stores only the
`${ANVIL_CONTROLLER_TOKEN}` reference. Treat that version-specific warning as
expected only after confirming the raw reference and `0600` file permissions;
never print the resolved config value while checking it.

The native `mcp.servers` layout and the client's wire protocol are separate
compatibility gates. The bridge test suite exercises the exact
`@modelcontextprotocol/sdk` `1.29.0` generation bundled by OpenClaw
`2026.7.1-2`, plus a modern SDK `2.0.0` client. Dark remains modern-only in
both cases.

The example controller includes Docker-compatible router, serve, voice,
inventory, preflight, benchmark-probe, and workflow-validation tools. It
excludes native-host management, OpenClaw gateway lifecycle, promotion,
artifact publication, and experimental manifests that are not mounted.
SSH remains an explicit break-glass transport for native-only work; it is not
an automatic fallback from controller errors.

The Docker socket is intentionally writable because lifecycle operations need
it. Do not mount a user home, `.ssh`, GitHub CLI config, or the whole operator
configuration directory into this container. Consequently, GitHub- or
SSH-authenticated actions are unavailable inside it; perform those on an
operator host or add a separately reviewed, narrowly scoped credential path.

## OpenClaw sync

Render or apply OpenClaw provider configuration through `harness sync openclaw`. The generated
provider names the router base URL, a token environment variable, and a direct model alias.
Do not use a decision endpoint or plugin classifier.

## Evidence and stop conditions

Keep the following together for a serve decision: manifest/config identity, health, preflight
output, benchmark artifact, failures, and rollback plan. Stop and ask for direction when the
target alias, local tier, topology owner, or promotion authority is ambiguous.
