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
  stdio tool surface for model inventory, status, route probes, OpenClaw sync,
  voice lifecycle, preflight, and benchmark probes.
- Split-host operation: the anvil-serving host runs
  `anvil-serving controller serve`, and the gateway or operator host runs the MCP bridge with
  `anvil-serving mcp --controller-url ... --auth-env ANVIL_CONTROLLER_TOKEN`.
  The bridge presents the same tool names while sending calls to the controller
  over the private tailnet.

The MCP surface is not yet a complete replacement for every CLI operation:
direct multiplexer inspection and some router promotion context still use the
CLI or HTTP contracts below.

Prefer MCP tools when a current tool exists because they return structured
results and keep mutating/probe operations behind explicit `confirm` fields.
When a tool is missing, use the CLI equivalent on the host that owns the
resource and keep the same gate semantics.

| Operator need | Preferred MCP/controller shape | CLI/HTTP equivalent today |
|---|---|---|
| Controller readiness | Health endpoint on the controller's private address | `GET /health` on `http://anvil-gpu.tailnet.example:8766` |
| Model inventory | `models_inventory` | `anvil-serving models sync --out ./model-library` |
| Environment and tier health | `doctor_summary`, `serves_status`, `router_status` | `anvil-serving doctor --config ./router.toml`; `anvil-serving serves --manifest ./serves.toml status`; `anvil-serving router status` |
| Router lifecycle and logs | `router_manage`, `router_logs` | `anvil-serving router reload`; `anvil-serving router logs --tail 200` |
| Recent routing decisions | `decision_summary` | `GET /v1/decisions` on the router front door |
| Route-decision probe | `route_decision` | `POST /v1/route` on the router front door |
| Start or restore compose-defined serves | `serves_manage` with preview, then `confirm:true` and `dry_run:false` | `anvil-serving serves --manifest ./serves.toml up <name>` |
| Start an experiment serve | `serves_manage` with `compose` preview, then `confirm:true` and `dry_run:false` | `anvil-serving serves up --compose <compose.yml> <service>` |
| Start or stop voice STT/TTS serves | `voice_manage` with preview, then `confirm:true` and `dry_run:false` | `anvil-serving voice up --config <voice.toml>`; `anvil-serving voice down --config <voice.toml>` |
| Probe a multiplexer endpoint | Not exposed yet | `GET /healthz`; `GET /v1/models` on the multiplexer base URL |
| Serve logs | `serves_logs` with bounded `tail`; no follow mode | `anvil-serving serves --manifest ./serves.toml logs <name> --tail 200` |
| Correctness gate | `preflight_probe` | `anvil-serving preflight --base-url http://127.0.0.1:30000/v1 --model <served-name>` |
| Throughput run | `benchmark_probe` for a bounded probe; `benchmark_artifact` when `--json-out` evidence is required | `anvil-serving benchmark --base-url http://127.0.0.1:30000/v1 --model <served-name> --json-out <file>` |
| OpenClaw config sync | `openclaw_sync`, `openclaw_gateway_restart` | `anvil-serving harness sync openclaw --config <router.toml> ...`; `anvil-serving harness restart openclaw ...` |
| Human-gated promotion | `router_promote` preview; apply requires `confirm:true`, `dry_run:false`, and `human_approved:true` | `anvil-serving router promote --profile <candidate.json> [--config <candidate.toml>]` |

Treat missing MCP tools as a product gap, not a reason to scrape Docker output
or hand-edit remote configs. Use `127.0.0.1` in local URLs.

MCP invocation rules:

- Start by listing tools (`anvil-serving mcp --list-tools`) or using the
  client-provided tool registry; do not assume future tools exist.
- In split-host mode, start the same remote bridge the operator will use for the
  run and let the MCP client issue `tools/list` through that bridge:

  ```bash
  anvil-serving mcp \
    --controller-url http://anvil-gpu.tailnet.example:8766 \
    --auth-env ANVIL_CONTROLLER_TOKEN
  ```

- For `serves_manage`, call once with `confirm:false` or omitted to preview the
  resolved plan and a dry-run command. A live serve mutation requires both
  `confirm:true` and `dry_run:false` after the exact manifest or compose file
  and serve names are known.
- For `voice_manage`, use the same preview-first pattern. It starts/stops only
  the STT/TTS lifecycle declared by the voice manifest on the host where the
  tool runs: Docker-backed managed audio serves, or same-host native processes
  such as MLX Audio endpoints with PID/log files.
- For `router_manage`, use the same preview-first pattern. A live router
  lifecycle change requires both `confirm:true` and `dry_run:false`.
