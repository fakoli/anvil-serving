# Operator Playbooks

This document is the operator-skill layer described by
[ADR-0013](adr/0013-openclaw-layers-and-mcp-control-plane.md). It tells a
lightweight agent how to run deterministic model-swap and benchmarking workflows
without becoming the system of record for routing decisions.

The playbooks are intentionally procedural. The agent chooses a documented
workflow, fills explicit tool arguments, records evidence, and stops at the
human promotion gate. It does not infer a new routing policy from vibes, it does
not self-verify model output, and it does not silently edit the router's live
profile.

## Current surface

ADR-0013 calls for an MCP control plane, and ADR-0014 adds the split-host
transport. There are two operator entry points:

- Same-host operation: `anvil-serving mcp --list-tools` exposes the bounded
  stdio tool surface for status, route probes, OpenClaw sync, preflight, and
  benchmark probes.
- Split-host operation: the anvil-serving host runs
  `anvil-serving controller serve`, and `fakoli-mini` runs the MCP bridge with
  `anvil-serving mcp --controller-url ... --auth-env ANVIL_CONTROLLER_TOKEN`.
  The bridge presents the same tool names while sending calls to the controller
  over the private tailnet.

The MCP surface is not yet a complete replacement for every CLI operation:
model inventory, serve start/swap, direct multiplexer inspection, JSON benchmark
artifact capture, and router promotion still use the CLI or HTTP contracts
below.

Prefer MCP tools when a current tool exists because they return structured
results and keep mutating/probe operations behind explicit `confirm` fields.
When a tool is missing, use the CLI equivalent on the host that owns the
resource and keep the same gate semantics.

| Operator need | Preferred MCP/controller shape | CLI/HTTP equivalent today |
|---|---|---|
| Controller readiness | Health endpoint on the controller's private address | `GET /health` on `http://anvil-gpu.tailnet.example:8765` |
| Model inventory | Not exposed yet | `anvil-serving models sync --out ./model-library` |
| Environment and tier health | `doctor_summary`, `serves_status`, `router_status` | `anvil-serving doctor --config ./router.toml`; `anvil-serving serves --manifest ./serves.toml status`; `anvil-serving router status` |
| Route-decision probe | `route_decision` | `POST /v1/route` on the router front door |
| Start or restore compose-defined serves | Not exposed yet | `anvil-serving serves --manifest ./serves.toml up <name>` |
| Start an experiment serve | Not exposed yet | `anvil-serving serves up --compose <compose.yml> <service>` |
| Probe a multiplexer endpoint | Not exposed yet | `GET /healthz`; `GET /v1/models` on the multiplexer base URL |
| Correctness gate | `preflight_probe` | `anvil-serving preflight --base-url http://127.0.0.1:30000/v1 --model <served-name>` |
| Throughput run | `benchmark_probe` for a bounded probe; CLI when `--json-out` is required | `anvil-serving benchmark --base-url http://127.0.0.1:30000/v1 --model <served-name> --json-out <file>` |
| OpenClaw config sync | `openclaw_sync`, `openclaw_gateway_restart` | `anvil-serving harness sync openclaw --config <router.toml> ...`; `anvil-serving harness restart openclaw ...` |
| Human-gated promotion | Not exposed yet | `anvil-serving router promote --profile <candidate.json> [--config <candidate.toml>]` |

Treat missing MCP tools as a product gap, not a reason to scrape Docker output
or hand-edit remote configs. Use `127.0.0.1` in local URLs.

MCP invocation rules:

- Start by listing tools (`anvil-serving mcp --list-tools`) or using the
  client-provided tool registry; do not assume future tools exist.
- In split-host mode, start the same remote bridge the operator will use for the
  run and let the MCP client issue `tools/list` through that bridge:

  ```bash
  anvil-serving mcp \
    --controller-url http://anvil-gpu.tailnet.example:8765 \
    --auth-env ANVIL_CONTROLLER_TOKEN
  ```

