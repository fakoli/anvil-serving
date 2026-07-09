# CLI Reference

`anvil-serving` is a single stdlib-only CLI (Python >= 3.11) that fronts every product surface:
the quality-gated router, the local GPU serve lifecycle, the model catalog, the quality loop, the
MCP/controller control plane, and the voice pipeline. This page is the complete verb reference for
v0.11.0. Run `anvil-serving --help` for the grouped live list, examples, and typo suggestions.
Run `anvil-serving <verb> --help`, or parser-backed focused action help such as
`anvil-serving router logs --help`, for the relevant flag set. Use `127.0.0.1` in local URLs,
never `localhost`.

## Command index

| Verb | Purpose | Group |
|------|---------|-------|
| `serve` | Start the quality-gated router front door on `127.0.0.1:8000`. | Data plane |
| `router` | Manage the deployed (containerized) router: lifecycle, token, promote. | Data plane |
| `serves` | Stop/start/inspect the local GPU model serves from a serves manifest. | Local serving tools |
| `models` | Model catalog (`sync`), HF downloads into a docker volume (`pull`), serve recipes (`recipe`). | Local serving tools |
| `profile` | Turn Claude Code logs into a usage baseline + sizing inputs. | Local serving tools |
| `preflight` | Correctness gate against any OpenAI-compatible endpoint. | Local serving tools |
| `benchmark` | Replay the measured request distribution (TTFT, throughput, prefix cache). | Local serving tools |
| `external-bench` | Ingest, store, report, and compare external inference benchmarks. | Local serving tools |
| `multiplexer` | Single-resident model swap server on one GPU (RAM-guarded). | Local serving tools |
| `cache-prune` | Plan (and optionally execute) pruning of local HF model caches. | Local serving tools |
| `deploy` | Render a tuned SGLang/vLLM docker-compose for a GPU + model. | Local serving tools |
| `init` (alias `onboard`) | Detect GPUs + a model; write compose + serves.toml + router.toml. | Local serving tools |
| `doctor` | Environment preflight for a router deploy (Python, docker, GPU, tier health). | Local serving tools |
| `host` | Own the WSL / Docker Desktop host config (inspect, cap, restart, reset). | Local serving tools |
| `eval` | Unified eval harness: preflight / benchmark / planning / bootstrap. | Quality loop |
| `calibrate` | Guarded write-back batch: measure local tiers, judge, write a candidate profile. | Quality loop |
| `score` | Role-suitability scorer over real benchmarks; recommends a mixture. | Quality loop |
| `mcp` | Stdio MCP server (and remote-controller proxy) for operational tools. | Control plane & integrations |
| `controller` | Token-authenticated HTTP controller for split-host MCP forwarding. | Control plane & integrations |
| `harness` | Render/apply harness-side config (OpenClaw) from the live router config. | Control plane & integrations |
| `voice` | Voice pipeline: STT/TTS serve lifecycle, realtime server, benchmark, bridge. | Voice |
| `voice-sidecar` | Validate/render the HF speech-to-speech sidecar command and compose. | Voice |

---

## Data plane

### `serve`

```
anvil-serving serve (--config PATH | --mode agentic|flexibility) [--host HOST] [--port PORT]
```

Starts the protocol-standard front door bound to the tiers in a router config
(config -> per-tier backends -> front door). A config selector is required — `--config`, `--mode`,
or the `ANVIL_MODE`/`ANVIL_MODES_CONFIG` environment variables; bare `serve` with none of them
set is a usage error (the router never silently boots a default). See the
[Configuration reference](CONFIGURATION.md) for the precedence chain.

| Flag | Default | Meaning |
|------|---------|---------|
| `--config PATH` | — | Load this exact router TOML; bypasses the mode resolver. |
| `--mode {agentic,flexibility}` | — | Resolve the global mode to its config (ADR-0011). Precedence: `--mode` > `ANVIL_MODE` > `[modes].active_mode` > default; a mode maps to a file via `ANVIL_CONFIG_<MODE>` > a `[modes]` manifest (`ANVIL_MODES_CONFIG`) > built-in default. |
| `--host` | `127.0.0.1` | Bind host. Configure `[server].auth_env` before any non-loopback bind — see `SECURITY.md`. |
| `--port` | `8000` | Bind port. |

```bash
anvil-serving serve --config configs/example.toml
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
| `restart` / `reload` / `status` | Restart the container / signal a config reload / show container + health status. |
| `logs` / `token` | `docker logs` (`--tail`, `--since`, `--follow`) / print the router bearer token. |
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
anvil-serving serves {status|up|down|rm|adopt|logs} [NAME ...] [flags]
```

