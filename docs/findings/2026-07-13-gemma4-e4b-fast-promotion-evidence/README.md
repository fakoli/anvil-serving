# gpu-reservations:T006 — Gemma 4 E4B fast-tier promotion evidence (2026-07-13)

Live evidence from fakoli-dark (RTX 5090, `GPU-04d3b6e7-5691-3e86-1d34-c37999440cf1`)
for the promotion of `gemma4-e4b-it` from the `voice-gemma4-e4b` experiment serve to the
resident `fast` serve with a declared ADR-0017 reservation.

## Files

- `preflight.json` — `anvil-serving eval preflight --tier fast` machine evidence
  (checks `smoke,json,needle,tools`, `--thinking-mode disabled`, needle at ~32768 ctx,
  20/20 shared-prefix tool batch): **ALL PASS** against the live serve on
  `http://127.0.0.1:30003/v1`.
- `ledger-post-up.txt` — `serves status` reservation ledger + `nvidia-smi` device totals
  with the serve running.

## Measurements that sized the reservation

| quantity | value | source |
|---|---|---|
| bf16 weight footprint | **15.18 GiB** | vLLM `gpu_model_runner`: "Model loading took 15.18 GiB" |
| declared reservation (`vram_mib`) | **18432 MiB** | weights + KV/activations for the 32K window |
| engine fraction | **0.5653** = 18432 / 32607 capacity | see deviation note below |
| KV cache at 0.5653 | 1.11 GiB → 60,994 tokens | vLLM `gpu_worker`/`kv_cache_utils` |
| device draw (running, incl. CUDA context) | 25498 − 6319 ≈ **19.2 GiB** | `nvidia-smi` before/after |
| voice sidecars + display (ledger `reserve_mib` 6656) | 6319–6325 MiB observed | `nvidia-smi` with fast down |

The task estimate of ~6000 MiB did not survive measurement: this checkpoint has no
per-layer-embedding offload path in vLLM (grep of `gemma4*.py` in the image — no
PLE/offload knob), so 15.18 GiB of weights is the floor.

## T003 derive-formula deviation (flagged)

`reservations.derive_gpu_memory_utilization` computes `vram_mib / (capacity − reserve)`
(= 0.7103 here), but vLLM applies `--gpu-memory-utilization` to **total device memory**, so
with `reserve_mib > 0` the engine over-allocates by `capacity / budget`. Measured on this
box: 0.7103 → engine held ~23.9 GiB, **5.5 GiB past the declared 18432 MiB reservation**
(device 30229/32607 used; the ledger's "free 7519 MiB" was off by ~5.1 GiB). The compose
therefore pins `0.5653 = vram_mib / capacity`, after which observed draw matches the
declaration (25498/32607 used; ledger free 7519 vs device free 7109 — the gap is CUDA
context overhead). Recommended fix for a follow-up task: derive the engine fraction as
`vram_mib / capacity_mib`; keep the budget denominator only for the admission check.

## Non-disruption checks (always-on tenants)

- `anvil-voice-tts` `/health` → 200; `anvil-voice-stt` answering HTTP on :30010;
  both containers `Up 4 hours` (never restarted) throughout the swap.
- `anvil-router` `/healthz` → 200 at its published address.

## Live-reconciliation follow-up (explicitly out of this task's scope)

The deployed router's config volume (`anvil-router-cfg`) still names
`qwen36-35b-a3b-nvfp4` for `fast-local`; until the updated
`examples/fakoli-dark/anvil-router.live.toml` is promoted through the human-gated
`router promote` path, fast-preset requests fail structural verify at the serve (model-name
404) and escalate to `heavy-local` — degraded but safe. Promote the captured config to
restore the fast tier.