- For `preflight_probe`, `benchmark_probe`, and `openclaw_sync`, call once with
  `confirm:false` or `dry_run:true` to preview the command/result shape, then
  call with `confirm:true` only after the exact endpoint, model, config, and
  target host are known.
- For authenticated probes, pass `api_key_env` such as `ANVIL_ROUTER_TOKEN`;
  never pass a literal token value through MCP arguments, command previews, or
  saved evidence.
- For controller transport, `--auth-env ANVIL_CONTROLLER_TOKEN` names the
  environment variable containing the controller token. The token value must be
  present on both the controller host and `fakoli-mini`, but it must not appear
  in tool arguments, command previews, logs, or saved evidence.
- Treat a successful command preview as planning evidence only, not as
  preflight, benchmark, or sync evidence.
- Preserve returned structured data and the equivalent command line in the
  operator report. When a call crosses the controller, also preserve the
  controller request id or audit-log reference if one is returned.

## Controller transport

Use this when the operator or OpenClaw gateway is on `fakoli-mini` and the
anvil-serving CLI, router config, serves manifests, and GPU-local operations
live on another private host.

1. On the anvil-serving host, bind the controller to a private Tailscale DNS
   name/address or to `127.0.0.1` for single-host local development. Do not bind
   it to a public interface.

   ```bash
   export ANVIL_CONTROLLER_TOKEN="<generate-and-store-out-of-band>"
   anvil-serving controller serve \
     --host anvil-gpu.tailnet.example \
     --port 8765 \
     --auth-token-env ANVIL_CONTROLLER_TOKEN
   ```

   Local-only development uses the same command with `--host 127.0.0.1`.

2. Before running remote operations, check the controller health endpoint on the
   same private address the bridge will use:

   ```bash
   curl -fsS \
     -H "Authorization: Bearer $ANVIL_CONTROLLER_TOKEN" \
     http://anvil-gpu.tailnet.example:8765/health
   ```

   This proves the management plane is reachable. It does not prove router tier
   health; run `doctor_summary`, `serves_status`, and `router_status` for that.

3. On `fakoli-mini`, start the MCP bridge with the controller URL and token env
   var name:

   ```bash
   export ANVIL_CONTROLLER_TOKEN="<same-secret-as-controller-host>"
   anvil-serving mcp \
     --controller-url http://anvil-gpu.tailnet.example:8765 \
     --auth-env ANVIL_CONTROLLER_TOKEN
   ```

4. Treat the controller audit log as operational evidence. It should show the
   operation name, target host, dry-run/confirm state, result status, and request
   id, but never credential values. A mutating tool without a preceding preview
   is a process violation even if it succeeds.

## Skill contract

The operator skill should accept a bounded request such as:

- "swap fast to `<model>` and benchmark it"
- "preflight the heavy tier after a serve restart"
- "sync OpenClaw after this router config changed"
- "produce promotion evidence for `<candidate profile>`"

It should return:

- the inventory it observed;
- the exact serve/router/harness target it acted on;
- preflight result and benchmark artifact paths;
- any config-sync diff or destination;
- a recommendation, if requested; and
- a hard statement that no router profile or routing policy was promoted unless
  a human explicitly authorized that gate.

It must stop and ask for a human decision before:

- changing `[router].profile_path` or running `router promote`;
- changing `decision`, `decision_for_score`, or profile threshold semantics;
- enabling an opt-in metered cloud tier;
- binding a controller, router, serve, or multiplexer beyond loopback without
  private/tailnet bind and token/auth confirmation;
- using raw `docker`, `ssh`, or file edits where an Anvil verb exists; or
- treating external benchmark rows as routing-quality evidence.

## Playbook A: inventory and readiness

Use this before any swap, benchmark, or harness-sync operation.

1. Capture model inventory.

   ```bash
   anvil-serving models sync --out ./model-library
   ```

   Read the generated `INDEX.md` or structured MCP result for model id, weight
   format, loadability, context window, quantization, and thinking defaults.

