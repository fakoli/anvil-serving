# `serves` + `eval` — managing the model serves and running the evals

Two CLI verbs that close long-standing gaps: the router only ever *connected* to
the model containers (never controlled them), and the evals were three different
invocation styles with no single entry point.

## `anvil-serving serves` — model-serve lifecycle

The router (`anvil-serving router run`) talks to the GPU model serves as backends but
never starts or stops them. `serves` does, driven by a declarative manifest
that is the single source of truth for *which container runs on which port as
which model*. When `--manifest` is omitted, `serves` checks `./serves.toml`
first and then `~/.anvil-serving/serves.toml`; the checked-in
[`examples/fakoli-dark/serves.toml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/serves.toml)
is the source-controlled reference template for the Fakoli Dark topology.

> **Operational prerequisite:** `serves` and `serves render` drive **Docker** and
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

For a host-level deployment, keep the live copies in `~/.anvil-serving` so
daily operations do not depend on the current checkout:

```bash
mkdir -p ~/.anvil-serving
cp examples/fakoli-dark/serves.toml ~/.anvil-serving/serves.toml
cp examples/fakoli-dark/docker-compose.yml ~/.anvil-serving/docker-compose.yml
cp examples/fakoli-dark/docker-compose.experiment.yml ~/.anvil-serving/docker-compose.experiment.yml
```

`serves` fills missing command environment from `~/.env`, then
`~/.anvil-serving/.env`, then a manifest-adjacent `.env`; shell environment
variables still win. Keep real tokens in env files or the shell, not in TOML.

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
engine = "vllm"               # vllm, sglang, llamacpp, q36 — or a truthful non-chat-LLM label:
                              # audio (STT/TTS), embedding, reranker (ADR-0017 §7),
                              # image (the ComfyUI tenant, gpu-reservations:T012)
health = "/health"
groups = ["fast-only", "llm-stack"]  # OPTIONAL: serve groups (see below); absent = none
up = "docker compose -f {dir}/docker-compose.yml up -d fast"   # {dir} = the manifest's dir
```

### Serve groups (`--group`)

An optional `groups` list tags a serve into any number of named groups (a serve
may belong to many; the members are non-empty strings; the reserved name `all`
must not be authored — it implicitly means every serve). `anvil-serving serves
{up,down,status} --group NAME` (repeatable) then acts on every serve tagged
`NAME`, unioning with any positional names, and `anvil-serving serves groups`
lists the catalog (`--json` for tooling).

Group resolution spans the whole **manifest set** — every `serves*.toml` in the
manifest's directory, sorted by path, de-duped by container (the lifecycle-owning
entry that declares `up` wins over a read-only ledger mirror). So `--group voice`
reaches `stt`/`tts` in `serves.voice.toml` even when `--manifest` points at
`serves.toml`, and `--group comfy` reaches the ComfyUI tenant in
`serves.comfyui.toml`. Every member of a group `up` still passes the same
ADR-0017 reservation admission (an over-budget member is refused with the ledger,
never bypassed).

The shipped `examples/fakoli-dark` manifests author: `voice` (stt, tts),
`fast-only` (fast), `heavy-only` (heavy), `embedding` (embeddings, reranker),
`llm-stack` (heavy, fast, embeddings, reranker, ocr, vision), `comfy`
(comfyui), and `q36-experiment` (q36-pro6000). Other experiment/candidate
serves are deliberately left untagged.