Stop/start/inspect the local GPU model serves declared in a serves manifest.
When `--manifest` is omitted, discovery checks `./serves.toml` first, then
`~/.anvil-serving/serves.toml`. The router connects to these; this verb manages them.
See [Serves & eval](SERVES-AND-EVAL.md) for the manifest format and workflows.

| Action | What it does |
|--------|--------------|
| `status` | Docker + `/health` state for every manifest serve. |
| `up` | Start (restart/unpause/run the manifest `up`); `--recreate` forces a fresh `up`; `up --compose FILE` brings up an ad-hoc compose serve not in the manifest. |
| `down` | `docker stop` the serves. |
| `rm` | `docker rm -f`; an unrecognised name is treated literally as a container (evict an experiment squatting a port). |
| `adopt` | Bring an externally-started manifest serve under compose management. |
| `logs` | `docker logs` for one serve (`--tail`, `--since`, `--follow`). |

Common flags: `--manifest`, `--dry-run`.

```bash
anvil-serving serves up heavy --manifest ./serves.toml --dry-run
```

### `models`

```
anvil-serving models sync [--out DIR] [--hf-roots ROOTS] [--model-dirs DIRS]
anvil-serving models pull REPO_ID [--volume VOL] [--image IMG] [--revision R]
                                  [--include GLOB] [--exclude GLOB] [--token-env ENV] [--dry-run]
anvil-serving models recipe {list|show MODEL} [--registry TOML]
```

Model catalog + fetch. `sync` scans HF caches and plain model dirs, pulls model cards, extracts
serving facts, and writes `cards/` + `INDEX.md` (default out dir `./model-library`). `pull`
downloads a HF repo into a **named docker volume** (default `vllm-hfcache`) via `hf download`
inside a container, avoiding the 9P bind-mount tax; `--token-env` forwards an HF token by env-var
name only. `recipe list`/`recipe show` read the recorded serve-recipe registry (default
`configs/serve-recipes.toml`) written by `benchmark --recipe-out`.

```bash
anvil-serving models pull openai/gpt-oss-120b --volume vllm-hfcache --dry-run
```

### `profile`

```
anvil-serving profile [--logs-dir DIR] [--out-dir DIR]
```

Turns your Claude Code session logs (default `~/.claude/projects`) into
`usage_aggregate.json` (usage percentiles) and `role_split.json` (role split) in `--out-dir`
(default: CWD) — the sizing inputs for your local serves.

```bash
anvil-serving profile --out-dir .
```

### `preflight`

```
anvil-serving preflight --base-url URL --model ID [--api-key-env ENV]
                        [--needle-ctx N] [--tool-batch N] [--no-thinking]
```

Correctness gate against any OpenAI-compatible endpoint, before trusting throughput: short coding
smoke, structured JSON, long-context needle retrieval (`--needle-ctx`, default 128000), and a
shared-prefix tool-calling batch (`--tool-batch`, default 20). `--no-thinking` injects
`chat_template_kwargs={"enable_thinking": false}` so thinking-by-default models (Qwen3.x, GLM)
don't false-fail with empty content. Exit code 0 = all pass, 1 = any fail.

```bash
anvil-serving preflight --base-url http://127.0.0.1:30000/v1 --model local --no-thinking
```

### `benchmark`

```
anvil-serving benchmark --base-url URL --model ID [flags]
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
| `--json-out` | — | Machine-readable summary for `external-bench compare`. |
| `--recipe-out`, `--recipe-from-container`, `--recipe-intent`, `--recipe-mode`, `--recipe-status`, `--recipe-model` | — | Record a reproducible `[[recipe]]` block for the live serve (read back with `models recipe`). |

```bash
anvil-serving benchmark --base-url http://127.0.0.1:30001/v1 --model local --burst 20 --no-thinking
```

> **Standalone scripts.** `preflight` and `benchmark` are dispatched by the CLI as plain scripts
> (`cli.py` runs `python anvil_serving/preflight.py ...` in a subprocess), and both are
> deliberately self-contained: `python anvil_serving/preflight.py --base-url ... --model ...`
> works from a checkout without installing the package. Every other verb imports its module and
> needs the package importable (`pip install -e .`).

### `external-bench`

```
anvil-serving external-bench {init|sources|fetch|import|list|report|export|compare} [flags]
```

Ingest, store, report, and compare external LLM inference benchmarks in a SQLite store
(`--db` on every subcommand). `fetch --source S --url U` pulls a snapshot; `import` loads a saved
JSON/CSV/Markdown/HTML file; `list`/`report` filter by `--gpu`/`--model`/`--source`;
`compare --local FILE` compares an anvil `benchmark --json-out` result against the store.
See [External benchmarks](EXTERNAL-BENCHMARKS.md).

```bash
anvil-serving external-bench compare --local bench-fast.json --gpu "RTX 5090"
```

### `multiplexer`

```
anvil-serving multiplexer [--registry JSON] [--host 127.0.0.1] [--port 8000]
                          [--ram-cap-gb N] [--drain-timeout SECS] [--self-check]
