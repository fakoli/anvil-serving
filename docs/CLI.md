# CLI Reference

`anvil-serving` is a single stdlib-only CLI (Python >= 3.11) that fronts every product surface:
the quality-gated router, the local GPU serve lifecycle, the model catalog, the quality loop, the
MCP/controller control plane, and the voice pipeline. This page is the complete verb reference for
v0.12.0. Run `anvil-serving --help` for the grouped live list, examples, and typo suggestions.
Run `anvil-serving <verb> --help`, or parser-backed focused action help such as
`anvil-serving router logs --help`, for the relevant flag set. Use `127.0.0.1` in local URLs,
never `localhost`.

## Global invocation

```
anvil-serving --help
anvil-serving --version
anvil-serving --command-manifest
anvil-serving <command> --help
```

`-h`/`--help` prints the grouped root surface or focused parser help. `-V`/`--version` prints the
installed package version as `anvil-serving X.Y.Z`. Root help also names the canonical nested
workflows that are easiest to miss: `serves render`, `models cache prune`, `models score`,
`eval benchmark external`, and `voice sidecar`.
`--command-manifest` prints the deterministic JSON command contract and rejects command arguments.

Topology-aware resource commands accept `--topology PATH`, `--command-host`, `--command-runtime`,
`--target`, `--transport`, and `--allow-ssh-fallback`. A model-free host rejects model workloads before handler launch.
The narrow exception requires both an `experimental-model` resource permitted by the topology's
capacity policy and the per-invocation `--experimental-model-workload` flag. Successful overrides
emit a warning and capacity audit fields; the flag alone never upgrades an ordinary model resource.
Remote-capable commands execute through the authenticated controller declared for the resource
owner. `auto` never selects SSH. A recovery-capable command may use `--transport ssh` or
`--allow-ssh-fallback`; fallback is limited to a proven pre-dispatch controller connection failure
and requires a pinned host identity plus the private-key path in `ANVIL_SSH_IDENTITY_FILE`.
Ambiguous controller outcomes are reconciled by idempotency status and never replayed over SSH.

The dispatcher uses exit status `0` for success, `1` for execution failure, `2` for invalid usage,
`3` for a refused safety gate, `4` for transport failure, and `5` for partial completion. Leaf
commands may preserve a subprocess's non-zero status. Human-readable results
go to stdout; warnings, migration guidance, and errors go to stderr. Commands that can mutate
containers, host configuration, caches, routing profiles, or non-loopback exposure document their
confirmation or acknowledgement flags in focused `--help`.

## Command index

| Verb | Purpose | Group |
|------|---------|-------|
| `router run` | Start the quality-gated router front door on `127.0.0.1:8000`. | Data plane |
| `router` | Manage the deployed (containerized) router: lifecycle, token, promote. | Data plane |
| `serves` | Stop/start/inspect the local GPU model serves from a serves manifest. | Local serving tools |
| `serves render` | Render a tuned SGLang/vLLM docker-compose for a GPU + model. | Local serving tools |
| `models` | Model catalog (`sync`), HF downloads into a docker volume (`pull`), serve recipes (`recipe`). | Local serving tools |
| `models cache prune` | Local HF cache safety and retention planning with explicit execution gates. | Local serving tools |
| `models score` | Role-suitability scorer over benchmark evidence; recommends a mixture. | Quality loop |
| `eval usage` | Turn Claude Code logs into a usage baseline + sizing inputs. | Quality loop |
| `eval preflight` | Correctness gate against any OpenAI-compatible endpoint. | Quality loop |
| `eval benchmark run` | Replay the measured request distribution (TTFT, throughput, prefix cache). | Quality loop |
| `eval benchmark external` | Ingest, store, report, and compare external inference benchmarks. | Quality loop |
| `serves multiplex` | Single-resident model swap server on one GPU (RAM-guarded). | Local serving tools |
| `init` | Detect GPUs + a model; write compose + serves.toml + router.toml. | Local serving tools |
| `doctor` | Environment preflight for a router deploy (Python, docker, GPU, tier health). | Local serving tools |
| `host` | Own the WSL / Docker Desktop host config (inspect, cap, restart, reset). | Local serving tools |
| `eval` | Unified eval harness: preflight / benchmark / planning / bootstrap. | Quality loop |
| `eval calibrate` | Guarded write-back batch: measure local tiers, judge, write a candidate profile. | Quality loop |
| `mcp` | Stdio MCP server (and remote-controller proxy) for operational tools. | Control plane & integrations |
| `controller` | Token-authenticated HTTP controller for split-host MCP forwarding. | Control plane & integrations |
| `harness` | Render/apply harness-side config (OpenClaw) from the live router config. | Control plane & integrations |
| `topology` | Validate and resolve host/resource ownership without execution. | Control plane & integrations |
| `voice` | Voice pipeline: STT/TTS serve lifecycle, realtime server, benchmark, bridge. | Voice |
| `voice sidecar` | Validate/render the HF speech-to-speech sidecar command and compose. | Voice |

Legacy aliases are documented in the compatibility section at the end of this file.

## Manifest contract index

This generated index is the mechanical coverage contract for the checked-in command manifest.
Every visible manifest path appears exactly once here. Options shown are command-specific policy
options declared by the dispatcher; leaf parsers may expose additional workload-specific flags in
focused `--help`.

