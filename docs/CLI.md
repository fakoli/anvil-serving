# CLI Reference

`anvil-serving` is a single stdlib-only CLI (Python >= 3.11) that fronts every product surface:
the quality-gated router, the local GPU serve lifecycle, the model catalog, the quality loop, the
MCP/controller control plane, and the voice pipeline. This page is the complete verb reference for
v0.11.0. Run `anvil-serving --help` for the grouped live list, examples, and typo suggestions.
Run `anvil-serving <verb> --help`, or parser-backed focused action help such as
`anvil-serving router logs --help`, for the relevant flag set. Use `127.0.0.1` in local URLs,
never `localhost`.

## Global invocation

```
anvil-serving --help
anvil-serving --version
anvil-serving <command> --help
```

`-h`/`--help` prints the grouped root surface or focused parser help. `-V`/`--version` prints the
installed package version as `anvil-serving X.Y.Z`. Root help also names the canonical nested
workflows that are easiest to miss: `serves render`, `models cache prune`, `models score`,
`benchmark external`, and `voice sidecar`.

Topology-aware resource commands accept `--topology PATH`, `--command-host`, `--command-runtime`,
`--target`, and `--transport`. A model-free host rejects model workloads before handler launch.
The narrow exception requires both an `experimental-model` resource permitted by the topology's
capacity policy and the per-invocation `--experimental-model-workload` flag. Successful overrides
emit a warning and capacity audit fields; the flag alone never upgrades an ordinary model resource.
The M1 dispatcher executes only plans that resolve to `local`; a controller or SSH plan fails closed
before handler import. Use `mcp serve --controller-url` for supported remote operations until the
M2 CLI transport adapter lands.

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
| `voice` | Voice pipeline: STT/TTS serve lifecycle, realtime server, benchmark, bridge. | Voice |
| `voice sidecar` | Validate/render the HF speech-to-speech sidecar command and compose. | Voice |

Legacy aliases are documented in the compatibility section at the end of this file.

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
anvil-serving router {up|down|restart|reload|status|logs|token|promote} [flags]
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

Key flags: `--container` (default `anvil-router`), `--compose`, `--service`, `--dry-run`;
promote-only: `--profile`, `--config`, `--cfg-volume`, `--image`, `--profile-dest`,
`--config-dest`, `--no-reload`.

```bash
anvil-serving router promote --profile ./candidate-profile.json --dry-run
```

---

## Local serving tools

### `serves`

```
anvil-serving serves {status|up|down|rm|adopt|logs|render} [NAME ...] [flags]
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

Common flags: `--manifest`, `--dry-run`; `rm`/`adopt` also take `--yes`.

```bash
anvil-serving serves up heavy --manifest ./serves.toml --dry-run
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

```bash
anvil-serving serves render --model /models/qwen3-32b-nvfp4 --gpu 1 --context 131072 --served-name heavy
```

### `models`

```
anvil-serving models sync [--out DIR] [--hf-roots ROOTS] [--model-dirs DIRS]
anvil-serving models pull REPO_ID [--volume VOL] [--image IMG] [--revision R]
                                  [--include GLOB] [--exclude GLOB] [--token-env ENV] [--dry-run]
anvil-serving models recipe {list|show MODEL} [--registry TOML]
anvil-serving models cache prune [flags]
anvil-serving models score [flags]
```

Model catalog + fetch. `sync` scans HF caches and plain model dirs, pulls model cards, extracts
serving facts, and writes `cards/` + `INDEX.md` (default out dir `./model-library`). `pull`
downloads a HF repo into a **named docker volume** (default `vllm-hfcache`) via `hf download`
inside a container, avoiding the 9P bind-mount tax; `--token-env` forwards an HF token by env-var
name only. `recipe list`/`recipe show` read the recorded serve-recipe registry (default
`configs/serve-recipes.toml`) written by `benchmark --recipe-out`. Cache pruning and model scoring
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
anvil-serving eval preflight --base-url URL --model ID [--api-key-env ENV]
                        [--needle-ctx N] [--tool-batch N] [--no-thinking]