```

On-demand OpenAI-compatible model multiplexer: one resident model per GPU, RAM-guarded swap with
a drain window for in-flight requests (`--drain-timeout`; `0` = swap immediately). The endpoint is
unauthenticated — keep the default loopback bind. `--self-check` runs the mock asserts and exits
(no server, no GPU).

```bash
anvil-serving multiplexer --port 8000 --ram-cap-gb 48
```

### `cache-prune`

```
anvil-serving cache-prune [--mixture CSV] [--json] [--execute --yes] [--dry-run]
                          [--include-servable] [--allow-empty-mixture] [--self-check]
```

Plans (and only with explicit gates, executes) pruning of local HF model caches. Default is a safe
dry-run; real deletion requires **both** `--execute` and `--yes`, deletes only dead-everywhere
candidates unless `--include-servable`, and refuses a broad wipe with an empty `--mixture` unless
`--allow-empty-mixture`. `--mixture` lists model ids to protect.

```bash
anvil-serving cache-prune --mixture openai/gpt-oss-120b,Qwen/Qwen3-32B --json
```

### `deploy`

```
anvil-serving deploy --model PATH [--gpu IDX|UUID] [--context N] [--served-name NAME]
                     [--port N] [--out FILE] [--engine sglang|vllm] [flags]
```

Renders a tuned SGLang/vLLM docker-compose for a GPU + model, appends a `[[serve]]` entry to the
serves manifest (`--manifest-out`, default `./serves.toml`; `--no-manifest` skips), and prints a
router-tier stub to paste into your config. Key flags: `--gpu` (index or GPU-UUID, default `0`),
`--context` (default `131072`), `--served-name`/`--port`/`--out` (defaults `local-specialist` /
`30000` / `docker-compose.yml`), `--engine sglang|vllm` (default: inferred from the model's
`config.json` weight format, else sglang), `--gpu-mem-util` (vLLM only, default `0.90`),
`--disable-thinking` + `--model-facts` (auto-disable a thinking-by-default model from a
`models sync` card), `--tier-id`, and `--bind`/`--expose-lan` (default `127.0.0.1`; `--expose-lan`
= `0.0.0.0` — see `SECURITY.md`).

```bash
anvil-serving deploy --model /models/qwen3-32b-nvfp4 --gpu 1 --context 131072 --served-name heavy
```

### `init` (alias `onboard`)

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
anvil-serving host {doctor|wsl-config|restart-docker|reset-wsl} [flags]
```

Owns the host (WSL / Docker Desktop) config, with backup/revert and safe caps.

| Action | What it does |
|--------|--------------|
| `doctor` | Inspect the host + recommend a safe WSL memory cap. |
| `wsl-config` | Edit `.wslconfig` memory/swap (`--memory GB`, `--swap GB`); backup + safe-cap refusal (`--force` to override), `--revert` restores the newest anvil backup, `--dry-run` shows the change. |
| `restart-docker` | Apply via a Docker Desktop restart (confirm prompt; `--force` skips). |
| `reset-wsl` | Un-wedge a hung WSL subsystem (confirm prompt; `--force` skips). |

```bash
anvil-serving host wsl-config --memory 64 --dry-run
```

---

## Quality loop

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

### `calibrate`

```
anvil-serving calibrate (--config PATH | --mode agentic|flexibility) --out PROFILE_JSON
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
anvil-serving calibrate --config configs/example.toml --out candidate-profile.json \
  --endpoint fast-local=http://127.0.0.1:30001/v1 \
  --endpoint heavy-local=http://127.0.0.1:30000/v1 \
  --i-understand-this-calls-real-tiers
```

### `score`

```
anvil-serving score [--json] [--no-local] [--self-check]
```

