# `fakoli-dark` — a real two-tier reference topology

This directory is a **worked, real-world instance** of the two local tiers `anvil-serving`
routes across, plus the bake-off findings that justified keeping a local box at all. It is
**not** a template you deploy as-is: every path, GPU UUID, and port here is specific to the
machine it was built on. Read this file before you copy anything.

## Topology — two boxes, not one

`fakoli-dark` is actually **two separate physical machines** on the same private network:

- **The GPU box (`fakoli-dark` itself)** — runs the local model serves (this directory's
  compose files): **heavy** `:30000` (Qwen3.5-35B-A3B AWQ on SGLang, RTX PRO 6000 96GB) and
  **fast** `:30001` (gpt-oss-20b on vLLM, RTX 5090 32GB). It also runs `anvil-serving serve`
  (the router front door), which talks to both serves over `127.0.0.1` because they're
  colocated with the router on this same box.
- **The gateway box (`Fakoli Mini`)** — a separate, smaller machine that runs **OpenClaw**
  (the harness). It is where coding-agent traffic originates.

```
Fakoli Mini (gateway)                 fakoli-dark (GPU box)
┌────────────────────┐   private net  ┌──────────────────────────────┐
│ OpenClaw            │ ─────────────▶ │ anvil-serving serve  :8000   │
│ (harness)            │  (NOT loopback)│  ├── heavy-local  → :30000  │
└────────────────────┘                 │  └── fast-local   → :30001  │
                                        └──────────────────────────────┘
```

## Cross-box exposure — this is the one thing you can't copy verbatim

Everywhere else in this repo, `127.0.0.1` is the right (and only supported) default — see
`CLAUDE.md` gotcha #1 and [`SECURITY.md`](../../SECURITY.md). That still holds **within** each
box: the model serves in this directory bind `127.0.0.1` only, and only the router talks to
them directly.

But the **router front door itself** must be reachable from the *other* box (the gateway), and
`127.0.0.1` never crosses a machine boundary — a loopback address is local to the box it's
bound on. Two ways to make that cross-box hop, in order of preference:

1. **Bind the front door to a private/tailnet address, not `0.0.0.0`.** If both boxes are on a
   private mesh network (Tailscale, WireGuard, a LAN you control), start the router with
   `anvil-serving serve --host <fakoli-dark's-tailnet-IP>` and point the gateway's
   `ANTHROPIC_BASE_URL` / `OPENAI_API_BASE` at that address instead of `127.0.0.1`. This never
   touches the public internet.
2. **If you must bind more broadly (`--host 0.0.0.0`), you own auth and network controls** —
   put a reverse proxy with authentication in front, or firewall the port to the gateway's IP
   only. See [`SECURITY.md`](../../SECURITY.md): an unauthenticated router on a reachable
   address lets any caller drive routing and, if you've opted into a metered cloud tier,
   consume your cloud credentials.

Either way, do **not** use `127.0.0.1` in the gateway's config for the router URL — it will
resolve to the gateway box itself, not to `fakoli-dark`, and every request will fail to connect.

## Every value you must REPLACE before reusing this topology

Nothing below is a placeholder token — these are the literal values from the machine this was
built on. Grep for `# REPLACE:` in this directory's files for the inline callouts; the table
below is the consolidated list.