- For `router_promote`, preview validates the candidate profile/config and
  returns a compact diff summary. Live apply additionally requires
  `confirm:true`, `dry_run:false`, and `human_approved:true`.
- For `preflight_probe`, `benchmark_probe`, `benchmark_artifact`, and
  `openclaw_sync`, call once with `confirm:false` or `dry_run:true` to preview
  the command/result shape, then call with `confirm:true` only after the exact
  endpoint, model, config, artifact path, and target host are known.
- For authenticated probes, pass `api_key_env` such as `ANVIL_ROUTER_TOKEN`;
  never pass a literal token value through MCP arguments, command previews, or
  saved evidence.
- For controller transport, `--auth-env ANVIL_CONTROLLER_TOKEN` names the
  environment variable containing the controller token. The token value must be
  present on both the controller host and the gateway/operator host, but it
  must not appear in tool arguments, command previews, logs, or saved evidence.
- Treat a successful command preview as planning evidence only, not as
  preflight, benchmark, or sync evidence.
- Preserve returned structured data and the equivalent command line in the
  operator report. When a call crosses the controller, also preserve the
  controller request id or audit-log reference if one is returned.

## Controller transport

Use this when the operator or OpenClaw gateway is on one trusted device and the
anvil-serving CLI, router config, serves manifests, voice manifests, or GPU-local
operations live on another private host. Fakoli Mini and Fakoli Dark are the
reference topology; additional laptops can use the same pattern when they are
reachable over Tailscale or another private or direct network path.

1. On the anvil-serving host, bind the controller to a private Tailscale DNS
   name/address or to `127.0.0.1` for single-host local development. Do not bind
   it to a public interface.

   ```bash
   export ANVIL_CONTROLLER_TOKEN="<generate-and-store-out-of-band>"
   anvil-serving controller serve \
     --host anvil-gpu.tailnet.example \
     --port 8766 \
     --auth-token-env ANVIL_CONTROLLER_TOKEN
   ```

   Local-only development uses the same command with `--host 127.0.0.1`.

2. Before running remote operations, check the controller health endpoint on the
   same private address the bridge will use:

   ```bash
   curl -fsS \
     -H "Authorization: Bearer $ANVIL_CONTROLLER_TOKEN" \
     http://anvil-gpu.tailnet.example:8766/health
   ```

   This proves the management plane is reachable. It does not prove router tier
   health; run `doctor_summary`, `serves_status`, and `router_status` for that.

