# Lifecycle-aware WSL cache reclaim — Fakoli Dark live qualification

**Date:** 2026-07-18

**Host:** Fakoli Dark, Windows + Docker Desktop WSL2

**Hardware:** NVIDIA RTX PRO 6000 Blackwell Max-Q Workstation Edition (95,587 MiB)

**Feature base:** `origin/main` `8ad73ff4a8b327e50f5ec04d125214235aa64bed`

**Decision:** [ADR-0023](../adr/0023-lifecycle-aware-wsl-cache-reclaim.md)

## Result

The default-off `host.toml` policy was enabled on the operator machine and a confirmed,
manifest-owned `serves up heavy` loaded the already-cached pinned GPT-OSS Puzzle 88B deployment.
The lifecycle hook attributed 49.9 GiB of page-cache growth to the operation, observed settled
growth of 0.00 GiB/s, ran the page-cache-only primitive, and reported:

```text
cache reclaim after serves up: reclaimed
(cache 1.1 GB -> 1.1 GB, operation growth 49.9 GB,
 latest growth 0.00 GB/s; distro docker-desktop)
```

After reclaim, the direct model endpoint remained healthy, advertised the exact expected model,
kept its GPU allocation resident, and completed a small inference. The serve and router were then
restored to their exact initial stopped state. No checkpoint was downloaded, no promotion or
rollback ran, no route changed, and no Puzzle/Gemma configuration was modified.

## Policy and target

The operator file did not exist before the run, so there was no prior file to back up. It was
created at `C:\Users\sdoum\.anvil-serving\host.toml` and intentionally left enabled:

```toml
schema_version = 1

[cache_reclaim]
enabled = true
distro = "docker-desktop"
threshold_gb = 16
```

The managed target was the Puzzle Heavy service already promoted on `main`:

| Property | Value |
|---|---|
| Compose service / container | `heavy` / `vllm-gptoss-puzzle-heavy` |
| Endpoint | `http://127.0.0.1:30002` |
| Model / served name | `nvidia/gpt-oss-puzzle-88B` / `gpt-oss-puzzle-88b` |
| Checkpoint revision | `9c0e0746a0d2218b28cc7b2cb3ce4e1a2f50fdb2` |
| Image | `anvil-vllm:gpt-oss-puzzle-485463b3498ed3ffcf0c8fcb52c1670a21be5d82` |
| Recipe | [Pinned Puzzle 88B recipe](../benchmarks/gpt-oss-puzzle-88b-recipe.md) |

The dry run resolved the enabled policy, `docker-desktop`, 16 GiB threshold, and the exact managed
Compose action before apply.

## Measurements

Linux values are direct `/proc/meminfo` samples from `docker-desktop`. GiB conversions use
1,048,576 KiB per GiB. `vmmemWSL` is the Windows process working set and is reported separately;
it is not treated as a page-cache measurement.

| Point | Linux `Cached` | `SReclaimable` | `vmmemWSL` working set | PRO 6000 VRAM | Router / Heavy |
|---|---:|---:|---:|---:|---|
| Before load | 1,142,288 KiB (1.09 GiB) | 25,892 KiB | 3.40 GiB | 510 MiB | exited 137 / exited 137 |
| After automatic reclaim, model resident | 1,275,948 KiB (1.22 GiB) | 525,268 KiB | 8.39 GiB | 93,839 MiB | exited 137 / running |
| After state restoration | 1,148,540 KiB (1.10 GiB) | 498,760 KiB | 4.94 GiB | 510 MiB | exited 137 / exited 137 |

The hook's internal samples saw 49.9 GiB of operation growth before the drop and approximately
1.1 GiB after it. The later 1.22 GiB sample includes endpoint verification and inference activity.
The result materially reduced page cache while leaving the loaded model's GPU VRAM allocation
intact.

## Readiness and inference evidence

- `GET /health` returned HTTP 200.
- `GET /v1/models` returned HTTP 200 with exactly one id: `gpt-oss-puzzle-88b`.
- A low-reasoning chat-completions request returned HTTP 200, model
  `gpt-oss-puzzle-88b`, `finish_reason: stop`, and visible content exactly
  `cache reclaim live check passed` (76 prompt tokens, 32 completion tokens).
- A deliberately smaller 64-token request first ended with `finish_reason: length` and no visible
  content because the reasoning model exhausted that budget. It was not counted as the functional
  success; the model-aware low-reasoning request above is the retained smoke result.

This validates service continuity and residency across the cache drop. It is not a new quality
benchmark and does not change the existing Puzzle promotion evidence.

## Caveats

- The pre-existing stopped `anvil-router` container belonged to the Puzzle worktree's Compose
  project, while the installed operator-home router action tried to create the same fixed container
  name from a different Compose project. Docker rejected that create with a name conflict. Existing
  `serves up` behavior continued the Heavy start; the router remained stopped during model
  qualification, and all probes used the direct loopback model endpoint. Later, its pre-existing
  `restart: unless-stopped` policy auto-started the previously exited container. The final state
  audit caught that change and explicitly stopped `router` through the Puzzle worktree's exact
  Compose file, restoring both containers to `exited 137`. This is an environment ownership and
  restoration caveat, not a cache-reclaim failure.
- Reclaim is VM-wide. A different WSL workload may need to reread evicted clean pages, which is why
  the product default remains disabled and the operator policy is explicit.
- The measurement establishes the pinned Puzzle managed-load path on this workstation. It does not
  claim request-triggered, voice, ComfyUI, ad-hoc Compose, or non-Windows applicability.