<!-- BEGIN GENERATED CLI MANIFEST INDEX -->
| Command path | Purpose | Class / output | Declared command options |
|---|---|---|---|
| `init` | Generate a local bring-up from detected facts. | `read` / `bounded` | - |
| `router` | Manage the deployed router and its lifecycle. | `read` / `bounded` | - |
| `router run` | Run the router in the foreground. | `process` / `foreground` | `--config`<br>`--mode`<br>`--host`<br>`--port` |
| `router up` | Start the deployed router. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `router down` | Stop the deployed router. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `router restart` | Restart the deployed router. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `router reload` | Reload router configuration. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `router promote` | Promote a reviewed router configuration. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--validate-only` |
| `router status` | Show router status. | `read` / `bounded` | - |
| `router transition-status` | Show router tier transition state. | `read` / `bounded` | `--tier`<br>`--router-url` |
| `router quiesce` | Quiesce one router tier. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--tier`<br>`--router-url` |
| `router drain` | Wait for a quiesced tier to drain. | `read` / `bounded` | `--tier`<br>`--router-url`<br>`--timeout` |
| `router readmit` | Safely readmit one router tier. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--tier`<br>`--router-url` |
| `router logs` | Read bounded router logs. | `read` / `bounded` | `--follow` |
| `router token` | Inspect the router token state. | `read` / `bounded` | `--reveal`<br>`--confirm` |
| `serves` | Manage local model serve lifecycle. | `read` / `bounded` | - |
| `serves render` | Render a model serve definition. | `mutate` / `bounded` | `--dry-run` |
| `serves up` | Start manifest-owned model serves. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest`<br>`--compose`<br>`--recreate` |
| `serves down` | Stop manifest-owned model serves. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest` |
| `serves rm` | Remove a model serve. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest`<br>`--yes` |
| `serves adopt` | Adopt an existing model serve. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest`<br>`--yes` |
| `serves promote` | Promote a staged model recipe with preflight and full rollback. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--manifest`<br>`--rollback`<br>`--resume` |
| `serves status` | Show model serve status. | `read` / `bounded` | `--manifest` |
| `serves logs` | Read bounded model serve logs. | `read` / `bounded` | `--manifest`<br>`--tail`<br>`--since`<br>`--follow` |
| `serves multiplex` | Run the single-resident model multiplexer. | `process` / `foreground` | - |
| `models` | Manage model catalog, artifacts, and recipes. | `read` / `bounded` | - |
| `models sync` | Sync the model catalog. | `mutate` / `bounded` | `--dry-run` |
| `models pull` | Pull a model artifact. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `models score` | Rank models from benchmark evidence. | `read` / `bounded` | - |
| `models recipes` | Inspect recorded serve recipes. | `read` / `bounded` | - |
| `models recipes list` | List recorded serve recipes. | `read` / `bounded` | - |
| `models recipes show` | Show one recorded serve recipe. | `read` / `bounded` | - |
| `models cache` | Manage model cache storage. | `read` / `bounded` | - |
| `models cache prune` | Plan or prune the model cache. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--execute` |
| `eval` | Run quality evaluation workflows. | `read` / `bounded` | - |
| `eval usage` | Analyze recorded usage. | `read` / `bounded` | - |
| `eval preflight` | Preflight an endpoint. | `mutate` / `bounded` | `--confirm` |
| `eval planning` | Run planning evaluation. | `read` / `bounded` | - |
| `eval bootstrap` | Bootstrap a quality profile. | `mutate` / `bounded` | - |
| `eval calibrate` | Calibrate a reviewable quality profile. | `mutate` / `bounded` | - |
| `eval benchmark` | Run or import benchmark evidence. | `read` / `bounded` | - |
| `eval benchmark run` | Run an endpoint benchmark. | `mutate` / `bounded` | `--confirm` |
| `eval benchmark evidence` | Inspect retained local benchmark evidence. | `read` / `bounded` | - |
| `eval benchmark evidence list` | List retained local benchmark artifacts. | `read` / `bounded` | - |
| `eval benchmark evidence show` | Show a normalized benchmark artifact summary. | `read` / `bounded` | - |
| `eval benchmark evidence compare` | Compare artifacts and flag workload mismatches. | `read` / `bounded` | - |
| `eval benchmark external` | Manage external benchmark evidence. | `read` / `bounded` | - |
| `eval benchmark external init` | Initialize benchmark evidence storage. | `mutate` / `bounded` | - |
| `eval benchmark external sources` | List benchmark sources. | `read` / `bounded` | - |
| `eval benchmark external fetch` | Fetch and import benchmark evidence. | `mutate` / `bounded` | - |
| `eval benchmark external import` | Import saved benchmark evidence. | `mutate` / `bounded` | - |
| `eval benchmark external list` | List normalized benchmark evidence. | `read` / `bounded` | - |
| `eval benchmark external report` | Render a benchmark report. | `read` / `bounded` | - |
| `eval benchmark external export` | Export benchmark evidence. | `mutate` / `bounded` | - |
| `eval benchmark external compare` | Compare local benchmark evidence. | `read` / `bounded` | - |
| `eval benchmark external notebook` | Record, list, or render model-bakeoff notebook runs. | `read` / `bounded` | - |
| `eval benchmark external notebook add` | Record a bakeoff evidence run. | `mutate` / `bounded` | - |
| `eval benchmark external notebook list` | List recorded bakeoff runs. | `read` / `bounded` | - |
| `eval benchmark external notebook render` | Render the bakeoff comparison. | `read` / `bounded` | - |
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
| `harness sync openclaw` | Synchronize harness configuration for OpenClaw. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `harness restart` | Restart the harness | `read` / `bounded` | - |
| `harness restart openclaw` | Restart the harness for OpenClaw. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `harness status` | Show harness status | `read` / `bounded` | - |
| `harness status openclaw` | Show harness status for OpenClaw. | `read` / `bounded` | - |
| `mcp` | Expose bounded MCP management tools. | `read` / `bounded` | `--list-tools` |
| `mcp serve` | Run the MCP management server. | `read` / `protocol` | `--list-tools` |
| `mcp tools` | List bounded MCP tools. | `read` / `bounded` | `--list-tools` |
| `controller` | Manage the private controller service. | `read` / `bounded` | - |
| `controller serve` | Run the private controller. | `process` / `foreground` | `--allow-unauthenticated-loopback` |
| `controller status` | Probe controller health. | `read` / `bounded` | - |
| `host` | Inspect and repair declared host operations. | `read` / `bounded` | - |
| `host status` | Show structured host status. | `read` / `bounded` | - |
| `host gpus` | Show GPU inventory. | `read` / `bounded` | - |
| `host gpu-sharing` | Inspect and probe CUDA GPU-sharing capabilities. | `read` / `bounded` | - |
| `host gpu-sharing inspect` | Inspect Green Context and MPS capability without mutation. | `read` / `bounded` | `--timeout` |
| `host gpu-sharing probe` | Run the guarded Docker CUDA prerequisite probe. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--compose-file`<br>`--gpu-uuid`<br>`--timeout` |
| `host doctor` | Diagnose host configuration. | `read` / `bounded` | - |
| `host memory` | Show host RAM and WSL VM memory usage. | `read` / `bounded` | - |
| `host wsl-config` | Render or update WSL configuration. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `host restart-docker` | Restart Docker Desktop. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `host reset-wsl` | Reset WSL. | `mutate` / `bounded` | `--dry-run`<br>`--confirm` |
| `host reclaim` | Drop the WSL VM page cache. | `mutate` / `bounded` | `--dry-run`<br>`--confirm`<br>`--watch` |
| `doctor` | Check dependencies and configured health. | `read` / `bounded` | - |
| `topology` | Inspect and resolve deployment topology. | `read` / `bounded` | - |
| `topology show` | Show a validated topology summary. | `read` / `bounded` | - |
| `topology validate` | Validate a topology offline. | `read` / `bounded` | - |
| `topology resolve` | Resolve one canonical command against a topology. | `read` / `bounded` | - |
| `collectors` | Configure and inspect optional read-only collector adapters. | `read` / `bounded` | - |
| `dashboard` | Serve the read-only system observability dashboard. | `read` / `bounded` | - |
| `dashboard serve` | Serve the packaged local dashboard. | `process` / `foreground` | `--host`<br>`--port`<br>`--auth-env` |
<!-- END GENERATED CLI MANIFEST INDEX -->

---

## Data plane

### `router run`