```

Correctness gate against any OpenAI-compatible endpoint, before trusting throughput: short coding
smoke, structured JSON, long-context needle retrieval (`--needle-ctx`, default 128000), and a
shared-prefix tool-calling batch (`--tool-batch`, default 20). `--no-thinking` injects
`chat_template_kwargs={"enable_thinking": false}` so thinking-by-default models (Qwen3.x, GLM)
don't false-fail with empty content. Exit code 0 = all pass, 1 = any fail.

```bash
anvil-serving eval preflight --base-url http://127.0.0.1:30000/v1 --model local --no-thinking
```

### `eval benchmark`

```
anvil-serving eval benchmark run --base-url URL --model ID [flags]
anvil-serving eval benchmark external {init|sources|fetch|import|list|report|export|compare} [flags]
```

Replays the measured Claude Code subagent request distribution and reports TTFT, end-to-end
latency, throughput, and a prefix-cache hit signal.

| Flag | Default | Meaning |
|------|---------|---------|
| `--requests` / `--concurrency` | `60` / `20` | Steady mixed load. |
| `--burst N` / `--shared-prefix-tokens` | `0` / `8000` | If burst >0, fire N requests sharing one prefix concurrently (fan-out wave). |
| `--ctx-tokens` / `--max-tokens` | `0` / `64` | Fixed context (0 samples the measured distribution) / generation length. |
| `--max-model-len` / `--margin` | `0` (auto) / `1024` | Clamp sampled ctx under the serve's window. |
| `--api-key-env` / `--no-thinking` | — / off | Bearer token env-var name / disable hidden reasoning for thinking-by-default models. |
| `--json-out` | — | Machine-readable summary for `eval benchmark external compare`. |
| `--recipe-out`, `--recipe-from-container`, `--recipe-intent`, `--recipe-mode`, `--recipe-status`, `--recipe-model` | — | Record a reproducible `[[recipe]]` block for the live serve (read back with `models recipe`). |

```bash
anvil-serving eval benchmark run --base-url http://127.0.0.1:30001/v1 --model local --burst 20 --no-thinking
```

> **Importable entrypoints.** `preflight` and `benchmark` are dispatched through their module
> `main()` functions like the rest of the CLI. They remain deliberately self-contained enough for
> direct script-style checks from a checkout, but the supported operator path is the
> `anvil-serving eval preflight` / `anvil-serving eval benchmark run` command surface after `pip install -e .`.

### `eval benchmark external`

```
anvil-serving eval benchmark external {init|sources|fetch|import|list|report|export|compare} [flags]
```

Ingest, store, report, and compare external LLM inference benchmarks in a SQLite store
(`--db` on every subcommand). `fetch --source S --url U` pulls a snapshot; `import` loads a saved
JSON/CSV/Markdown/HTML file; `list`/`report` filter by `--gpu`/`--model`/`--source`;
`compare --local FILE` compares an anvil `benchmark --json-out` result against the store.
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
consistent `docker-compose.yml` + `serves.toml` + `router.toml` bring-up, then prints the
remaining manual steps (`serves up`, `serves status`, `serve --config`).

```bash
anvil-serving init --catalog-dir ./model-library --gpu 0
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
anvil-serving host {doctor|wsl-config|restart-docker|reset-wsl|memory|reclaim} [flags]
```

Owns the host (WSL / Docker Desktop) config, with backup/revert and safe caps.

| Action | What it does |
|--------|--------------|
| `doctor` | Inspect the host + recommend a safe WSL memory cap. |
| `wsl-config` | Edit `.wslconfig` memory/swap (`--memory GB`, `--swap GB`); backup + safe-cap refusal (`--force` to override), `--revert` restores the newest anvil backup, `--dry-run` shows the change. |
| `restart-docker` | Apply via a Docker Desktop restart (confirm prompt; `--force` skips). |
| `reset-wsl` | Un-wedge a hung WSL subsystem (confirm prompt; `--force` skips). |
| `memory` | Show host RAM, the WSL VM's used / **page cache** / available (from `/proc/meminfo` inside the distro), and GPU VRAM. `--distro NAME` targets a specific distro. |
| `reclaim` | Drop the WSL VM's page cache (`sync && echo 3 > /proc/sys/vm/drop_caches` as root). Confirm-gated (`--confirm`/`--force`); **refuses while a model load is actively streaming** (page cache growing fast) unless `--force`. `--watch --threshold-gb N [--interval S]` runs a foreground watchdog that drops whenever the cache exceeds the threshold; `--dry-run` shows the command. |

```bash
anvil-serving host wsl-config --memory 64 --dry-run
anvil-serving host memory
anvil-serving host reclaim --confirm                       # one-shot
anvil-serving host reclaim --watch --threshold-gb 40 --interval 30 --confirm   # bakeoff watchdog
```

Repeated 60–90 GB model-weight streams balloon the WSL2 VM's Linux page cache until Windows
itself starves (`autoMemoryReclaim=gradual` in `.wslconfig` lags load bursts) — `memory` shows
it, `reclaim` frees it. Both are Windows/WSL2-only and exit with a clear message elsewhere. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#windows-starves-for-ram-during-repeated-big-model-loads-wsl-page-cache).

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
| `preflight` / `benchmark` | Run the correctness gate / throughput replay against a manifest tier: `--tier heavy` fills `--base-url`/`--model` from the serves manifest (or override them directly); unknown flags pass through to the underlying script. |
| `planning` | Planning-capability bake-off; offline re-grade of committed eval-data by default, `--live` also runs generation against live serves; `--dir` selects the eval-data dir. |
| `bootstrap` | Replay committed eval fixtures into a quality profile (`--eval-data`, `--out`; the offline, CI-safe alternative to `calibrate`). |

```bash
anvil-serving eval preflight --tier fast --no-thinking
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
(`router_status`, `serves_status`, `doctor_summary`, `host_summary`, `models_inventory`,
`decision_summary`, `route_decision`), guarded lifecycle (`router_manage`, `router_promote`,
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
anvil-serving harness {sync|restart} openclaw [flags]
```

Owns the harness-side config: renders a harness's model/provider config **from** the live router
config so the two never drift (v1 target: OpenClaw). `sync` requires `--config <router.toml>`;
`restart` reloads the gateway (locally or over ssh) and takes only `--gateway-host`/`--gateway-user`.

| Flag | Default | Meaning |
|------|---------|---------|
| `--config` | — | Router TOML to render presets + context limits from (required for `sync`). |
| `--out` | stdout | Write the harness config here. |
| `--base-url` | `http://127.0.0.1:8000/v1` | Router front door the harness dials. |
| `--api-key-env` | `ANVIL_ROUTER_TOKEN` | Token env-var name (referenced by name, never the secret). |
| `--gateway-host` / `--gateway-user` / `--gateway-path` | — | Push to a remote OpenClaw gateway over ssh (merge by default, backup taken); `--overwrite` replaces instead of merging. |
| `--restart` | off | After `sync`: restart the gateway (requires an applied target, not stdout). |
| `--skills` / `--skill-dir` | off | Also render/apply the workbench skill + sub-agent config. |
| `--voice`, `--voice-realtime-url`, `--voice-model`, `--voice-consult-*`, `--voice-api-key-env` | off | Also render/apply OpenClaw Talk realtime config for Anvil Voice. |
| `--timeout-seconds` | bounded | Cap each ssh/scp/openclaw subprocess call. |

```bash
anvil-serving harness sync openclaw --config configs/example.toml --gateway-host fakoli-mini --restart
```

---

## Voice

### `voice`

```
anvil-serving voice audio {up|down} [flags]
anvil-serving voice proxy {run|bridge} [flags]
anvil-serving voice benchmark [flags]
anvil-serving voice profiles {list|validate} [flags]
anvil-serving voice sidecar {validate|command|compose} [flags]
```

Local realtime voice pipeline (VAD -> STT -> LLM -> TTS): `audio up`/`audio down` manage the STT/TTS serves
from a voice manifest (`--config`, `--profile`, `--dry-run`; default
`~/.anvil-serving/voice.toml` when present), `proxy run` starts the realtime server in
the foreground, `benchmark` replays a recorded session end-to-end and reports latency
(`--candidate`, `--candidate-overlay`, `--evidence-out`), `profiles` lists/validates profile
overlays, and `proxy bridge` forwards STT/TTS TCP ports to local audio endpoints (loopback by default;
non-loopback binds require explicit acknowledgement flags). Full flag reference, topologies, and
validation flows: [Voice pipeline](VOICE.md).

```bash
anvil-serving voice audio up --profile dark-audio --dry-run
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

| Removed path | Replacement |
|---|---|
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
| `models recipe list\|show` | `models recipes list\|show` |
| `voice-sidecar` | `voice sidecar` |
| `voice up\|down` | `voice audio up\|down` |
| `voice run\|bridge` | `voice proxy run\|bridge` |
| `voice start\|stop` | `voice audio up\|down` |
| `onboard` | `init` |
| `mcp list-tools` or `mcp --list-tools` | `mcp tools` |
| bare `mcp` | `mcp serve` |
| `controller serve --allow-unauthenticated-loopback` | Configure the token named by `--auth-token-env` |

---

## See also

- [Getting started](GETTING-STARTED.md) — no-GPU smoke test and first real-tier run.
- [Configuration](CONFIGURATION.md) — router config reference (tiers, presets, modes, auth).
- [Serves & eval](SERVES-AND-EVAL.md) — serves manifest and the eval harness.
- [Operator playbooks](OPERATOR-PLAYBOOKS.md) — MCP/controller workflows and safety gates.
- [Voice pipeline](VOICE.md) — voice lifecycle, realtime server, and bridge topologies.
