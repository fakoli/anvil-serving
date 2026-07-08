# `serves` + `eval` — managing the model serves and running the evals

Two CLI verbs that close long-standing gaps: the router only ever *connected* to
the model containers (never controlled them), and the evals were three different
invocation styles with no single entry point.

## `anvil-serving serves` — model-serve lifecycle

The router (`anvil-serving serve`) talks to the GPU model serves as backends but
never starts or stops them. `serves` does, driven by a declarative manifest
(default [`examples/fakoli-dark/serves.toml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/serves.toml))
that is the single source of truth for *which container runs on which port as
which model*.

> **Operational prerequisite:** `serves` (and `deploy`) drive **Docker** and
> **Docker Compose v2** (`docker compose …`) to run the GPU model containers. These
> are *ops* requirements for standing up the serving substrate — **not** Python
> runtime dependencies. The router and the whole `anvil-serving` package stay
> **stdlib-only** (nothing added to `pip install`, nothing in the hot path); Docker
> Compose is a tool the operator installs alongside Docker + the NVIDIA runtime. Every
> serve is Docker-Compose-defined, so `serves up` is a drift-safe `docker compose up -d`
> — see [ADR-0002](adr/0002-serves-are-compose-defined.md).

```bash
anvil-serving serves status           # docker state + health + GPU memory per serve
anvil-serving serves down             # docker stop every serve (free the GPUs)
anvil-serving serves down fast        # stop one (by manifest name or container name)
anvil-serving serves up               # start them (see below)
anvil-serving serves up --dry-run     # print what would run, start nothing
anvil-serving serves --manifest X.toml status   # use a different topology
```

`up` is mechanism-aware by container state: **running** → left alone; **stopped**
(exited/created) → restarted with `docker start` (fast, no reload); **paused** →
`docker unpause`; **missing** → created fresh from the manifest's `up` command (a
`docker compose up -d <service>` per tier — **both** tiers are now Docker-Compose-
defined; see [ADR-0002](adr/0002-serves-are-compose-defined.md)). A container in an
exotic state (dead/restarting) is left for you to resolve rather than blindly
re-created. `down` likewise stops any state that holds the GPU (running/paused/
restarting), not just `running`.

> **Two notes on `up`:** (1) The manifest `up` is **executed** — it's parsed with
> `shlex` and run as an argv list (no shell, so paths with spaces are safe and
> there's no injection sink), but treat the manifest as trusted like a Makefile.
> (2) Every serve's `up` is `docker compose -f {dir}/docker-compose.yml up -d <service>`
> (`sglang` for heavy, `fast` for the gpt-oss vLLM tier). `docker compose up -d` is
> **drift-safe** — it natively recreates a service whose config has changed, closing
> the old bug where a stopped `docker run` container kept serving a stale model. This
> supersedes the ad-hoc `serve-fast-*.sh` scripts (kept only as reference); a
> first-time `serves up fast` no longer needs `bash` on PATH.

**Manifest entry:**
```toml
[[serve]]
name = "fast"                 # logical name (also accepted by down/up)
container = "vllm-gptoss"     # docker container name (== the compose service's container_name)
port = 30001
model = "gpt-oss-20b"         # served-model-name (used by `eval`)
engine = "vllm"               # vllm, sglang, or llamacpp
health = "/health"
up = "docker compose -f {dir}/docker-compose.yml up -d fast"   # {dir} = the manifest's dir
```

### Standing up a one-off experiment serve

Trying a new model (e.g. for the Blackwell lab notebook) does **not** need a hand-built
`docker run`. The parametrized
`examples/fakoli-dark/docker-compose.experiment.yml`
is one vLLM service driven by env vars, with the hard-won sm_120/WSL2 defaults baked in
(stable image, `VLLM_USE_V2_MODEL_RUNNER=0`, the D:-backed `vllm-hfcache` volume for ~15s
native loads, `CUDA_DEVICE_ORDER=PCI_BUS_ID`):

```bash
MODEL=RedHatAI/Qwen3-32B-NVFP4 \
GPU_UUID=GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1 \
PORT=30002 SERVED_NAME=qwen3-32b-nvfp4 \
  docker compose -f examples/fakoli-dark/docker-compose.experiment.yml up -d

# extra vLLM flags (parsers, trust-remote-code, …) ride in EXTRA_ARGS:
#   EXTRA_ARGS="--reasoning-parser qwen3 --tool-call-parser qwen3_coder --trust-remote-code"
```

`MODEL`, `GPU_UUID`, `SERVED_NAME`, `PORT`, and `EXTRA_ARGS` can all be overridden. With no
overrides, the generic experiment service uses the RTX 5090 and a loopback-only sandbox port so
`docker compose ... config --quiet` is a useful validation gate. Once it answers on `:{PORT}`,
point `anvil-serving eval preflight --base-url http://127.0.0.1:{PORT}/v1 --model {SERVED_NAME}`
at it.

### Fast-tier LLM bakeoff registry

The July 2026 Fast-tier bakeoff is tracked in
[`docs/findings/2026-07-08-fast-tier-llm-bakeoff.md`](findings/2026-07-08-fast-tier-llm-bakeoff.md)
and `configs/serve-recipes.toml`. Treat `status = "unverified"` rows as source-backed
candidate priors, not promotion evidence. A row graduates only after a local Fakoli Dark run
captures serve health, preflight/tool results, context behavior, voice-cycle latency, and rollback
proof through Anvil Serving commands.

The required candidate set for that bakeoff is:

- `nvidia/Qwen3.6-27B-NVFP4` as the current Fast baseline/control.
- `nvidia/Qwen3.6-35B-A3B-NVFP4`.
- `nvidia/Gemma-4-31B-IT-NVFP4`.
- `zai-org/GLM-4.7-Flash`.
- `mistralai/Devstral-Small-2-24B-Instruct-2512`.

`Qwen/Qwen3-30B-A3B-Instruct-2507` is optional fallback coverage only. Do not run candidate LLM
serves on Fakoli Mini, do not use the Heavy card for these small Fast experiments, and do not
promote a recipe into production routing from registry data alone.

The loaded-endpoint benchmark mode records one candidate/config run without starting or stopping
the serve:

```bash
anvil-serving benchmark \
  --bakeoff \
  --base-url http://127.0.0.1:39010/v1 \
  --model qwen36-35b-a3b-nvfp4 \
  --candidate-id qwen36-35b-a3b \
  --config-id vllm-nvfp4-32k \
  --context-targets 32768,65536 \
  --suite chat,context,tool \
  --source-recipe configs/serve-recipes.toml#qwen36-35b-a3b \
  --serve-command "anvil-serving serves --manifest examples/fakoli-dark/serves.toml up fast-qwen36-35b-a3b" \
  --evidence-out .anvil/evidence/fast-qwen36-35b-a3b-vllm-32k.json
```

The evidence JSON includes identity, source recipe, timing, context targets, tool/voice sections,
score inputs, and a `failures` list. Failed sub-checks stay in the same artifact as successful
checks so the final scoring pass can compare partial candidates without rerunning a loaded model.

## `anvil-serving eval` — one entry point for the evals

```bash
anvil-serving eval preflight --tier fast     # correctness gate vs the fast serve
anvil-serving eval benchmark --tier heavy    # throughput / request-replay
anvil-serving eval planning                  # planning bake-off (offline re-grade)
anvil-serving eval planning --live           # also re-generate against live serves
anvil-serving eval bootstrap                 # replay eval fixtures -> quality profile
```

- **`preflight` / `benchmark`** resolve `--base-url` and `--model` from the serves
  manifest, so `--tier fast` is enough. If that serve is down, you get an
  actionable hint (`start it: anvil-serving serves up fast`) instead of a
  connection error. Pass extra script flags after the options, or use
  `--base-url`/`--model` to target any endpoint.
- **`planning`** drives the planning-capability bake-off. The default `--offline`
  re-runs the deterministic structural grade + aggregate over the committed
  eval-data (no serves needed, byte-reproducible). `--live` first runs
  `eval_gen.py` against the heavy+fast serves (the frontier baseline and blind
  judge panel remain human-agent steps — see the eval README).
- **`bootstrap`** replays the committed eval fixtures into a quality-profile table
  (`anvil_serving.router.profile_bootstrap --replay`) — the eval-grounded seed for
  the router's routing policy (planning → cloud `allow`; locals `deny`).

### Typical flow

```bash
anvil-serving serves up                       # bring the models up
anvil-serving eval preflight --tier fast      # is it correct?
anvil-serving eval benchmark --tier fast      # is it fast enough?
anvil-serving serves down                     # free the GPUs when done
anvil-serving eval planning                   # re-grade the bake-off offline anytime
```
