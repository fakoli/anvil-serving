# ComfyUI tenant — one-time volume migration runbook (gpu-reservations:T012)

ComfyUI runs on fakoli-dark as an **on-demand image/video-generation tenant** of the
multi-tenant RTX 5090 — its own compose project
([`examples/fakoli-dark/docker-compose.comfyui.yml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/docker-compose.comfyui.yml)),
its own serves manifest
([`examples/fakoli-dark/serves.comfyui.toml`](https://github.com/fakoli/anvil-serving/blob/main/examples/fakoli-dark/serves.comfyui.toml),
the [docs/VOICE.md](VOICE.md) isolation rule), and an ADR-0017 `on-demand` reservation
(12288 MiB on `dark-fast`).

This runbook covers the **one-time migration** of the model library from the retired
Windows portable install into the named volume the tenant serves from, and the standard
bring-up/tear-down flow. It was executed on fakoli-dark on **2026-07-13**; the steps
stay valid for re-runs on a rebuilt host.

## Why a named volume (and never a `C:/` bind mount)

CLAUDE.md gotcha #15: a Windows bind mount reaches a Linux container over 9P at
~15 MB/s — checkpoint-sized reads turn every cold model load into tens of minutes. A
named docker volume lives on ext4 inside the docker-desktop WSL VM (D:-backed on this
box) and loads natively. Model paths therefore live in `comfyui-models`; the one-time
copy pays the 9P read tax once.

Two external volumes back the tenant:

| Volume | Mounted at | Contents |
| --- | --- | --- |
| `comfyui-models` | `/app/models` | the migrated model library (61 GiB) |
| `comfyui-user` | `/app/output`, `/app/user`, `/app/input` (subpath mounts) | generated outputs, saved workflows/user state, input uploads |

Both are `external: true` in the compose file so `docker compose down -v` can never
delete them.

## Source library (measured 2026-07-13)

Source: `C:/Users/sdoum/ai-code/ComfyUI_windows_portable/ComfyUI/models`

| Subdir | Size | Notes |
| --- | --- | --- |
| `diffusion_models/` | 27.3 GiB | Wan2.2 i2v 14B fp8-scaled (high/low noise) — the "27 GB" the task packet names |
| `unet/` | 22.9 GiB | Wan2.2 i2v 14B Q6_K **GGUF** — needs the ComfyUI-GGUF custom node (not in the pinned image); migrated anyway so the portable install can retire without data loss |
| `text_encoders/` | 6.3 GiB | umt5 et al. for the Wan workflows |
| `loras/` | 3.6 GiB | |
| `clip_vision/` | 1.2 GiB | |
| `vae/` + `vae_approx/` + `configs/` | ~0.3 GiB | |
| **Total** | **61 GiB** | the packet's "27 GB" counted only `diffusion_models/` |

`checkpoints/` is empty in this library — the models are diffusion-model/unet loader
files, so "a checkpoint is visible" is verified against
`/models/diffusion_models` (the UI's loader list), not `/models/checkpoints`.

## One-time migration (executed 2026-07-13)

Run from **PowerShell** (Git Bash mangles container paths — gotcha #11 — unless you
prefix `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'`).

```powershell
# 1. Create the external volumes (compose will NOT create them: external: true).
docker volume create comfyui-models
docker volume create comfyui-user

# 2. Prepare the user-volume subpath layout. Compose mounts comfyui-user with
#    volume.subpath (output/, user/, input/) and docker does not create missing
#    subpaths — a fresh volume without them fails the mount at `serves up`.
#    The image runs as user 1000:1000, so hand it ownership.
docker run --rm -v comfyui-user:/data alpine sh -c `
  "mkdir -p /data/output /data/user /data/input && chown -R 1000:1000 /data"

# 3. The long copy: portable library -> named volume (one-off 9P read; ~15 min at
#    the ~75 MB/s observed on this box, budget up to ~70 min if 9P is slow).
#    The bind mount is read-only; the portable install is never modified.
docker run --rm `
  -v "C:\Users\sdoum\ai-code\ComfyUI_windows_portable\ComfyUI\models:/src:ro" `
  -v comfyui-models:/dst `
  alpine sh -c "cp -a /src/. /dst/ && chown -R 1000:1000 /dst && du -sm /dst"

# 4. Verify the copy: sizes match the source table above, ownership is 1000:1000.
docker run --rm -v comfyui-models:/dst alpine sh -c "du -sm /dst/* | sort -rn | head; ls -ln /dst | head"
```

This is a **copy**, not a move — see "Portable install retirement" below.

## Bring-up / tear-down (the normal lifecycle)

Always through the product surface, never raw `docker` (ADR-0002 / CLAUDE.md):

```bash
# Admission first: the manifest mirrors the serves.toml dark-fast reservations, so
# `up` is denied with the honest ledger when the card is committed. Free residents
# via the MAIN manifest (or evict the vision slot, below) until 12288 MiB is free.
anvil-serving serves --manifest examples/fakoli-dark/serves.comfyui.toml up comfyui

# UI: http://127.0.0.1:8188  (loopback-only; set COMFYUI_PUBLISH for tailnet opt-in)
# API readiness: curl http://127.0.0.1:8188/system_stats
# Library check:  curl http://127.0.0.1:8188/models/diffusion_models

# Done with the task? Stopping the container IS the reservation release (ADR-0017).
anvil-serving serves --manifest examples/fakoli-dark/serves.comfyui.toml down comfyui
anvil-serving serves --manifest examples/fakoli-dark/serves.comfyui.toml status  # free again
```

When the `vision` evictable slot holds the VRAM, the on-demand tenant may take it
through the drained ADR-0018 transition (T005):

```bash
anvil-serving serves --manifest examples/fakoli-dark/serves.comfyui.toml \
  up comfyui --evict --router-url http://100.87.34.66:8000
# ... after the ComfyUI task:
anvil-serving serves --manifest examples/fakoli-dark/serves.comfyui.toml down comfyui
anvil-serving serves --manifest examples/fakoli-dark/serves.toml up vision
# then readmit tier vision-local per docs/CLI.md (`router readmit`).
```

Capacity reality on the live box (2026-07-13): with the full resident set up (fast
14336 + embeddings 3200 + reranker 3456 + ocr 5120 = 26112 of 27999 MiB) the ledger
correctly **denies** `up comfyui` (free 1887 < 12288) — that denial is the feature.
Bring residents down via the main manifest (e.g. `serves down ocr embeddings
reranker` frees 11776 → 13663 free) and restore them afterwards.

## GGUF caveat

The `unet/` GGUF files (Wan2.2 Q6_K) load only through the ComfyUI-GGUF custom node,
which the pinned image does not ship. They are migrated for completeness; the
fp8-scaled safetensors in `diffusion_models/` are the ones the stock v0.27.1 loaders
serve. If GGUF workflows are needed later, bake the custom node into a derived image
(pin it the same way) rather than installing it mutably into a running container.

## Portable install retirement

`C:/Users/sdoum/ai-code/ComfyUI_windows_portable/` is **retired as a serving path**
as of this migration: nothing should launch it, and its model library is now
authoritative in the `comfyui-models` volume. The directory is deliberately **not
deleted** — it stays as the migration source / rollback copy until the operator
decides to reclaim the 61 GiB (a later, explicitly human decision; do not automate
it). Rollback is trivial while it exists: the volume can be re-created from it by
re-running the migration steps.