```bash
anvil-serving serves groups --manifest examples/fakoli-dark/serves.toml
anvil-serving serves up --group embedding --manifest examples/fakoli-dark/serves.toml
anvil-serving serves status --group llm-stack --manifest examples/fakoli-dark/serves.toml
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

The dedicated q36 engine has its own digest-pinned CUDA 13.1.2 build and managed
manifest. It uses the exact GGUF snapshot from the shared `vllm-hfcache` volume,
binds loopback-only on port 39040, and is mutually exclusive with `heavy` on the
RTX PRO 6000:

```powershell
anvil-serving serves down --manifest examples/fakoli-dark/serves.toml heavy --confirm
anvil-serving serves up --manifest examples/fakoli-dark/serves.q36.toml q36-pro6000 --confirm
anvil-serving serves status --manifest examples/fakoli-dark/serves.q36.toml q36-pro6000
anvil-serving serves down --manifest examples/fakoli-dark/serves.q36.toml q36-pro6000 --confirm
```

The image recipe and its source/image pins are documented in
`examples/fakoli-dark/q36/README.md`.

#### Docker Desktop/WSL CUDA validation layers

Do not use the native Windows CUDA toolkit or the default Ubuntu WSL distro as a proxy for the
runtime inside a Docker serve. They are distinct layers:

1. Native Windows owns the NVIDIA display/compute driver that WSL exposes.
2. An ordinary WSL distro can inspect the WSL `libcuda` stub and any CUDA runtime installed in that
   distro, but that result is not container evidence.
3. The `docker-desktop` WSL VM owns Docker's Linux `dockerd`/`containerd` and GPU passthrough. It is
   deliberately minimal: `/dev/dxg` and the WSL `libcuda.so` stub may be present while `nvidia-smi`,
   `nvcc`, `libcudart`, and MPS are absent.
4. The selected container image supplies `libcudart`, compiler headers/tools, and optional CUDA
   utilities. Validate their versions and symbols inside that exact, digest-pinned image.

Therefore a CUDA capability claim for a serve or experiment needs both substrate evidence from
Docker Desktop and image-local evidence from a Compose-managed container pinned by GPU UUID with
`CUDA_DEVICE_ORDER=PCI_BUS_ID`. Runtime observations stay in evidence artifacts/findings; do not
write them into stable topology identity.

The read-only Green Context prerequisite recipe is now owned by the Anvil Serving CLI. Preview its
fully audited command first, then explicitly confirm the one-shot container:

```powershell
anvil-serving host gpu-sharing probe `
  --compose-file examples/fakoli-dark/docker-compose.experiment.yml `
  --gpu-uuid GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1 `
  --dry-run

anvil-serving host gpu-sharing probe `
  --compose-file examples/fakoli-dark/docker-compose.experiment.yml `
  --gpu-uuid GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1 `
  --confirm
```

The underlying profile-gated Compose command remains useful for debugging the product wrapper:

```powershell
docker compose -f examples/fakoli-dark/docker-compose.experiment.yml `
  --profile gpu-sharing-probe run --rm --no-deps gpu-sharing-inspect
```

It compiles in a temporary executable filesystem, queries the UUID-selected RTX 5090 and CUDA
Runtime/Driver symbols, creates no CUDA context or workload, and removes its own container. Direct
Compose invocation is a diagnostic fallback; the guarded Anvil Serving verb is the operator
contract. Context creation is still outside that contract.

### Fast-tier LLM bakeoff registry

The July 2026 Fast-tier bakeoff is tracked in
[`docs/findings/2026-07-08-fast-tier-llm-bakeoff.md`](findings/2026-07-08-fast-tier-llm-bakeoff.md)
and `configs/serve-recipes.toml`. The human-gated recommendation from that bakeoff promotes
`nvidia/Qwen3.6-35B-A3B-NVFP4` as the production Fast recipe on port `30003`, served as
`qwen36-35b-a3b-nvfp4`. Treat remaining `status = "unverified"` rows as source-backed
candidate priors, not promotion evidence. A row graduates only after a local Fakoli Dark run
captures serve health, preflight/tool results, context behavior, voice-cycle latency, and
rollback proof through Anvil Serving commands.

The required candidate set for that bakeoff is:

- `nvidia/Qwen3.6-27B-NVFP4` as the previous Fast baseline/control.
- `nvidia/Qwen3.6-35B-A3B-NVFP4` as the promoted Fast recipe.
- `nvidia/Gemma-4-31B-IT-NVFP4`.
- `zai-org/GLM-4.7-Flash`.
- `mistralai/Devstral-Small-2-24B-Instruct-2512`.

`Qwen/Qwen3-30B-A3B-Instruct-2507` is optional fallback coverage only. Do not run candidate LLM
serves on Fakoli Mini, do not use the Heavy card for these small Fast experiments, and do not
promote a recipe into production routing from registry data alone.

The loaded-endpoint benchmark mode records one candidate/config run without starting or stopping
the serve:

```bash
anvil-serving eval benchmark quality \
  --base-url http://127.0.0.1:39010/v1 \
  --model qwen36-35b-a3b-nvfp4 \
  --candidate-id qwen36-35b-a3b \
  --config-id vllm-nvfp4-32k \
  --context-targets 32768 \
  --suite chat,context,tool \
  --source-recipe configs/serve-recipes.toml#nvidia-qwen36-35b-a3b-nvfp4 \
  --serve-command "anvil-serving serves up fast-qwen36-35b-a3b --manifest examples/fakoli-dark/serves.toml" \
  --output .anvil/evidence/fast-qwen36-35b-a3b-vllm-32k.json \
  --confirm
```

The evidence JSON includes identity, source recipe, timing, context targets, tool/voice sections,
score inputs, and a `failures` list. Failed sub-checks stay in the same artifact as successful
checks so the final scoring pass can compare partial candidates without rerunning a loaded model.

