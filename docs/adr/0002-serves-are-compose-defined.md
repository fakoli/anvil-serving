# ADR-0002 — Model serves are Docker-Compose-defined

- **Status:** **Accepted** (2026-06-30)
- **Date:** 2026-06-30
- **Relates to:** `anvil_serving/serves.py` (`serves up|down|status`), `anvil_serving/deploy.py`
  (renders a compose file), `examples/fakoli-dark/serves.toml`, `docs/SERVES-AND-EVAL.md`,
  CLAUDE.md gotchas #11 (MSYS path-mangling) / #13 (GPU pinning) / #14 (UVA) / #15 (weights cache).

## Context

The serving substrate stood up model containers two different ways. The heavy tier was already
Docker-Compose-defined (`examples/fakoli-dark/docker-compose.yml`); the fast tier was a hand-rolled
`docker run` one-liner in a bash script (`serve-fast-gptoss-vllm.sh`). Ad-hoc `docker run` proved
fragile in exactly the ways this repo keeps rediscovering:

- **MSYS path-mangling.** Under Git Bash, `docker run … serve /models/x` rewrites the leading-slash
  path to `C:/Program Files/Git/models/x` and vLLM errors `Repo id must be in the form …`. The
  scripts have to defend with `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'` (gotcha #11), and a
  `first-time serves up fast` needs `bash` on PATH just to run the script.
- **Quoting / flag drift.** GPU-UUID env vars, `--ipc=host`, long vLLM flag lists, and volume specs
  are easy to mistype in a shell line and hard to diff.
- **Port conflicts** between overlapping hand-started containers.
- **The stale-container drift bug (the decisive one).** `serves up` restarts a *stopped-but-existing*
  container with `docker start`, which replays its **original** create-time config. Edit the model,
  flags, or mount and `docker start` silently serves the **stale** container — the config on disk and
  the config actually running diverge with no signal.

Meanwhile `docker compose up -d` already solves the drift case: it diffs the desired spec against the
running container and **recreates natively when the config has changed**. Having one tier declarative
and the other imperative meant the drift-safe path only covered half the fleet.

## Considered options

1. **Keep the split** (compose for heavy, `docker run` script for fast). Rejected: leaves the fast
   tier exposed to every failure above, and keeps two mental models for one operation.
2. **Harden the bash scripts** (force-recreate, more MSYS guards, config-hash checks). Rejected: it
   reimplements, worse, what `docker compose up -d` already does natively, and still carries the
   MSYS/quoting surface.
3. **All serves Docker-Compose-defined; `serves up` delegates to `docker compose up -d`** (a service
   per tier in one compose file). Chosen.

## Decision

**Model serves are Docker-Compose-defined. `anvil-serving serves up` delegates to
`docker compose up -d <service>` for compose serves — which is drift-safe (native recreate on config
change).** Concretely:

- `examples/fakoli-dark/docker-compose.yml` holds **one service per tier** (`sglang` = heavy,
  `fast` = gpt-oss-20b on vLLM). Each `serves.toml` `up` targets its service by name
  (`docker compose … up -d sglang` / `… up -d fast`) so the tiers stay independent.
- The hard-won config is captured **declaratively** in the compose file: GPU pinned by UUID via
  `deploy.…devices.device_ids` + `CUDA_DEVICE_ORDER=PCI_BUS_ID`, `VLLM_USE_V2_MODEL_RUNNER=0`
  (WSL2/UVA), `ipc: host`, the model mount, and the full vLLM command — no shell quoting, no MSYS
  path rewriting.
- A **parametrized experiment harness** (`docker-compose.experiment.yml`) covers one-off model
  trials: a single vLLM service driven by `MODEL` / `SERVED_NAME` / `PORT` / `GPU_UUID` / `EXTRA_ARGS`
  env vars, with the sm_120/WSL2 defaults (stable image, `VLLM_USE_V2_MODEL_RUNNER=0`, the
  D:-backed `vllm-hfcache` volume for ~15s native loads, `CUDA_DEVICE_ORDER=PCI_BUS_ID`) baked in.
  A future experiment is `MODEL=… GPU_UUID=… PORT=… docker compose -f … up -d` — never a hand-built
  `docker run`.

This **supersedes the ad-hoc `docker run` serve scripts** (`serve-fast-gptoss-vllm.sh`,
`serve-fast-glm-vllm.sh`) as the way serves are launched; they remain in-tree only as reference.

## Consequences

- **Docker Compose v2 is now a serving-substrate operational prerequisite — NOT a Python runtime
  dependency.** It is required only to *operate* the GPU serves (`serves` / `deploy`), which already
  require Docker + NVIDIA GPUs. **The router (`anvil-serving router run`) and the rest of the Python
  package remain stdlib-only** — no new import, no PyPI dependency, nothing added to the hot path.
  This is an ops tool the operator installs, on the same footing as Docker itself, and it is
  documented as such in `README.md` and `docs/SERVES-AND-EVAL.md`.
- **The stale-container drift bug is closed** for compose serves: `serves up` issues
  `docker compose up -d <service>` **unconditionally — even when the container is already
  running** — so editing the compose file and re-running `serves up` recreates the container to
  match (and is a cheap no-op when unchanged), instead of short-circuiting on "already running" or
  `docker start`-ing a stale one.
- **One mental model.** Every serve is `docker compose up -d <service>` / `down`; no per-tier bash
  entrypoint, no `bash`-on-PATH requirement for a first-time `serves up fast`, no MSYS guards.
- **Trade-off:** the compose files carry machine-specific facts (the fakoli-dark GPU UUIDs, Windows
  model paths, the `vllm-hfcache` external volume). They are an *example topology*, not a portable
  default — an operator on other hardware edits them (or renders a fresh one with `deploy`). This is
  the same locality the bash scripts already had, now declarative and diff-able.
- **Follow-up (out of scope here):** `serves.py` makes `serves up` delegate to `docker compose up -d`
  for compose serves (in a sibling change); this ADR records the decision that all serves are
  compose-defined, which that delegation depends on.