2. Capture environment and live topology.

   ```bash
   anvil-serving doctor --config ./router.toml
   anvil-serving serves --manifest ./serves.toml status
   anvil-serving router status
   ```

   In split-host mode, first prove the controller itself is reachable from
   `fakoli-mini`:

   ```bash
   curl -fsS \
     -H "Authorization: Bearer $ANVIL_CONTROLLER_TOKEN" \
     http://anvil-gpu.tailnet.example:8765/health
   ```

   Then use the MCP bridge to call `doctor_summary`, `serves_status`, and
   `router_status` through the controller rather than running host-local CLI
   commands on the gateway box.

   If `./router.toml` or `./serves.toml` is not the active deployment, the skill
   must first identify the intended config/manifest from the operator request or
   ask for it.

3. If the target is a multiplexer-backed endpoint, inspect the endpoint before
   forcing a swap:

   ```bash
   curl -s http://127.0.0.1:30001/healthz
   curl -s http://127.0.0.1:30001/v1/models
   ```

   `/v1/models` lists candidates without loading them. A later preflight or
   benchmark request for a different `model` is what causes the multiplexer to
   load or swap the resident backend.

4. Record blockers exactly. Common blockers are missing manifests, unhealthy
   tier health checks, unknown model ids, a stopped router, unavailable Docker,
   an occupied port, or a model that `models sync` marks as not loadable by the
   intended engine.

## Playbook B: start or swap a serve

Use the least disruptive mechanism that matches the deployment.

### Compose-defined serve

1. Dry-run when the action is not just restarting a stopped known container.

   ```bash
   anvil-serving serves --manifest ./serves.toml --dry-run up <serve-name>
   ```

2. Start the serve.

   ```bash
   anvil-serving serves --manifest ./serves.toml up <serve-name>
   ```

3. Re-check status and logs only if needed.

   ```bash
   anvil-serving serves --manifest ./serves.toml status
   anvil-serving serves --manifest ./serves.toml logs <serve-name> --tail 200
   ```

### Experiment serve

For a model not yet in the manifest, use the checked-in experiment compose file
or an operator-supplied compose file. Do not invent a raw `docker run` command.

```bash
anvil-serving serves up --compose examples/fakoli-dark/docker-compose.experiment.yml <service>
```

The skill must record the model id, served name, GPU target, port, engine, and
any extra serve flags from the compose environment. If those inputs are
ambiguous, stop before starting the experiment.

### Multiplexer-backed endpoint

The multiplexer swaps on the first OpenAI-compatible request whose `model`
differs from the resident model. Do not claim a separate manual swap verb unless
an MCP wrapper actually exists.

1. Confirm the target model appears in `/v1/models`.
2. Run `preflight` against the multiplexer base URL with that model id.
3. Treat a clean preflight as both the correctness gate and evidence that the
   swap/load path succeeded.

## Playbook C: preflight then benchmark

Never benchmark first. A fast model that fails preflight is not a promotion
candidate.

1. Resolve the endpoint and model id.

   Use the serve manifest, router config, or explicit operator input. For direct
   local tier checks, the usual endpoints are:

   - heavy: `http://127.0.0.1:30000/v1`
   - fast: `http://127.0.0.1:30001/v1`

2. Run preflight.

   ```bash
   anvil-serving preflight \
     --base-url http://127.0.0.1:30000/v1 \
     --model <served-name> \
     --needle-ctx 60000
   ```

   Add `--no-thinking` only when the served model uses chat-template thinking
   defaults that should be disabled for this test. Do not use that flag as a
   generic fix for models whose reasoning is controlled by another mechanism.

3. If preflight fails, stop the workflow. Report the failing check and do not
   run benchmark except by explicit human request for diagnostic purposes.