```
anvil-serving router run (--config PATH | --mode agentic|flexibility) [--host HOST] [--port PORT]
```

Starts the protocol-standard front door bound to the tiers in a router config
(config -> per-tier backends -> front door). A config selector is required — `--config`, `--mode`,
or the `ANVIL_MODE`/`ANVIL_MODES_CONFIG` environment variables; bare `router run` with none of them
set is a usage error (the router never silently boots a default). See the
[Configuration reference](CONFIGURATION.md) for the precedence chain.

| Flag | Default | Meaning |
|------|---------|---------|
| `--config PATH` | — | Load this exact router TOML; bypasses the mode resolver. |
| `--mode {agentic,flexibility}` | — | Resolve the global mode to its config (ADR-0011). Precedence: `--mode` > `ANVIL_MODE` > `[modes].active_mode` > default; a mode maps to a file via `ANVIL_CONFIG_<MODE>` > a `[modes]` manifest (`ANVIL_MODES_CONFIG`) > built-in default. |
| `--host` | `127.0.0.1` | Bind host. Configure `[server].auth_env` before any non-loopback bind — see `SECURITY.md`. |
| `--port` | `8000` | Bind port. |

```bash
anvil-serving router run --config configs/example.toml
```

### `router`

```
anvil-serving router {up|down|restart|reload|status|transition-status|quiesce|drain|readmit|logs|token|promote} [flags]
```

Manages the deployed, containerized router (ADR-0004): lifecycle, bearer token, logs, and the
guarded profile-promotion write-back path. For `up`/`down`, omitted `--compose`
uses `~/.anvil-serving/docker-compose.yml` when present, else the checked-in
Fakoli Dark example. `router up` also auto-detects `~/.anvil-serving/.env`,
then legacy `~/.anvil_env`, then `~/.env` unless `--env-file` is provided.

| Action | What it does |
|--------|--------------|
| `up` / `down` | `docker compose` bring-up/tear-down (`--compose`, `--service`, `--env-file`). |
| `restart` / `reload` / `status` | Restart the container / restart to reload startup-read config / show container + health status. `restart`/`reload` verify the router STAYS up (~11s settle + consecutive samples, the same crash-loop check `promote` uses); `--no-verify` skips it for rapid iteration. |
| `logs` / `token` | `docker logs` (`--tail`, `--since`, `--follow`) / inspect whether auth is configured. Token values require `--reveal --confirm`. |
| `promote` | Validate + write a new profile (and optionally config) into the router's config volume; requires `--profile`; `--no-reload` skips the restart. |
| `transition-status` | Read router-owned per-tier admission state, active count, readiness reason, and expected/observed model. |
| `quiesce` | Atomically stop new leases for `--tier`; previews unless `--confirm` is supplied. |
| `drain` | Wait up to `--timeout` for a previously quiesced tier's active count to reach zero. A timeout exits nonzero and performs no lifecycle mutation. |
| `readmit` | Invalidate cached readiness and re-open `--tier` only after current health and exact model identity pass; previews unless `--confirm` is supplied. |

Key flags: `--container` (default `anvil-router`), `--compose`, `--service`, `--dry-run`;
promote-only: `--profile`, `--config`, `--cfg-volume`, `--image`, `--profile-dest`,
`--config-dest`, `--no-reload`.

```bash
anvil-serving router promote --profile ./candidate-profile.json --dry-run
anvil-serving router quiesce --tier heavy-local --router-url http://127.0.0.1:8000 --confirm
anvil-serving router drain --tier heavy-local --timeout 120 --router-url http://127.0.0.1:8000
anvil-serving router readmit --tier heavy-local --router-url http://127.0.0.1:8000 --confirm
```

---

## Local serving tools

### `serves`

```
anvil-serving serves {status|up|down|rm|adopt|logs|render|promote} [NAME ...] [flags]
```

Stop/start/inspect the local GPU model serves declared in a serves manifest.
When `--manifest` is omitted, discovery checks `./serves.toml` first, then
`~/.anvil-serving/serves.toml`. The router connects to these; this verb manages them.
See [Serves & eval](SERVES-AND-EVAL.md) for the manifest format and workflows.

| Action | What it does |
|--------|--------------|
| `status` | Docker + `/health` state for every manifest serve. |
| `up` | Start (restart/unpause/run the manifest `up`); `--recreate` forces a fresh `up`; `up --compose FILE` brings up an ad-hoc compose serve not in the manifest. |
| `down` | `docker stop` the serves, then re-checks state: a container revived by its restart policy (GPU not actually freed) is a loud warning and exit 1. |
| `rm` | `docker rm -f`; an unrecognised name is treated literally as a container (evict an experiment squatting a port). **Irreversible, so it prompts `[y/N]` — pass `--yes` in scripts/automation** (no TTY answers No and nothing is removed). |
| `adopt` | Bring an externally-started manifest serve under compose management (recreates via `docker rm -f` + `up`, so it prompts like `rm`; `--yes` skips). |
| `logs` | `docker logs` for one serve (`--tail`, `--since`, `--follow`). |
| `render` | Render tuned compose, serves-manifest, and router-tier configuration for a model. |
| `promote` | Execute the manifest's complete quiesce → drain → Heavy swap → health/identity → direct preflight → router promotion/restart → post-restart readiness transaction. `--rollback` uses the same order; `--resume` reasserts quiescence and reruns every gate while reusing an already healthy target. |

Common flags: `--manifest`, `--dry-run`; `rm`/`adopt` also take `--yes`.

**GPU residency reservations (ADR-0017).** A `[[serve]]` entry may declare
`gpu_role`, `vram_mib`, and `residency`, and the manifest may declare
`[[gpu_roles]]` capacity rows (`id`, `vram_mib` capacity, `reserve_mib`
display/system reserve — mirroring the operator topology fields). With both
present, `up` (including `voice audio up`) acquires the serve's VRAM
reservation first: an over-budget request prints the per-role ledger
(capacity/reserve/committed/free plus the offending reservation) and exits 1
before any container command runs. The ledger derives from running serves
(docker state) plus the declared fields — there is no state file — so `down`
releases a reservation simply by stopping the container. `serves status`
prints the same ledger — per-`gpu_role` capacity, reserve, committed, and free
MiB plus each declared reservation with its observed docker state — and the
read-only MCP `reservation_status` tool returns it structurally, so agents can
answer "can model X fit right now?" without starting anything. Manifests
without these fields behave exactly as before.

```bash
anvil-serving serves up heavy --manifest ./serves.toml --dry-run
anvil-serving serves promote thinkingcap-heavy --manifest ./serves.toml --dry-run
anvil-serving serves promote thinkingcap-heavy --manifest ./serves.toml --confirm
anvil-serving serves promote thinkingcap-heavy --manifest ./serves.toml --rollback --confirm
anvil-serving serves promote thinkingcap-heavy --manifest ./serves.toml --resume --confirm
```