Role-suitability scorer: derives coding/research/writing scores from real benchmarks (with
provenance) and recommends a model mixture per tier/role; it never fabricates a score. `--no-local`
skips local-catalog discovery (offline/fast), `--json` emits JSON instead of markdown.

```bash
anvil-serving score --json > mixture.json
```

---

## Control plane & integrations

### `mcp`

```
anvil-serving mcp [--list-tools|list-tools] [--controller-url URL --auth-env ENV]
```

Stdio MCP server exposing the operational tool surface to agents — status
(`router_status`, `serves_status`, `doctor_summary`, `host_summary`, `models_inventory`,
`decision_summary`, `route_decision`), guarded lifecycle (`router_manage`, `router_promote`,
`serves_manage`, `serves_logs`, `router_logs`, `voice_manage`, `cache_prune_plan`), probes
(`preflight_probe`, `benchmark_probe`, `benchmark_artifact`), OpenClaw integration
(`openclaw_sync`, `openclaw_gateway_restart`), and external-bench readers. Mutating or expensive
tools stay dry-run unless `confirm=true`; probe tools accept tokens only by env-var name and
restrict target URLs to loopback/private/tailnet hosts. `--list-tools` or positional `list-tools`
prints the tool catalog as JSON and exits. With `--controller-url` + `--auth-env` (both or
neither), operational calls are forwarded to a remote `controller serve` instead of executing
locally — the split-host bridge.
Playbooks and per-tool contracts: [Operator playbooks](OPERATOR-PLAYBOOKS.md).

```bash
anvil-serving mcp --controller-url http://100.64.0.10:8765 --auth-env ANVIL_CONTROLLER_TOKEN
```

### `controller`

```
anvil-serving controller serve [--host 127.0.0.1] [--port 8765]
                               [--auth-token-env ANVIL_CONTROLLER_TOKEN]
                               [--allow-public-bind] [--allow-unauthenticated-loopback]
```

Stdlib HTTP controller for tailnet-safe split-host MCP forwarding (ADR-0014): run it on the
anvil-serving host, bridge from the operator/gateway host with `mcp --controller-url`. Auth is
required by default even on loopback (`--allow-unauthenticated-loopback` is development-only);
a public or wildcard bind additionally requires `--allow-public-bind` *and* a token.

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
anvil-serving voice {up|start|down|stop|run|benchmark|profiles|bridge} [flags]
```

Local realtime voice pipeline (VAD -> STT -> LLM -> TTS): `up`/`down` manage the STT/TTS serves
from a voice manifest (`--config`, `--profile`, `--dry-run`; default
`~/.anvil-serving/voice.toml` when present), `run` starts the realtime server in
the foreground, `benchmark` replays a recorded session end-to-end and reports latency
(`--candidate`, `--candidate-overlay`, `--evidence-out`), `profiles` lists/validates profile
overlays, and `bridge` forwards STT/TTS TCP ports to local audio endpoints (loopback by default;
non-loopback binds require explicit acknowledgement flags). Full flag reference, topologies, and
validation flows: [Voice pipeline](VOICE.md).

```bash
anvil-serving voice up --profile dark-audio --dry-run
```

### `voice sidecar` / `voice-sidecar`

```
anvil-serving voice sidecar {validate|command|compose} [--config TOML] [flags]
anvil-serving voice-sidecar {validate|command|compose} [--config TOML] [flags]
```

Validates and renders the Hugging Face speech-to-speech sidecar that uses anvil as a Chat
Completions backend. Prefer the nested `voice sidecar` form when working inside the voice command
family; `voice-sidecar` remains a compatibility alias. `validate` checks the sidecar manifest
(`--json`), `command` renders the host speech-to-speech command (`--with-auth` includes the
router-token argument by env-var reference, `--json` emits argv), `compose` renders a Docker
Compose service skeleton (`--service-name`).

```bash
anvil-serving voice sidecar command --with-auth
```

---

## See also

- [Getting started](GETTING-STARTED.md) — no-GPU smoke test and first real-tier run.
- [Configuration](CONFIGURATION.md) — router config reference (tiers, presets, modes, auth).
- [Serves & eval](SERVES-AND-EVAL.md) — serves manifest and the eval harness.
- [Operator playbooks](OPERATOR-PLAYBOOKS.md) — MCP/controller workflows and safety gates.
- [Voice pipeline](VOICE.md) — voice lifecycle, realtime server, and bridge topologies.
