# CLI Reference

`anvil-serving` is the operator interface for the router, local model serves,
quality gates, host operations, and integrations. This landing page is the map;
each command family has a focused reference with its commands, workflows, and
safety rules.

The cross-family grammar, safety, output, and portability rules are recorded in
[ADR-0021](adr/0021-cli-interaction-contract.md).

## Command families

| Work area | Top-level verbs | Reference |
| --- | --- | --- |
| Router data plane | `router` | [Router](cli/router.md) |
| Model serve lifecycle | `serves` | [Model serves](cli/serves.md) |
| Catalog, artifacts, and recipes | `models` | [Models & recipes](cli/models.md) |
| Quality gates and benchmarks | `eval` | [Evaluation & benchmarks](cli/eval.md) |
| Setup and host operations | `init`, `doctor`, `upgrade`, `host`, `dashboard` | [Host & setup](cli/host.md) |
| Topology and integrations | `topology`, `harness`, `mcp`, `controller`, `collectors`, `edge` | [Control plane & integrations](cli/control-plane.md) |
| Audio and realtime speech | `voice` | [Voice](cli/voice.md) |

If you are looking for serve recipes, start with
[Models & recipes: Recipes](cli/models.md#recipes). It covers listing, inspecting,
creating, updating, deleting, and loading recipes.

## Invocation and help

Run the installed entry point or the equivalent module form:

```bash
anvil-serving --help
python -m anvil_serving.cli --help
```

Help is contextual. Add `--help` after any command path to see only its children,
options, safety requirements, and documentation link:

```bash
anvil-serving models --help
anvil-serving models recipes --help
anvil-serving models recipes load --help
```

## Global options

Global options describe where a command runs and how its result is rendered.
They may appear before or after the command path, but must precede a literal `--`
separator.

| Option | Purpose |
| --- | --- |
| `--topology PATH` | Use a deployment topology document for target resolution. |
| `--topology-overlay PATH` | Apply a deployment overlay to the topology. |
| `--command-host host:ID` | Declare the host issuing the command. |
| `--command-runtime runtime:ID` | Declare the runtime issuing the command. |
| `--target host:ID\|host-role:ROLE` | Select the resource owner explicitly. |
| `--transport auto\|local\|controller\|ssh` | Choose execution transport. |
| `--allow-ssh-fallback` | Permit verified SSH recovery after a proven pre-dispatch controller failure. |
| `--experimental-model-workload` | Allow a topology-permitted experimental model workload on a model-free host. |
| `--json` | Emit the machine-readable result envelope. |
| `--quiet` | Suppress nonessential human output. |
| `--verbose` | Include diagnostic human output. |
| `-h`, `--help` | Show focused help and exit. |

## Output and safety conventions

- Read commands are bounded by default. Long-running commands such as `router run`
  and `mcp serve` remain in the foreground until stopped.
- Mutating commands expose `--dry-run` where a preview is meaningful and use one
  consent spelling: `--confirm`. Removed consent aliases fail with the replacement.
- `--json` is the stable automation surface. Human-readable output may improve over
  time without changing the result envelope.
- Resource-owner commands resolve through the declared topology. SSH is a recovery
  transport, not an implicit first choice.
- Examples use `127.0.0.1`, because loopback is host-relative.

## Complete command index

This generated index is the exhaustive public and migration surface. The family
pages above are organized for reading; this table is optimized for lookup and is
checked against the canonical command tree in CI. Its option column records dispatcher
policy; focused `--help` remains authoritative for each leaf's complete workload flags,
required operands, choices, and defaults.

<!-- BEGIN GENERATED CLI MANIFEST INDEX -->
| Command path | Purpose | Class / output | Declared command options |
|---|---|---|---|
| `init` | Scaffold the operational config home (or a single-model bring-up with --single-model). | `mutate` / `bounded` | `--out-dir`<br>`--single-model`<br>`--model`<br>`--catalog-dir`<br>`--gpu`<br>`--served-name`<br>`--tier-id`<br>`--port`<br>`--context`<br>`--engine`<br>`--disable-thinking`<br>`--bind`<br>`--expose-lan` |
| `router` | Manage the deployed router and its lifecycle. | `read` / `bounded` | - |
| `router run` | Run the router in the foreground. | `process` / `foreground` | `--config`<br>`--mode`<br>`--host`<br>`--port` |
| `router up` | Start the deployed router. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `router down` | Stop the deployed router. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `router restart` | Restart the deployed router. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `router reload` | Reload router configuration. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `router promote` | Promote a reviewed router configuration. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--profile`<br>`--config`<br>`--validate-only` |
| `router endpoint` | Show the router listen address and this node's Tailscale DNS name. | `read` / `bounded` | `--container`<br>`--host`<br>`--port`<br>`--no-tailscale` |
| `router status` | Show router status. | `read` / `bounded` | - |
| `router transition-status` | Show router tier transition state. | `read` / `bounded` | `--tier`<br>`--router-url` |
| `router quiesce` | Quiesce one router tier. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--tier`<br>`--router-url` |
| `router drain` | Wait for a quiesced tier to drain. | `read` / `bounded` | `--tier`<br>`--router-url`<br>`--timeout` |
| `router readmit` | Safely readmit one router tier. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--tier`<br>`--router-url` |
| `router logs` | Read bounded router logs. | `read` / `bounded` | `--follow` |
| `router token` | Inspect the router token state. | `read` / `bounded` | `--reveal`<br>`--confirm` |
| `serves` | Manage local model serve lifecycle. | `read` / `bounded` | - |
| `serves render` | Render a model serve definition. | `mutate` / `bounded` | - |
| `serves up` | Start manifest-owned model serves. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest`<br>`--group`<br>`--compose`<br>`--recreate`<br>`--evict`<br>`--drain-timeout`<br>`--router-url` |
| `serves down` | Stop manifest-owned model serves. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest`<br>`--group` |
| `serves rm` | Remove a model serve. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest` |
| `serves adopt` | Adopt an existing model serve. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest` |
| `serves switch` | Switch a deployment role to an activation-ready recipe. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest`<br>`--registry`<br>`--recipe` |
| `serves promote` | Promote a staged model recipe with preflight and full rollback. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest`<br>`--rollback`<br>`--resume` |
| `serves status` | Show model serve status. | `read` / `bounded` | `--manifest`<br>`--group` |
| `serves groups` | List serve groups across the manifest set and their members. | `read` / `bounded` | `--manifest` |
| `serves logs` | Read bounded model serve logs. | `read` / `bounded` | `--manifest`<br>`--tail`<br>`--since`<br>`--follow` |
| `serves multiplex` | Run the single-resident model multiplexer. | `process` / `foreground` | - |
| `models` | Manage model catalog, artifacts, and recipes. | `read` / `bounded` | - |
| `models sync` | Sync the model catalog. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--out`<br>`--hf-roots`<br>`--model-dirs` |
| `models pull` | Pull a model artifact. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--volume`<br>`--image`<br>`--revision`<br>`--include`<br>`--exclude`<br>`--token-env`<br>`--token-file`<br>`--no-token` |
| `models score` | Rank models from benchmark evidence. | `read` / `bounded` | - |
| `models recipes` | Manage recorded serve recipes. | `read` / `bounded` | - |
| `models recipes list` | List recorded serve recipes. | `read` / `bounded` | `--registry` |
| `models recipes show` | Show one recorded serve recipe. | `read` / `bounded` | `--registry` |
| `models recipes create` | Create one recipe in an operator registry. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--registry`<br>`--recipe-file` |
| `models recipes update` | Update one selected recipe. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--registry`<br>`--recipe-file` |
| `models recipes delete` | Delete one selected recipe. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--registry` |
| `models recipes load` | Load one recipe into a named local container. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--registry`<br>`--container` |
| `models cache` | Manage model cache storage. | `read` / `bounded` | - |
| `models cache prune` | Plan or prune the model cache. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--execute`<br>`--mixture`<br>`--include-servable`<br>`--allow-empty-mixture`<br>`--self-check` |
| `eval` | Run quality evaluation workflows. | `read` / `bounded` | - |
| `eval usage` | Write usage and role summaries from recorded sessions. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--logs-dir`<br>`--out-dir`<br>`--analysis-timeout` |
| `eval preflight` | Preflight an endpoint. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--tier`<br>`--manifest`<br>`--recipe`<br>`--registry`<br>`--base-url`<br>`--model`<br>`--api-key-env`<br>`--timeout-seconds`<br>`--thinking-mode`<br>`--reasoning-effort`<br>`--visible-answer-tokens`<br>`--reasoning-headroom-tokens`<br>`--checks`<br>`--needle-ctx`<br>`--tool-batch`<br>`--reasoning-evidence`<br>`--allowed-finish-reasons`<br>`--output` |
| `eval bootstrap` | Build a candidate quality profile from retained fixtures. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `eval calibrate` | Measure local tiers into a reviewable candidate profile. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `eval benchmark` | Run or import benchmark evidence. | `read` / `bounded` | - |
| `eval benchmark capacity` | Measure endpoint latency, throughput, context, and cache behavior. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--tier`<br>`--manifest`<br>`--recipe`<br>`--registry`<br>`--base-url`<br>`--model`<br>`--api-key-env`<br>`--timeout-seconds`<br>`--thinking-mode`<br>`--reasoning-effort`<br>`--visible-answer-tokens`<br>`--reasoning-headroom-tokens`<br>`--requests`<br>`--concurrency`<br>`--ctx-tokens`<br>`--max-tokens`<br>`--max-model-len`<br>`--burst`<br>`--engine`<br>`--gpu`<br>`--output` |
| `eval benchmark quality` | Run repeated quality suites and retain comparison evidence. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--tier`<br>`--manifest`<br>`--recipe`<br>`--registry`<br>`--base-url`<br>`--model`<br>`--api-key-env`<br>`--timeout-seconds`<br>`--thinking-mode`<br>`--reasoning-effort`<br>`--visible-answer-tokens`<br>`--reasoning-headroom-tokens`<br>`--suite`<br>`--suite-file`<br>`--candidate-id`<br>`--config-id`<br>`--eval-repetitions`<br>`--eval-min-pass-rate`<br>`--engine`<br>`--gpu`<br>`--source-recipe`<br>`--control-status`<br>`--control-evidence`<br>`--output` |
| `eval benchmark evidence` | Inspect retained local benchmark evidence. | `read` / `bounded` | - |
| `eval benchmark evidence list` | List retained local benchmark artifacts. | `read` / `bounded` | - |
| `eval benchmark evidence show` | Show a normalized benchmark artifact summary. | `read` / `bounded` | - |
| `eval benchmark evidence compare` | Compare artifacts and flag workload mismatches. | `read` / `bounded` | - |
| `eval benchmark external` | Manage external benchmark evidence. | `read` / `bounded` | - |
| `eval benchmark external init` | Initialize benchmark evidence storage. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--db` |
| `eval benchmark external sources` | List benchmark sources. | `read` / `bounded` | `--db` |
| `eval benchmark external fetch` | Fetch and import benchmark evidence. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--db`<br>`--source`<br>`--url` |
| `eval benchmark external import` | Import saved benchmark evidence. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--db`<br>`--source`<br>`--file` |
| `eval benchmark external list` | List normalized benchmark evidence. | `read` / `bounded` | `--db`<br>`--gpu`<br>`--model`<br>`--source`<br>`--top` |
| `eval benchmark external report` | Render a benchmark report. | `read` / `bounded` | `--db`<br>`--gpu`<br>`--model`<br>`--source`<br>`--format` |
| `eval benchmark external export` | Export benchmark evidence. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--db`<br>`--out`<br>`--format` |
| `eval benchmark external compare` | Compare local benchmark evidence. | `read` / `bounded` | `--db`<br>`--local`<br>`--gpu` |
| `eval benchmark external notebook` | Record, list, or render model-bakeoff notebook runs. | `read` / `bounded` | - |
| `eval benchmark external notebook add` | Record a bakeoff evidence run. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--db`<br>`--evidence`<br>`--task`<br>`--hardware` |
| `eval benchmark external notebook list` | List recorded bakeoff runs. | `read` / `bounded` | `--db`<br>`--task`<br>`--hardware`<br>`--format`<br>`--all` |
| `eval benchmark external notebook render` | Render the bakeoff comparison. | `read` / `bounded` | `--db`<br>`--task`<br>`--hardware`<br>`--format`<br>`--baseline` |
| `voice` | Manage audio and realtime proxy operations. | `read` / `bounded` | - |
| `voice audio` | Manage Dark-owned STT/TTS lifecycle. | `read` / `bounded` | - |
| `voice audio up` | Start audio serves. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `voice audio down` | Stop audio serves. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `voice audio status` | Show bounded audio serve status. | `read` / `bounded` | - |
| `voice audio logs` | Show bounded audio serve logs. | `read` / `bounded` | - |
| `voice proxy` | Manage the realtime proxy process. | `read` / `bounded` | - |
| `voice proxy run` | Run the realtime proxy. | `process` / `foreground` | - |
| `voice proxy up` | Start the realtime proxy. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `voice proxy down` | Stop the realtime proxy. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `voice proxy restart` | Restart the realtime proxy. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `voice proxy status` | Show realtime proxy status. | `read` / `bounded` | - |
| `voice proxy logs` | Show bounded realtime proxy logs. | `read` / `bounded` | - |
| `voice proxy bridge` | Run the Mini-to-Dark audio bridge. | `process` / `foreground` | - |
| `voice benchmark` | Benchmark an end-to-end voice session. | `read` / `bounded` | - |
| `voice profiles` | Inspect voice profiles. | `read` / `bounded` | - |
| `voice profiles list` | List voice profiles. | `read` / `bounded` | - |
| `voice profiles validate` | Validate the profile selected by --profile. | `read` / `bounded` | - |
| `voice sidecar` | Manage the speech-to-speech sidecar. | `read` / `bounded` | - |
| `voice sidecar validate` | Validate a sidecar manifest. | `read` / `bounded` | - |
| `voice sidecar command` | Render a sidecar command. | `read` / `bounded` | - |
| `voice sidecar compose` | Render sidecar compose configuration. | `read` / `bounded` | - |
| `harness` | Manage harness integration. | `read` / `bounded` | - |
| `harness sync` | Synchronize harness configuration | `read` / `bounded` | - |
| `harness sync openclaw` | Synchronize harness configuration for OpenClaw. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--config`<br>`--out`<br>`--base-url`<br>`--api-key-env`<br>`--gateway-host`<br>`--gateway-user`<br>`--gateway-path`<br>`--overwrite`<br>`--restart`<br>`--timeout-seconds`<br>`--skills`<br>`--skill-dir`<br>`--voice`<br>`--voice-realtime-url`<br>`--voice-model`<br>`--voice-consult-model`<br>`--voice-consult-thinking-level`<br>`--voice-consult-bootstrap-context-mode`<br>`--voice-api-key-env` |
| `harness restart` | Restart the harness | `read` / `bounded` | - |
| `harness restart openclaw` | Restart the harness for OpenClaw. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--gateway-host`<br>`--gateway-user`<br>`--timeout-seconds` |
| `harness status` | Show harness status | `read` / `bounded` | - |
| `harness status openclaw` | Show harness status for OpenClaw. | `read` / `bounded` | `--timeout-seconds`<br>`--max-output-bytes` |
| `mcp` | Expose bounded MCP management tools. | `read` / `bounded` | - |
| `mcp serve` | Run the MCP management server. | `read` / `protocol` | `--controller-url`<br>`--auth-env` |
| `mcp tools` | List bounded MCP tools. | `read` / `bounded` | - |
| `controller` | Manage the private controller service. | `read` / `bounded` | - |
| `controller serve` | Run the private controller. | `process` / `foreground` | `--host`<br>`--port`<br>`--auth-token-env`<br>`--allow-public-bind`<br>`--allow-operation` |
| `controller status` | Probe controller health. | `read` / `bounded` | `--url`<br>`--auth-token-env`<br>`--timeout`<br>`--max-response-bytes`<br>`--require-operation` |
| `host` | Inspect and repair declared host operations. | `read` / `bounded` | - |
| `host status` | Show structured host status. | `read` / `bounded` | - |
| `host gpus` | Show GPU inventory. | `read` / `bounded` | - |
| `host gpu-sharing` | Inspect and probe CUDA GPU-sharing capabilities. | `read` / `bounded` | - |
| `host gpu-sharing inspect` | Inspect Green Context and MPS capability without mutation. | `read` / `bounded` | `--timeout` |
| `host gpu-sharing probe` | Run the guarded Docker CUDA prerequisite probe. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--compose-file`<br>`--gpu-uuid`<br>`--timeout` |
| `host doctor` | Diagnose host configuration. | `read` / `bounded` | - |
| `host memory` | Show host RAM and WSL VM memory usage. | `read` / `bounded` | `--distro` |
| `host wsl-config` | Render or update WSL configuration. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--memory`<br>`--swap`<br>`--revert`<br>`--force` |
| `host restart-docker` | Restart Docker Desktop. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `host reset-wsl` | Reset WSL. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `host reclaim` | Drop the WSL VM page cache. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--force`<br>`--watch`<br>`--threshold-gb`<br>`--interval`<br>`--distro` |
| `doctor` | Check dependencies and configured health. | `read` / `bounded` | `--config`<br>`--no-config` |
| `upgrade` | Upgrade this CLI to the newest stable published release. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manager`<br>`--allow-editable` |
| `topology` | Inspect and resolve deployment topology. | `read` / `bounded` | - |
| `topology show` | Show a validated topology summary. | `read` / `bounded` | - |
| `topology validate` | Validate a topology offline. | `read` / `bounded` | - |
| `topology resolve` | Resolve one canonical command against a topology. | `read` / `bounded` | `--command` |
| `collectors` | Configure and inspect optional read-only collector adapters. | `read` / `bounded` | - |
| `collectors configure` | Validate and optionally write adapter configuration. | `mutate` / `bounded` | `--config`<br>`--name`<br>`--adapter`<br>`--endpoint`<br>`--capability`<br>`--auth-env`<br>`--output`<br>`--confirm` |
| `collectors validate` | Validate adapter configuration without network access. | `read` / `bounded` | `--config`<br>`--name`<br>`--adapter`<br>`--endpoint`<br>`--capability`<br>`--auth-env` |
| `collectors capabilities` | Report configured adapter capabilities offline. | `read` / `bounded` | `--config`<br>`--name`<br>`--adapter`<br>`--endpoint`<br>`--capability`<br>`--auth-env` |
| `collectors inspect` | Perform one bounded read-only adapter inspection. | `read` / `bounded` | `--config`<br>`--name`<br>`--adapter`<br>`--endpoint`<br>`--capability`<br>`--auth-env`<br>`--timeout` |
| `dashboard` | Serve the read-only system observability dashboard. | `read` / `bounded` | - |
| `dashboard serve` | Serve the packaged local dashboard. | `process` / `foreground` | `--host`<br>`--port`<br>`--auth-env` |
| `edge` | Own the Tailscale tailnet edge in front of the unchanged router. | `read` / `bounded` | - |
| `edge render` | Render the tailscale serve invocations without applying. | `read` / `bounded` | `--config`<br>`--https-port`<br>`--host`<br>`--map` |
| `edge status` | Show serve mappings, flagging which this tool manages. | `read` / `bounded` | `--config`<br>`--https-port`<br>`--host`<br>`--map` |
| `edge up` | Apply the managed route map (additive; idempotent). | `mutate` / `bounded` | `--config`<br>`--https-port`<br>`--host`<br>`--map`<br>`--dry-run`<br>`--confirm` |
| `edge down` | Remove ONLY the mounts this tool manages. | `mutate` / `bounded` | `--config`<br>`--https-port`<br>`--host`<br>`--map`<br>`--dry-run`<br>`--confirm` |
<!-- END GENERATED CLI MANIFEST INDEX -->

## Migration from legacy commands

The current CLI keeps removed paths as tombstones so an old command fails with a
specific replacement instead of becoming an unknown command. Use the replacement
shown by focused help. Common moves include:

| Legacy path | Current path |
| --- | --- |
| `serve` | `router run` |
| `deploy` | `serves render` |
| `multiplexer` | `serves multiplex` |
| `cache-prune` | `models cache prune` |
| `score` | `models score` |
| `profile` | `eval usage` |
| `preflight` | `eval preflight` |
| `benchmark` | `eval benchmark run` |
| `external-bench` | `eval benchmark external` |
| `calibrate` | `eval calibrate` |
| `gpus` | `host gpus` |
| `voice-sidecar` | `voice sidecar` |
| `onboard` | `init` |

<!-- BEGIN GENERATED CLI TOMBSTONES -->
| Removed path | Replacement |
|---|---|
| `serves rm --yes` | `--confirm` |
| `serves adopt --yes` | `--confirm` |
| `models cache prune --yes` | `--confirm` |
| `models recipe` | `models recipes` |
| `models recipe list` | `models recipes list` |
| `models recipe show` | `models recipes show` |
| `eval planning` | `eval benchmark quality --suite-file PATH` |
| `eval calibrate --i-understand-this-calls-real-tiers` | `--confirm` |
| `eval benchmark run` | `eval benchmark capacity or eval benchmark quality` |
| `voice up` | `voice audio up` |
| `voice down` | `voice audio down` |
| `voice run` | `voice proxy run` |
| `voice bridge` | `voice proxy bridge` |
| `voice start` | `voice audio up` |
| `voice stop` | `voice audio down` |
| `mcp` | `mcp serve` |
| `mcp --list-tools` | `mcp tools` |
| `mcp serve --list-tools` | `mcp tools` |
| `mcp tools --list-tools` | `mcp tools` |
| `mcp list-tools` | `mcp tools` |
| `mcp list-tools --list-tools` | `mcp tools` |
| `controller serve --allow-unauthenticated-loopback` | `Configure the token named by --auth-token-env` |
| `host restart-docker --force` | `--confirm` |
| `host reset-wsl --force` | `--confirm` |
| `host reclaim --yes` | `--confirm` |
| `serve` | `router run` |
| `deploy` | `serves render` |
| `multiplexer` | `serves multiplex` |
| `cache-prune` | `models cache prune` |
| `score` | `models score` |
| `profile` | `eval usage` |
| `preflight` | `eval preflight` |
| `benchmark` | `eval benchmark capacity` |
| `external-bench` | `eval benchmark external` |
| `calibrate` | `eval calibrate` |
| `gpus` | `host gpus` |
| `voice-sidecar` | `voice sidecar` |
| `onboard` | `init` |
<!-- END GENERATED CLI TOMBSTONES -->

## Related references

- [Configuration](CONFIGURATION.md)
- [Getting started](GETTING-STARTED.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Operator playbooks](OPERATOR-PLAYBOOKS.md)
- [Terminology](TERMINOLOGY.md)