### `serves render`

```
anvil-serving serves render --model PATH [--gpu IDX|UUID] [--context N] [--served-name NAME]
                           [--port N] [--out FILE] [--engine sglang|vllm] [flags]
```

One-shot compose render path for model onboarding: tuned SGLang/vLLM docker-compose for a
GPU + model, plus appends a `[[serve]]` entry to the serves manifest (`--manifest-out`, default
`./serves.toml`; `--no-manifest` skips), and prints a router-tier stub to paste into your config.
Key flags: `--gpu` (index or GPU-UUID, default `0`), `--context` (default `131072`), `--served-name`/
`--port`/`--out` (defaults `local-specialist` / `30000` / `docker-compose.yml`), `--engine
sglang|vllm` (default inferred from the model's `config.json`), `--gpu-mem-util` (vLLM only, default
`0.90`), `--disable-thinking` + `--model-facts` (auto-disable a thinking-by-default model from a
`models sync` card), `--tier-id`, and `--bind`/`--expose-lan` (default `127.0.0.1`; `--expose-lan` = `0.0.0.0`,
see `SECURITY.md`).

GPU residency reservations (ADR-0017): `--gpu-role` + `--vram-mib` (and optional `--residency
resident|evictable|on-demand`) declare the serve's VRAM reservation. When the manifest declares a
matching `[[gpu_roles]]` capacity row, the engine memory fraction is **derived** from
`vram_mib / (capacity - reserve)` — `--gpu-memory-utilization` (vLLM) or `--mem-fraction-static`
(SGLang) — overriding `--gpu-mem-util`, and the reservation fields are written into the appended
`[[serve]]` entry so `serves up` admission enforces the same budget. Without reservation flags the
render is unchanged.

```bash
anvil-serving serves render --model /models/qwen3-32b-nvfp4 --gpu 1 --context 131072 --served-name heavy
```

### `models`

```
anvil-serving models sync [--out DIR] [--hf-roots ROOTS] [--model-dirs DIRS]
anvil-serving models pull REPO_ID [--volume VOL] [--image IMG] [--revision R]
                                  [--include GLOB] [--exclude GLOB] [--token-env ENV]
                                  [--token-file PATH | --no-token] [--dry-run]
anvil-serving models recipes {list|show MODEL} [--registry TOML]
anvil-serving models cache prune [flags]
anvil-serving models score [flags]
```

Model catalog + fetch. `sync` scans HF caches and plain model dirs, pulls model cards, extracts
serving facts, and writes `cards/` + `INDEX.md` (default out dir `./model-library`). `pull`
downloads a HF repo into a **named docker volume** (default `vllm-hfcache`) via `hf download`
inside a container, avoiding the 9P bind-mount tax. Pulls forward `HF_TOKEN` by name by default:
an exported value wins, otherwise the command reads it from `~/.env` (override with
`--token-env` and `--token-file`). The token value is never placed on argv. Use `--no-token`
only for an explicitly anonymous pull. `recipes list`/`recipes show` read the recorded serve-recipe registry (default
`configs/serve-recipes.toml`) written by `eval benchmark run --recipe-out`. Cache pruning and model scoring
are documented in their focused sections below.

```bash
anvil-serving models pull openai/gpt-oss-120b --volume vllm-hfcache --dry-run
```

### `models cache prune`

```
anvil-serving models cache prune [--mixture CSV] [--json] [--execute --yes] [--dry-run]
                                 [--include-servable] [--allow-empty-mixture] [--self-check]
```

Plans (and only with explicit gates, executes) pruning of local HF model caches. Default is a safe
dry-run; real deletion requires **both** `--execute` and `--yes`, deletes only dead-everywhere
candidates unless `--include-servable`, and refuses a broad wipe with an empty `--mixture` unless
`--allow-empty-mixture`. `--mixture` lists model ids to protect.

```bash
anvil-serving models cache prune --mixture openai/gpt-oss-120b,Qwen/Qwen3-32B --json
```

### `eval preflight`

```
anvil-serving eval preflight (--base-url URL --model ID | --tier NAME [--manifest PATH])
                        --confirm [--api-key-env ENV] [--needle-ctx N]
                        [--tool-batch N] [--checks LIST]
                        [--thinking-mode MODE | --reasoning-effort LEVEL]
                        [--visible-answer-tokens N] [--reasoning-headroom-tokens N]
                        [--json-out PATH] [--no-thinking]
```

Correctness gate against any OpenAI-compatible endpoint, before trusting throughput: short coding
smoke, structured JSON, long-context needle retrieval (`--needle-ctx`, default 128000), and a
shared-prefix tool-calling batch (`--tool-batch`, default 20). `--checks` can select an ordered
subset of `smoke,json,needle,tools`. `--thinking-mode enabled|disabled` sends the Qwen-style
`chat_template_kwargs` control; `--reasoning-effort` supports model families that use the
top-level OpenAI field. `--no-thinking` remains an alias for `--thinking-mode disabled`.
The visible-answer allocation and reasoning headroom are recorded separately and sent as one
completion cap; each result reports finish reason, visible length, parsed reasoning-channel
length, and reasoning-token usage when the engine exposes it. `--json-out` retains that evidence.
`--tier` fills the endpoint and model from a serves manifest;
without `--manifest`, it uses the bundled reference manifest. The CLI requires `--confirm` because
the gate sends a live workload. Controller execution accepts direct endpoint inputs only, so an
operator-local manifest path is never interpreted on another host. Exit code 0 = all pass, 1 = any fail.

```bash
anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 --model local --no-thinking --confirm
anvil-serving eval preflight --tier heavy --thinking-mode enabled \
  --visible-answer-tokens 256 --reasoning-headroom-tokens 4096 \
  --checks smoke,json --json-out ./preflight-thinking.json --confirm
```

### `eval benchmark`

```
anvil-serving eval benchmark run (--base-url URL --model ID | --tier NAME [--manifest PATH]) --confirm [flags]
anvil-serving eval benchmark evidence {list|show|compare} [flags]
anvil-serving eval benchmark external {init|sources|fetch|import|list|report|export|compare} [flags]
```

Replays the measured Claude Code subagent request distribution and reports TTFT, end-to-end
latency, throughput, and a prefix-cache hit signal. Like preflight, the CLI requires `--confirm`
before sending the live workload, and controller execution accepts direct endpoint inputs only.