| Value | Example (this box) | Find yours with | Where it appears |
|---|---|---|---|
| GPU UUID (heavy card) | `GPU-d0f446cf-1771-414c-e116-a39138798a8c` | `nvidia-smi -L` | `docker-compose.yml` (`sglang.environment.CUDA_VISIBLE_DEVICES`) |
| GPU UUID (fast card) | `GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1` | `nvidia-smi -L` | `docker-compose.yml` (`fast.environment.CUDA_VISIBLE_DEVICES`, `fast.deploy.resources.reservations.devices.device_ids`), `serve-fast-gptoss-vllm.sh`, `serve-fast-glm-vllm.sh` (`GPU0_UUID`) |
| GPU index (heavy card, reference-only) | `1` | `nvidia-smi -L` (0-based index) | `docker-compose.heavy.yml` (`deploy.resources.reservations.devices.device_ids`) |
| Heavy model directory | `C:/Users/sdoum/models/qwen35-awq` | wherever you store local model weights | `docker-compose.yml`, `docker-compose.heavy.yml` (`volumes`) |
| Fast model directory | `C:/Users/sdoum/models/gpt-oss-20b` | wherever you store local model weights | `docker-compose.yml`, `serve-fast-gptoss-vllm.sh` (`volumes`/`-v`) |
| GLM model directory (optional alt fast serve) | `C:/Users/sdoum/models/glm47-flash-awq` | wherever you store local model weights | `serve-fast-glm-vllm.sh` (`-v`) |
| Heavy served-model-name | `qwen35-awq-local` | your choice — must match across every file that names it | `docker-compose.yml`, `docker-compose.heavy.yml` (`--served-model-name`), `serves.toml` (`[[serve]] name = "heavy"` `.model`), `../../configs/example.toml` (`[[router.tiers]] id = "heavy-local"` `.model`), `../../README.md` |
| Fast served-model-name | `gpt-oss-20b` | your choice | `docker-compose.yml`, `serve-fast-gptoss-vllm.sh` (`--served-model-name`), `serves.toml` (`[[serve]] name = "fast"` `.model`), `../../configs/example.toml` (`[[router.tiers]] id = "fast-local"` `.model`) |
| Ports | `30000` (heavy), `30001` (fast), `8000` (router front door) | your choice — must not collide with other local services | every compose/script file, `../../configs/example.toml` (`base_url`) |
| Cross-box tailnet/private IP | *(none committed — machine-specific)* | your mesh network's assigned address for the GPU box (e.g. `tailscale ip -4`) | the gateway's `ANTHROPIC_BASE_URL` / `OPENAI_API_BASE`, and the router's `--host` flag — see "Cross-box exposure" above |

`docker-compose.heavy.yml` is a **superseded reference file** (kept only as an alternate
single-tier example) — `docker-compose.yml` is the current source of truth for both tiers; see
the header comment in each file.

## Files

- [`docker-compose.yml`](docker-compose.yml) — the Docker-Compose source of truth for **both**
  local tiers (heavy + fast). `anvil-serving serves up <heavy|fast>` delegates here via
  [`serves.toml`](serves.toml) (see `docs/adr/0002-serves-are-compose-defined.md`).
- [`docker-compose.heavy.yml`](docker-compose.heavy.yml) — superseded single-tier reference; not
  used by `serves up`.
- [`docker-compose.experiment.yml`](docker-compose.experiment.yml) — parametrized harness for
  bake-off experiments (`docs/SERVES-AND-EVAL.md`).
- [`serves.toml`](serves.toml) — the declarative serves manifest `anvil-serving serves` reads.
- [`serve-fast-gptoss-vllm.sh`](serve-fast-gptoss-vllm.sh),
  [`serve-fast-glm-vllm.sh`](serve-fast-glm-vllm.sh) — reference `docker run` one-liners
  (superseded by the compose `fast` service; kept for readability / alternate models).
- [`sglang-up.ps1`](sglang-up.ps1), [`sglang-down.ps1`](sglang-down.ps1) — PowerShell wrappers
  around `docker compose` for the heavy tier.
- [`SETUP-STORY.md`](SETUP-STORY.md) — the original overnight setup narrative (WSL2 OOM fix,
  first successful serve).
- [`DECISIONS.md`](DECISIONS.md) — running decision log for the local-serving build.
- [`BAKE-OFF-RUNBOOK.md`](BAKE-OFF-RUNBOOK.md) — the local-vs-cloud quality/cost bake-off that
  justified keeping this box in the loop.
- [`model-index.example.md`](model-index.example.md) — a sample `anvil-serving models sync`
  INDEX.md output.
- [`wslconfig.snapshot`](wslconfig.snapshot) — the `.wslconfig` that fixed the WSL2 memory-cap
  OOM (CLAUDE.md gotcha #3).