3. On the gateway or operator host, start the MCP bridge with the controller URL
   and token env var name:

   ```bash
   export ANVIL_CONTROLLER_TOKEN="<same-secret-as-controller-host>"
   anvil-serving mcp \
     --controller-url http://anvil-gpu.tailnet.example:8766 \
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

   Prefer `models_inventory` when MCP/controller is available. A read-only call
   reads `cards/*.json` summaries from the generated catalog without scraping
   `INDEX.md`. If the catalog is missing, preview the sync command first:

   ```json
   {
     "catalog_dir": "./model-library",
     "sync": true,
     "confirm": false
   }
   ```

   Then run the confirmed sync only when the output directory and roots are
   known:

   ```json
   {
     "catalog_dir": "./model-library",
     "sync": true,
     "confirm": true
   }
   ```

   Preserve the returned model id, weight format, loadability, context window,
   quantization, and thinking defaults.

2. Capture environment and live topology.

   ```bash
   anvil-serving doctor --config ./router.toml
   anvil-serving serves --manifest ./serves.toml status
   anvil-serving router status
   ```

   In split-host mode, first prove the controller itself is reachable from the
   gateway or operator host:

   ```bash
   curl -fsS \
     -H "Authorization: Bearer $ANVIL_CONTROLLER_TOKEN" \
     http://anvil-gpu.tailnet.example:8766/health
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

   Prefer `serves_manage` when MCP/controller is available. The preview returns
   both the wrapper argv and a resolved plan of Docker or manifest commands.
   Live mutation requires `confirm:true` and `dry_run:false`; use it only after
   the manifest and serve names are exact.

3. Re-check status and logs only if needed.

   ```bash
   anvil-serving serves --manifest ./serves.toml status
   anvil-serving serves --manifest ./serves.toml logs <serve-name> --tail 200
   ```

   Prefer `serves_logs` for logs through MCP/controller. It requires exactly one
   manifest serve name, caps `tail`, spools subprocess output, caps returned
   output bytes, and rejects follow mode so the call remains bounded.

### Experiment serve

For a model not yet in the manifest, use the checked-in experiment compose file
or an operator-supplied compose file. Do not invent a raw `docker run` command.

```bash
anvil-serving serves up --compose examples/fakoli-dark/docker-compose.experiment.yml <service>
```

Through MCP/controller, call `serves_manage` with `action:"up"`, `compose`, and
the compose service names. The first call should omit `confirm` to capture the
dry-run preview; the confirmed call may run only with `confirm:true` and
`dry_run:false` after the compose file and service names are explicit.

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

## Playbook C: voice lifecycle and validation

Use this when operating `anvil-serving voice` on a gateway, Mini, laptop, or
other trusted voice host. The voice command surface has three layers: STT/TTS
lifecycle (`up`/`down`), foreground Realtime serving (`run`), and evidence
(`benchmark` or `scripts/voice/mini_validation.py`).

1. Identify the voice topology and manifest.

   First name the devices that own each role: voice/Realtime server, STT, TTS,
   LLM router, and lifecycle control. Same-host endpoints should use
   `127.0.0.1`; cross-device endpoints should use a private tailnet or direct
   address. `lifecycle = "native"` starts a process on the host running
   `voice up`, so use `external` for remote STT/TTS unless operating that
   remote host through local CLI or a controller.

   The checked-in Mini manifest is one reference topology:

   ```bash
   examples/voice/fakoli-mini.toml
   ```

   It runs STT and TTS as native MLX Audio processes on `127.0.0.1:30010` and
   `127.0.0.1:30011`, while the LLM goes to the Fakoli Dark router over the
   tailnet. Additional laptops can use the same manifest shape with device
   names, `base_url` values, and lifecycle fields changed for the new topology.

2. Preview STT/TTS lifecycle before mutation.

   ```bash
   anvil-serving voice up --config examples/voice/fakoli-mini.toml --dry-run
   ```

   Prefer `voice_manage` through MCP/controller when available:

   ```json
   {
     "action": "up",
     "config": "examples/voice/fakoli-mini.toml"
   }
   ```

   The preview should show each audio endpoint's lifecycle. `native` endpoints
   show the parsed start command, PID file, log file, and readiness timeout.

3. Start the audio endpoints only after the target manifest is exact.

   ```bash
   anvil-serving voice up --config examples/voice/fakoli-mini.toml
   ```

   Through MCP/controller, the live call requires:

   ```json
   {
     "action": "up",
     "config": "examples/voice/fakoli-mini.toml",
     "confirm": true,
     "dry_run": false
   }
   ```

4. Start the Realtime server in the foreground.

   ```bash
   anvil-serving voice run --config examples/voice/fakoli-mini.toml
   ```

   This command probes the LLM, STT, and TTS endpoints before binding the
   WebSocket server. It should fail loudly on unreachable endpoints rather than
   starting a session pool that cannot serve a turn.

5. Collect evidence.

   For a quick smoke measurement:

   ```bash
   anvil-serving voice benchmark --config examples/voice/fakoli-mini.toml
   ```

   For Mini acceptance evidence:

   ```bash
   python scripts/voice/mini_validation.py --report
   ```

   The Mini validation report adds host identity, memory, endpoint model ids,
   router auth proof, and post-benchmark STT/TTS process memory.

6. Stop audio endpoints when done.

   ```bash
   anvil-serving voice down --config examples/voice/fakoli-mini.toml
   ```

   `voice down` does not stop the router and does not stop an already-running
   foreground `voice run`; stop that process with Ctrl+C.

## Playbook D: preflight then benchmark

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

   Through MCP/controller, use `benchmark_probe` for the quick bounded probe and
   `benchmark_artifact` for promotion evidence. `benchmark_artifact` validates
   the artifact path before it runs and only writes under the workspace or
   server-configured `ANVIL_BENCHMARK_EVIDENCE_DIR` / `ANVIL_EVIDENCE_DIR`
   roots.

   Include `--max-model-len` when the endpoint cannot advertise the context
   limit reliably. Include `--no-thinking` only under the same rule as
   preflight.

5. Summarize TTFT, throughput, errors, context settings, concurrency, and the
   artifact path. External benchmark comparisons may be included as capacity
   priors, but they do not decide work-class quality.

## Playbook E: sync OpenClaw config

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

## Playbook F: promotion evidence and stop gate

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

4. Preview the promotion through MCP/controller when available:

   ```json
   {
     "profile": "<candidate-profile.json>",
     "config": "<candidate-router.toml>",
     "current_profile": "<current-profile.json>"
   }
   ```

   The preview validates the profile/config and returns a diff summary without
   writing. Apply is allowed only with `confirm:true`, `dry_run:false`, and
   `human_approved:true`.

5. Only after explicit human authorization should an operator run the CLI
   fallback:

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