| Flag | Default | Meaning |
|------|---------|---------|
| `--requests` / `--concurrency` | `60` / `20` | Steady mixed load. |
| `--burst N` / `--shared-prefix-tokens` | `0` / `8000` | If burst >0, fire N requests sharing one prefix concurrently (fan-out wave). |
| `--ctx-tokens` / `--max-tokens` | `0` / `64` | Fixed context (0 samples the measured distribution) / generation length. |
| `--max-model-len` / `--margin` | `0` (auto) / `1024` | Clamp sampled ctx under the serve's window. |
| `--api-key-env` / `--no-thinking` | — / off | Bearer token env-var name / disable hidden reasoning for thinking-by-default models. |
| `--thinking-mode` / `--reasoning-effort` | default / — | Select Qwen/Nemotron-style `enable_thinking` control or the OpenAI-compatible reasoning-effort mechanism used by GPT-OSS and Mistral. Conflicting mechanisms are rejected. |
| `--visible-answer-tokens` / `--reasoning-headroom-tokens` | `256` / `0` | For repaired quality evals, record a visible-answer allocation and reasoning headroom separately; their sum is sent as the endpoint's single `max_tokens` cap (maximum 65,536 tokens per completion). |
| `--eval-repetitions` / `--eval-min-pass-rate` | `1` / `1.0` | Repeat each built-in intelligence or external-suite item and aggregate it against an explicit stability threshold; repetitions are capped at 20. |
| `--timeout` / `--timeout-seconds` | `900` | Equivalent request-timeout spellings; the typed controller contract uses `--timeout-seconds`. |
| `--json-out` | — | Machine-readable summary for `eval benchmark external compare`. |
| `--recipe-out`, `--recipe-from-container`, `--recipe-intent`, `--recipe-mode`, `--recipe-status`, `--recipe-model` | — | Record a reproducible `[[recipe]]` block for the live serve (read back with `models recipes`). |
| `--suite-file SPECS_JSON` | — | With `--bakeoff`: run an externally-authored eval suite through deterministic text/regex and tool checks. Repaired specs may declare `visible_answer_tokens` plus `reasoning_headroom_tokens`; legacy `max_tokens` remains supported but cannot be mixed with those fields. Runs only the external suite unless `--suite` also selects built-ins. See [Benchmark results](BENCHMARKS.md). |

```bash
anvil-serving eval benchmark run --base-url http://127.0.0.1:30001/v1 --model local --burst 20 --no-thinking --confirm
```

For cross-model reasoning comparisons, use one control mechanism per model,
equal visible-answer allocations, explicit reasoning headroom, and repeated
attempts. Evidence records the full visible answer, finish reason, reasoning
field/size/token metadata when reported, the per-attempt budget, and classified
budget-exhaustion failures:

```bash
anvil-serving eval benchmark run --confirm --bakeoff \
  --base-url http://127.0.0.1:30002/v1 --model heavy-candidate \
  --candidate-id heavy-candidate --config-id reasoning-high-v2 \
  --suite-file tests/fixtures/eval-data/hf-mmlu-pro-10-repeated.suite.json \
  --reasoning-effort high \
  --visible-answer-tokens 256 --reasoning-headroom-tokens 2048 \
  --eval-repetitions 3 --eval-min-pass-rate 0.66 \
  --evidence-out evidence.json
```

The two allocations are an evidence contract, not a server-side partition:
OpenAI-compatible chat endpoints expose one total completion cap. A run using a
legacy per-item `max_tokens` is marked `legacy_total_budget=true` and should not
be mixed into a repaired cross-model comparison.

The work plan is bounded before the first request: at most 100 external evals,
500 aggregate attempts, and 2,000,000 requested quality tokens. External
`matches_regex` validators are limited to a 512-character conservative subset
of literals, anchors, boundaries, non-repeated character classes, `\s*`, and
the final-marker `[*]*`; grouping, alternation, wildcards, and general
quantifiers are rejected.

> **Importable entrypoints.** `preflight` and `benchmark` are dispatched through their module
> `main()` functions like the rest of the CLI. They remain deliberately self-contained enough for
> direct script-style checks from a checkout, but the supported operator path is the
> `anvil-serving eval preflight` / `anvil-serving eval benchmark run` command surface after `pip install -e .`.

### `eval benchmark evidence`

Read retained local benchmark JSON through a bounded, prompt-free summary
instead of writing one-off file searches or JSON extraction commands:

```bash
anvil-serving eval benchmark evidence list \
  --root docs/findings \
  --model thinkingcap \
  --suite mmlu \
  --limit 20

anvil-serving eval benchmark evidence show \
  docs/findings/2026-07-12-qwen36-protocol-v2-evidence/thinkingcap-mmlu-thinking-headroom4096.json \
  --format json

anvil-serving eval benchmark evidence compare \
  docs/findings/2026-07-12-qwen36-27b-heavy-bakeoff-evidence/thinkingcap-concurrency1.json \
  docs/findings/2026-07-12-qwen36-27b-heavy-bakeoff-evidence/thinkingcap-concurrency5.json
```

`list` recursively discovers recognized capacity, protocol-v2 quality, and MTP
A/B artifacts. It can filter by model/candidate/config substring, suite, or
artifact kind. `show` normalizes one artifact without returning prompts, full
model output, reasoning text, or arbitrary stored command/method text. `compare` reports normalized rows and sets
`comparable=false` when material workload fields differ, including context,
concurrency, protocol version, repetition count, visible-answer allocation, or
reasoning headroom. Missing controls or provenance also make a comparison
non-comparable: unknown is never treated as equal. Quality comparisons require
an immutable SHA-256 for every suite, and malformed counts, budgets, or control
combinations fail closed. Engine, recipe, and method differences are reported
separately as implementation provenance; they do not by themselves mean the
workload differs. For speculative-decoding A/B artifacts, the method hash is
workload identity and a mismatch is blocking. Invalid numeric values and unknown
thinking/control modes are rejected as comparison evidence. A non-comparable comparison exits non-zero by default;
`--allow-mismatch` acknowledges it for exploratory reporting. Use `--format
json` for a directly parseable result; null fields are omitted, and output,
directory traversal, and file reads are bounded.

### `eval benchmark external`

```
anvil-serving eval benchmark external {init|sources|fetch|import|list|report|export|compare} [flags]
```

Ingest, store, report, and compare external LLM inference benchmarks in a SQLite store
(`--db` on every subcommand). `fetch --source S --url U` pulls a snapshot; `import` loads a saved
JSON/CSV/Markdown/HTML file; `list`/`report` filter by `--gpu`/`--model`/`--source`;
`compare --local FILE` compares an `eval benchmark run --json-out` result against the store.
See [External benchmarks](EXTERNAL-BENCHMARKS.md).

```bash
anvil-serving eval benchmark external compare --local bench-fast.json --gpu "RTX 5090"
```

### `serves multiplex`

```
anvil-serving serves multiplex [--registry JSON] [--host 127.0.0.1] [--port 8000]
                          [--ram-cap-gb N] [--drain-timeout SECS] [--self-check]
```

On-demand OpenAI-compatible model multiplexer: one resident model per GPU, RAM-guarded swap with
a drain window for in-flight requests (`--drain-timeout`; `0` = swap immediately). The endpoint is
unauthenticated — keep the default loopback bind. `--self-check` runs the mock asserts and exits
(no server, no GPU).