For reasoning-capable cross-model evals, use the protocol-v3 controls rather
than a shared undifferentiated completion cap. Select either `--thinking-mode`
for chat-template-controlled families or `--reasoning-effort` for GPT-OSS and
Mistral-style APIs, then specify an equal `--visible-answer-tokens` allocation
and model-appropriate `--reasoning-headroom-tokens`. Their sum is sent as the
single API `max_tokens` limit. `--eval-repetitions` and
`--eval-min-pass-rate` turn one-shot checks into explicit stability evidence.
The evaluator rejects oversized plans before sending requests: 100 external
items, 20 repetitions per item, 500 aggregate attempts, 65,536 tokens per
completion, and 2,000,000 requested quality tokens are the hard ceilings.

Each quality attempt retains full visible content, `finish_reason`, bounded
reasoning-channel evidence, token usage, its budget allocation, and a failure
class. A blank answer caused by hidden reasoning reaching `finish_reason=length`
is therefore `reasoning_budget_exhausted`, not an ordinary model-quality miss.
Use validated `matches_regex` checks for harmless formatting variation (for
example `\bFINAL\s*=\s*D\b\s*$`) while keeping grading deterministic and
independent of the candidate model. The accepted regex subset is deliberately
small and excludes grouping, alternation, wildcards, and general quantifiers.

For the full Fast-tier bakeoff loop, pair that loaded-endpoint artifact with a
voice benchmark artifact and an explicit restoration check:

```bash
anvil-serving voice benchmark \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --candidate-base-url http://127.0.0.1:39010/v1 \
  --candidate-model qwen36-35b-a3b-nvfp4 \
  --candidate qwen36-35b-a3b-vllm-nvfp4-32k \
  --evidence-out docs/findings/fast-tier-bakeoff-evidence/qwen36-35b-a3b-vllm-nvfp4-32k.voice.json

anvil-serving eval benchmark quality \
  --base-url http://127.0.0.1:39010/v1 \
  --model qwen36-35b-a3b-nvfp4 \
  --candidate-id qwen36-35b-a3b \
  --config-id vllm-nvfp4-32k \
  --context-targets 32768 \
  --suite chat,context,tool,session,intelligence,voice \
  --voice-latency-ms 377.52 \
  --stt-latency-ms 68.65 \
  --tts-latency-ms 143.46 \
  --source-recipe configs/serve-recipes.toml#nvidia-qwen36-35b-a3b-nvfp4 \
  --serve-command "anvil-serving serves up fast-qwen36-35b-a3b --manifest examples/fakoli-dark/serves.toml" \
  --output docs/findings/fast-tier-bakeoff-evidence/qwen36-35b-a3b-vllm-nvfp4-32k.bakeoff.json \
  --confirm

anvil-serving serves down fast-qwen36-35b-a3b --manifest examples/fakoli-dark/serves.toml --confirm
anvil-serving serves up fast --manifest examples/fakoli-dark/serves.toml --confirm
anvil-serving serves status --manifest examples/fakoli-dark/serves.toml
```

Treat the voice benchmark artifact as stage-latency evidence unless its STT
hypothesis and WER prove semantic transcription quality for the test prompt.
Promotion remains a separate human-gated router/profile decision.

## `anvil-serving eval` — one entry point for the evals

```bash
anvil-serving eval preflight --tier fast --confirm
anvil-serving eval benchmark capacity --tier heavy --output capacity.json --confirm
anvil-serving eval benchmark quality --tier heavy --suite-file suite.json \
  --candidate-id MODEL --config-id CONFIG --output quality.json --confirm
anvil-serving eval bootstrap --eval-data ./eval-data --out ./profile.json --confirm
```

- **`preflight` / `benchmark`** resolve `--base-url` and `--model` from the serves
  manifest, so `--tier fast` is enough. If that serve is down, you get an
  actionable hint (`start it: anvil-serving serves up fast`) instead of a
  connection error. Pass extra script flags after the options, or use
  `--base-url`/`--model` to target any endpoint.
- **`benchmark quality`** runs repeated built-in or external suites and retains
  full visible output, finish/reasoning evidence, budgets, pass rates, and provenance.
  The removed `eval planning` path is replaced by `benchmark quality --suite-file`.
- **`bootstrap`** replays the committed eval fixtures into a quality-profile table
  (`anvil_serving.router.profile_bootstrap --replay`) — the eval-grounded seed for
  the router's routing policy (planning → cloud `allow`; locals `deny`).

### Typical flow

```bash
anvil-serving serves up --confirm
anvil-serving eval preflight --tier fast --confirm
anvil-serving eval benchmark capacity --tier fast --output fast-capacity.json --confirm
anvil-serving eval benchmark quality --tier fast --suite-file suite.json \
  --candidate-id MODEL --config-id CONFIG --output fast-quality.json --confirm
anvil-serving serves down --confirm
```