4. Run benchmark and write a machine-readable artifact.

   ```bash
   anvil-serving benchmark \
     --base-url http://127.0.0.1:30000/v1 \
     --model <served-name> \
     --burst 20 \
     --json-out .anvil/benchmarks/<served-name>-benchmark.json
   ```

   The current `benchmark_probe` MCP tool is useful for a bounded structured
   probe, but it does not expose `--json-out`. Use the CLI path when the
   promotion packet needs a benchmark artifact.

   Include `--max-model-len` when the endpoint cannot advertise the context
   limit reliably. Include `--no-thinking` only under the same rule as
   preflight.

5. Summarize TTFT, throughput, errors, context settings, concurrency, and the
   artifact path. External benchmark comparisons may be included as capacity
   priors, but they do not decide work-class quality.

## Playbook D: sync OpenClaw config

Use this after router presets, tier context windows, model ids, or per-tier
settings change. OpenClaw reads config at gateway startup, so sync usually needs
a restart.

1. Preview or write the rendered config locally.

   ```bash
   anvil-serving harness sync openclaw \
     --config ./router.toml \
     --base-url http://127.0.0.1:8000/v1 \
     --out ./openclaw.anvil.json
   ```

   Use the router base URL reachable from the OpenClaw gateway. If the gateway
   is remote, that may be a private host address rather than `127.0.0.1`.

2. Prefer gateway-local apply when possible. If the gateway cannot yet pull/apply the
   rendered config itself, push to a remote gateway only with an explicit confirmed target.

   ```bash
   anvil-serving harness sync openclaw \
     --config ./router.toml \
     --base-url http://anvil-gpu.tailnet.example:8000/v1 \
     --gateway-host <gateway-host> \
     --restart
   ```

   The SSH push is the current explicit fallback described in ADR-0014, not the
   long-term default contract. The tool merges Anvil-owned OpenClaw keys by default
   and takes a backup. Use `--overwrite` only when the operator explicitly requested
   replacement.

3. Do not pass `--skills` as part of the deterministic workflow; the current CLI
   documents that skills/agent-config sync is not implemented yet.

4. After restart, run a small OpenClaw-side smoke check if the gateway is
   available to the operator. If not, report that config was synced but live
   gateway validation remains pending.

## Playbook E: promotion evidence and stop gate

The skill may assemble evidence for a human, but promotion changes live routing
and is not automatic.

1. Gather:

   - inventory and serve fingerprint facts;
   - preflight output;
   - benchmark JSON;
   - any local eval or calibration candidate profile;
   - router config diff, if one is proposed; and
   - OpenClaw sync preview or destination.

2. Compare against the incumbent deployment:

   - same work-class and intent;
   - same endpoint or changed endpoint called out explicitly;
   - changed model, quant, engine, context, reasoning, parser, or serve flags
     called out as fingerprint drift;
   - external benchmark priors clearly marked as priors only; and
   - failed or skipped checks listed before any recommendation.

3. Stop with a recommendation:

   ```text
   Recommendation: promote / do not promote / needs more data.
   Human gate required before `anvil-serving router promote ...`.
   ```

4. Only after explicit human authorization should an operator run:

   ```bash
   anvil-serving router promote \
     --profile <candidate-profile.json> \
     --config <candidate-router.toml>
   ```

   Promotion must use the deployed router image's validation path and rollback
   behavior from ADR-0012. The skill should not replace that with manual volume
   edits.

## Failure handling

- Unknown model: re-check `models sync`, `/v1/models`, and the serve manifest.
- Preflight failure: stop; do not benchmark for promotion evidence.
- Benchmark failure after preflight pass: capture logs and mark the candidate as
  unpromotable until the capacity issue is understood.
- Router down: use `anvil-serving router status` and `anvil-serving router logs`
  before restart; restart only with an explicit operator target.
- Controller unreachable: check the private bind address, tailnet ACL, controller
  health endpoint, and `ANVIL_CONTROLLER_TOKEN` on both hosts. Use the controller
  audit log to find the failed request before falling back to raw SSH.
- OpenClaw config drift: run `harness sync openclaw` from the router config; do
  not hand-edit the provider block.
- Need for raw Docker or SSH: report the missing Anvil verb/MCP wrapper unless
  the operator explicitly approves an emergency action.