```bash
anvil-serving serves multiplex --port 8000 --ram-cap-gb 48
```

### `init`

```
anvil-serving init [--model PATH] [--catalog-dir DIR] [--gpu IDX|UUID] [--served-name NAME]
                   [--tier-id ID] [--port N] [--context N] [--engine sglang|vllm]
                   [--disable-thinking] [--bind ADDR|--expose-lan] [--out-dir DIR]
```

Generic onboarding (ADR-0003): detects GPUs and a local model (default: the biggest loadable entry
from the `models sync` catalog in `--catalog-dir`, default `./model-library`), and writes a
consistent `docker-compose.yml` + `serves.toml` + `router.toml` + `operator-topology.toml`
bring-up, then prints the remaining manual steps (`serves up`, `serves status`, `router run`).

The generated topology is an offline-valid generic base. It uses stable `local-*` identifiers and
`127.0.0.1`, and does not inspect or record the machine hostname, operating system, network,
GPU UUIDs, credentials, or ambient command identity. Keep machine-specific addresses, host OS,
GPU roles, and authenticated controllers in a separate deployment overlay. Consequently,
OS-specific repairs and GPU-bound topology execution remain unavailable until those facts are
declared explicitly.

```bash
anvil-serving init --catalog-dir ./model-library --gpu 0
anvil-serving topology validate --topology ./operator-topology.toml
```

### `doctor`

```
anvil-serving doctor [--config PATH] [--no-config]
```

Environment preflight for a router deploy. Prints a per-check PASS/WARN/FAIL report and exits
non-zero iff a **required** check failed:

- Python >= 3.11 (required)
- `docker` present (required)
- `docker compose` v2 (required)
- NVIDIA container runtime registered with docker (advisory)
- GPU visibility via `nvidia-smi` (advisory)
- each tier's `/health` from a router config (default `./router.toml` if present; advisory)

```bash
anvil-serving doctor --config configs/example.toml
```

> ### `doctor` vs `host doctor`
>
> Both exist; they answer different questions.
>
> - **`anvil-serving doctor`** — *"Can this environment run a router deploy?"* Checks the Python
>   version, docker, docker compose v2, the NVIDIA container runtime, GPU visibility, and the
>   `/health` of each tier in a router config. Run it before `serve`/`serves up`, and after
>   changing configs or moving boxes.
> - **`anvil-serving host doctor`** — *"Is the WSL / Docker Desktop host itself healthy and sized
>   right?"* Inspects the host layer beneath docker (WSL memory cap, swap) and recommends a safe
>   WSL memory setting; its sibling actions (`host wsl-config`, `host restart-docker`,
>   `host reset-wsl`) apply the fix. Run it when models OOM at load, WSL is wedged, or you are
>   sizing a new Windows/WSL2 box (CLAUDE.md gotcha #3).
>
> Rule of thumb: `doctor` before every deploy; `host doctor` when the machine itself misbehaves.

### `host`

```
anvil-serving host {status|gpus|gpu-sharing|doctor|memory|wsl-config|restart-docker|reset-wsl|reclaim} [flags]
```

Owns the host (WSL / Docker Desktop) config, with backup/revert and safe caps.

| Action | What it does |
|--------|--------------|
| `status` | Return structured host RAM, Docker/WSL memory, and GPU status. |
| `gpus` | List observed NVIDIA indexes, names, and stable UUIDs. |
| `gpu-sharing inspect` | Return versioned JSON evidence for CUDA Green Context and MPS static-partition capability without creating contexts, starting daemons, or changing partitions. |
| `gpu-sharing probe` | Audit the reviewed Docker CUDA prerequisite service and, with `--confirm`, run it once on an exact GPU UUID without creating a CUDA context or workload. |
| `doctor` | Inspect the host + recommend a safe WSL memory cap. |
| `wsl-config` | Windows-native only. Edit `.wslconfig` memory/swap (`--memory GB`, `--swap GB`); backup + safe-cap refusal (`--force` overrides only that cap), `--revert` restores the newest anvil backup, and `--dry-run` shows the change. |
| `restart-docker` | Windows/macOS native only. Apply via a Docker Desktop restart; requires `--confirm`, while `--dry-run` performs no restart. |
| `reset-wsl` | Windows-native only. Un-wedge a hung WSL subsystem; requires `--confirm`, while `--dry-run` performs no reset. |
| `memory` | Show host RAM, the WSL VM's used / **page cache** / available (from `/proc/meminfo` inside the distro), and GPU VRAM. `--distro NAME` targets a specific distro. Note: querying `/proc/meminfo` starts the (default) distro if it is stopped — a cold boot can take longer than the 15 s probe timeout. |
| `reclaim` | Drop the WSL VM's page cache (`sync && echo 3 > /proc/sys/vm/drop_caches` as root). Confirm-gated per the CLI safety policy (`--confirm`); **refuses while a model load is actively streaming** (page cache growing fast) unless `--force` (which overrides only that refusal, not the confirmation gate). `--watch --threshold-gb N [--interval S]` runs a foreground watchdog that drops whenever the cache exceeds the threshold; `--dry-run` shows the command. |

```bash
anvil-serving host wsl-config --memory 64 --dry-run
anvil-serving host gpu-sharing inspect
anvil-serving host gpu-sharing probe --gpu-uuid GPU-... --dry-run
anvil-serving host memory
anvil-serving host reclaim --confirm                       # one-shot
anvil-serving host reclaim --watch --threshold-gb 40 --interval 30 --confirm   # bakeoff watchdog
```

Repeated 60–90 GB model-weight streams balloon the WSL2 VM's Linux page cache until Windows
itself starves (`autoMemoryReclaim=gradual` in `.wslconfig` lags load bursts) — `memory` shows
it, `reclaim` frees it. Both are Windows/WSL2-only and exit with a clear message elsewhere. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#windows-starves-for-ram-during-repeated-big-model-loads-wsl-page-cache).

With `--topology`, host actions resolve a declared `host` resource. Use `--target host:ID` when a
topology contains more than one host resource. OS/runtime compatibility is checked before local or
controller dispatch, so a Windows-only action cannot execute on Fakoli Mini/macOS.

#### `host gpu-sharing inspect`

```bash
anvil-serving host gpu-sharing inspect [--timeout SECONDS]
anvil-serving host gpu-sharing inspect \
  --topology ~/.anvil-serving/operator-topology.toml \
  --target host:fakoli-dark
```

This is a bounded, read-only capability inventory. It reuses `host gpus` discovery and the
topology's UUID-backed `gpu_roles`, queries extended `nvidia-smi` facts, checks CUDA driver/runtime
symbols without creating a context, discovers optional PyTorch/FlashInfer packages without importing
them, and sends only `get_server_list` and `lspart` to an already-running MPS control daemon. Missing
tools, malformed output, permission failures, and timeouts remain structured warnings. The payload's
`mutated_state` is always `false`.

The status vocabulary is `supported`, `unsupported`, `unavailable`, `unknown`,
`blocked_by_runtime_version`, and `blocked_by_environment`. `supported` means the inspected API/tool
surface and documented hardware gate are present; it does not prove that creating a Green Context or
an MPS partition works on that exact WSL2/Docker path. No container is started merely to inspect
container CUDA, so that field stays `unknown` when the command runs on the host.

#### `host gpu-sharing probe`

```bash
anvil-serving host gpu-sharing probe \
  --compose-file examples/fakoli-dark/docker-compose.experiment.yml \
  --gpu-uuid GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1 \
  --dry-run

anvil-serving host gpu-sharing probe \
  --compose-file examples/fakoli-dark/docker-compose.experiment.yml \
  --gpu-uuid GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1 \
  --confirm
```

The default is a preview. Before live execution, the command renders Compose with the requested
UUID and fails closed unless the service is profile-gated, read-only, unprivileged, portless,
capability-dropped, `no-new-privileges`, non-restarting, and pinned to that UUID in both
`CUDA_VISIBLE_DEVICES` and the NVIDIA device reservation. It also requires the exact reviewed CUDA
image digest, Linux platform, entrypoint/command, read-only source bind, and normalized source hash;
an image override or edited probe source is refused before container creation. `--confirm`
authorizes only the reviewed
prerequisite inspection: Docker may pull/cache the pinned image and creates a temporary container,
but the source creates no CUDA context, allocation, stream, event, kernel, MPS daemon, or partition.
Green Context creation and sustained work remain a separate maintenance-window operation,
especially when the selected GPU drives the Windows desktop.

### `collectors`

```bash
anvil-serving collectors configure --name NAME --endpoint URL --capability ID [--auth-env ENV] [--output PATH]
anvil-serving collectors validate --config PATH
anvil-serving collectors capabilities [--config PATH]
anvil-serving collectors inspect --config PATH [--timeout SECONDS]
```

Optional adapters use the bounded `anvil-json-v1` capability document and are never required for
ordinary inference. Configuration and capability reporting are offline. `inspect` performs one
read-only request and never installs, starts, stops, or manages an exporter or collector service.

Endpoints must use an explicit loopback, RFC1918, tailnet/CGNAT, or IPv6 ULA address. Public IPs,
hostnames, URL credentials, redirects, proxies, query strings, and fragments are refused. A
non-loopback endpoint requires `--auth-env`; only the environment-variable name is retained, and
the resolved bearer token is redacted from errors and output. Adapter failure is returned as a
degraded capability and does not alter routing or inference.

The inspected endpoint must return this bounded JSON capability document; no Prometheus scrape or
service-management protocol is implied:

```json
{"status":"ok","capabilities":["gpu-process-memory","gpu-container-attribution"]}
```

### `dashboard serve`

```bash
anvil-serving dashboard serve
anvil-serving dashboard serve --host 127.0.0.1 --port 8766
```

Serves a packaged, read-only single page for host, WSL/Docker boundary, GPU,
container, and configured service telemetry. The default URL is
`http://127.0.0.1:8766/`; no Node.js process or frontend build service is
required. The page exposes no lifecycle or configuration controls. A
non-loopback private/tailnet bind requires `--auth-env` naming a populated
bearer-token environment variable.

### `topology`

```
anvil-serving topology show --topology PATH [--topology-overlay PATH]
anvil-serving topology validate --topology PATH [--topology-overlay PATH]
anvil-serving topology resolve --topology PATH --command "host status" [target options]
```

`show` and `validate` are offline and do not require command identity. A partial deployment overlay
merges tables by stable `id` before validation. `resolve` performs the same owner, target, host OS,
runtime, capacity, and transport selection used by operational commands, but executes nothing. Its
human and JSON output includes command, execution, and resource hosts/runtimes, transport and
controller/resource endpoints, topology/overlay identity, and GPU role/UUID when applicable.

```bash
anvil-serving topology resolve \
  --topology examples/fakoli-dark/operator-topology.toml \
  --command "host status" --target host:fakoli-mini
```
---

## Quality loop

### `eval usage`

```
anvil-serving eval usage [--logs-dir DIR] [--out-dir DIR]
```

Turns your Claude Code session logs (default `~/.claude/projects`) into
`usage_aggregate.json` (usage percentiles) and `role_split.json` (role split) in `--out-dir`
(default: CWD) - the sizing inputs for your local serves.

```bash
anvil-serving eval usage --out-dir .
```

### `models score`

```
anvil-serving models score [--json] [--no-local] [--self-check]
```

Role-suitability scorer: derives coding/research/writing scores from real benchmarks (with
provenance) and recommends a model mixture per tier/role; it never fabricates a score. `--no-local`
skips local-catalog discovery (offline/fast), `--json` emits JSON instead of markdown.

```bash
anvil-serving models score --json > mixture.json
```

### `eval`

```
anvil-serving eval {preflight|benchmark|planning|bootstrap} [flags]
```

Unified shadow-eval harness. See [Serves & eval](SERVES-AND-EVAL.md).

| Subcommand | What it does |
|------------|--------------|
| `preflight` / `benchmark` | Run the correctness gate / throughput replay against a manifest tier: `--tier heavy` fills `--base-url`/`--model` from the serves manifest (or override them directly). Both live workloads require `--confirm`. |
| `planning` | Planning-capability bake-off; offline re-grade of committed eval-data by default, `--live` also runs generation against live serves; `--dir` selects the eval-data dir. |
| `bootstrap` | Replay committed eval fixtures into a quality profile (`--eval-data`, `--out`; the offline, CI-safe alternative to `calibrate`). |

```bash
anvil-serving eval preflight --tier fast --no-thinking --confirm
```

### `eval calibrate`

```
anvil-serving eval calibrate (--config PATH | --mode agentic|flexibility) --out PROFILE_JSON
                        --endpoint TIER=URL [--endpoint TIER=URL ...]
                        --i-understand-this-calls-real-tiers
                        [--eval-data DIR] [--max-tokens N]
```

Operator entry to the guarded write-back batch (ADR-0009): measures your configured **local**
tiers through their real backends, grades each output with the independent Agent-SDK judge, and
writes a **reviewable candidate** `profile.json`. It never auto-promotes, and it refuses to run
without both an explicit `--endpoint TIER=URL` confirmation for every measured local tier and the
`--i-understand-this-calls-real-tiers` flag — it is never triggered by CI. Promote the reviewed
artifact with `anvil-serving router promote`.

```bash
anvil-serving eval calibrate --config configs/example.toml --out candidate-profile.json \
  --endpoint fast-local=http://127.0.0.1:30001/v1 \
  --endpoint heavy-local=http://127.0.0.1:30000/v1 \
  --i-understand-this-calls-real-tiers
```

---

## Control plane & integrations

### `mcp`

```
anvil-serving mcp serve [--controller-url URL --auth-env ENV]
anvil-serving mcp tools
```

Stdio MCP server exposing the operational tool surface to agents — status
(`router_status`, `serves_status`, `reservation_status`, `doctor_summary`, `host_summary`,
`models_inventory`, `decision_summary`, `route_decision`), guarded lifecycle (`router_manage`, `router_promote`,
`serves_manage`, `serves_logs`, `router_logs`, `voice_manage`, `cache_prune_plan`), probes
(`preflight_probe`, `benchmark_probe`, `benchmark_artifact`), OpenClaw integration
(`openclaw_sync`, `openclaw_gateway_restart`), and external benchmark readers. Mutating or expensive
tools stay dry-run unless `confirm=true`; probe tools accept tokens only by env-var name and
restrict target URLs to loopback/private/tailnet hosts. `mcp tools` prints the tool catalog as JSON
and exits. With `--controller-url` + `--auth-env` (both or
neither), operational calls are forwarded to a remote `controller serve` instead of executing
locally — the split-host bridge.
Playbooks and per-tool contracts: [Operator playbooks](OPERATOR-PLAYBOOKS.md).

```bash
anvil-serving mcp serve --controller-url http://100.64.0.10:8765 --auth-env ANVIL_CONTROLLER_TOKEN
```

### `controller`

```
anvil-serving controller serve [--host 127.0.0.1] [--port 8765]
                               [--auth-token-env ANVIL_CONTROLLER_TOKEN]
                               [--allow-public-bind]
anvil-serving controller status [--url URL] [--auth-token-env ENV] [--timeout SECONDS]
```

Stdlib HTTP controller for tailnet-safe split-host MCP forwarding (ADR-0014): run it on the
anvil-serving host, bridge from the operator/gateway host with `mcp serve --controller-url`. Auth is
required on every bind. A public or wildcard bind additionally requires `--allow-public-bind`
and a token. `controller status` performs a bounded authenticated `/health` probe.

```bash
export ANVIL_CONTROLLER_TOKEN="<controller-secret>"
anvil-serving controller serve --host 100.64.0.10 --auth-token-env ANVIL_CONTROLLER_TOKEN
```

### `harness`

```
anvil-serving harness {sync|restart|status} openclaw [flags]
```

Owns the harness-side config: renders a harness's model/provider config **from** the live router
config so the two never drift (v1 target: OpenClaw). `sync` requires `--config <router.toml>`;
`restart` reloads the gateway, and `status` returns bounded gateway status. With topology options,
all three target the declared gateway owner and use its controller. Restart alone is marked for
explicit verified SSH recovery; normal `auto` execution never invokes SSH.

| Flag | Default | Meaning |
|------|---------|---------|
| `--config` | — | Router TOML to render presets + context limits from (required for `sync`). |
| `--out` | stdout | Write the harness config here. |
| `--base-url` | `http://127.0.0.1:8000/v1` | Router front door the harness dials. |
| `--api-key-env` | `ANVIL_ROUTER_TOKEN` | Token env-var name (referenced by name, never the secret). |
| `--gateway-host` / `--gateway-user` / `--gateway-path` | — | Push to a remote OpenClaw gateway over ssh (merge by default, backup taken); `--overwrite` replaces instead of merging. |
| `--restart` | off | After `sync`: restart the gateway (requires an applied target, not stdout). |
| `--dry-run` | off | Validate/render sync or show the fixed restart command without applying it. |
| `--skills` / `--skill-dir` | off | Also render/apply the workbench skill + sub-agent config. |
| `--voice`, `--voice-realtime-url`, `--voice-model`, `--voice-consult-*`, `--voice-api-key-env` | off | Also render/apply OpenClaw Talk realtime config for Anvil Voice. |
| `--timeout-seconds` | bounded | Cap each controller-side or local OpenClaw subprocess call. |
| `--max-output-bytes` | `65536` | For `status`, bound each captured output stream. |

```bash
anvil-serving harness sync openclaw --config configs/example.toml --gateway-host fakoli-mini --restart
anvil-serving harness status openclaw --topology examples/fakoli-dark/operator-topology.toml
```

---

## Voice

### `voice`

```
anvil-serving voice audio {up|down|status|logs} [flags]
anvil-serving voice proxy {run|up|down|restart|status|logs|bridge} [flags]
anvil-serving voice benchmark [flags]
anvil-serving voice profiles {list|validate} [flags]
anvil-serving voice sidecar {validate|command|compose} [flags]
```

Topology-owned realtime voice pipeline (VAD -> STT -> LLM -> TTS): `audio`
manages and inspects Dark-owned STT/TTS serves, while `proxy` manages the
Mini-owned Realtime process and its local audio forwarding bridge. `proxy run`
is foreground; `proxy up` starts the durable background service. `benchmark`
replays a recorded session end-to-end and reports latency
(`--candidate`, `--candidate-overlay`, `--evidence-out`), `profiles` lists/validates profile
overlays, and `proxy bridge` binds only Mini loopback ports and derives its Dark
targets from topology. Operational audio/proxy commands require `--topology`;
controller tools may instead use `ANVIL_VOICE_TOPOLOGY` on the owning host.
Full flag reference, topologies, and validation flows: [Voice pipeline](VOICE.md).

```bash
anvil-serving voice audio up \
  --topology ~/.anvil-serving/operator-topology.toml \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio --dry-run
```

### `voice sidecar`

```
anvil-serving voice sidecar {validate|command|compose} [--config TOML] [flags]
```

Validates and renders the Hugging Face speech-to-speech sidecar that uses anvil as a Chat
Completions backend. `validate` checks the sidecar manifest (`--json`), `command` renders the
host speech-to-speech command (`--with-auth` includes the router-token argument by env-var
reference, `--json` emits argv), and `compose` renders a Docker Compose service skeleton
(`--service-name`).

```bash
anvil-serving voice sidecar command --with-auth
```

## Migration from legacy commands

The following forms have been removed. They exit `2` before a handler is imported or
invoked, and print the replacement guidance to stderr. With CLI `--json`, they emit one
error envelope to stdout instead.

<!-- BEGIN GENERATED CLI TOMBSTONES -->
| Removed path | Replacement |
|---|---|
| `models recipe` | `models recipes` |
| `models recipe list` | `models recipes list` |
| `models recipe show` | `models recipes show` |
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
<!-- END GENERATED CLI TOMBSTONES -->

---

## See also

- [Getting started](GETTING-STARTED.md) — no-GPU smoke test and first real-tier run.
- [Configuration](CONFIGURATION.md) — router config reference (tiers, presets, modes, auth).
- [Serves & eval](SERVES-AND-EVAL.md) — serves manifest and the eval harness.
- [Operator playbooks](OPERATOR-PLAYBOOKS.md) — MCP/controller workflows and safety gates.
- [Voice pipeline](VOICE.md) — voice lifecycle, realtime server, and bridge topologies.
