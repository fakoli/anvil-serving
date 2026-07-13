# gpu-reservations:T015 — Live 5090 resident-set validation

**Date:** 2026-07-13 (UTC) · **Box:** fakoli-dark (RTX 5090 "dark-fast", 32607 MiB) + RTX PRO 6000 (heavy)
**Branch:** `agent/gpu-reservations-t015-live-5090-resident-set-validation`
**Evidence:** `docs/findings/2026-07-13-t015-resident-set-evidence/`

Lifecycle was driven only through `anvil-serving serves` / `voice audio` verbs — never raw
`docker` for start/stop. The resident set was already live (3–13 h uptime) from the
merged T006–T014 work; this task validated concurrent operation, reconciled the budget
against fresh measurement, and exercised the eviction drain path.

## 1. Full resident set healthy concurrently

`serves status --manifest examples/fakoli-dark/serves.toml` (`serves-status-ledger.txt`)
and the voice manifest (`serves-status-voice.txt`) — all HEALTH 200:

| serve | port | residency | vram_mib | docker | health |
|-------|------|-----------|----------|--------|--------|
| fast (Gemma 4 E4B FP8-Dynamic) | 30003 | resident | 14336 | running | 200 |
| embeddings (Qwen3-Embedding-0.6B) | 30005 | resident | 3200 | running | 200 |
| reranker (Qwen3-Reranker-0.6B) | 30006 | resident | 3456 | running | 200 |
| ocr (PaddleOCR-VL-1.6) | 30007 | resident | 5120 | running | 200 |
| stt (parakeet tdt-0.6b-v3) | 30010 | voice sidecar | (reserve) | running | 200 |
| tts (kokoro-fastapi) | 30011 | voice sidecar | (reserve) | running | 200 |
| vision (Qwen3-VL-4B) | 30008 | evictable | 12288 | exited | — (by design) |

## 2. Ledger invariant + nvidia-smi consistency

```
gpu_role 'dark-fast': capacity 32607 MiB, reserve 4608 MiB, committed 26112 MiB, free 1887 MiB
```

- **committed 26112 ≤ capacity − reserve (27999)** ✓ — invariant holds with 1887 MiB ledger headroom.
- **nvidia-smi `memory.total` = 32607 MiB = ledger capacity, EXACTLY** ✓ (`nvidia-smi-device.txt`).

## 3. Voice WS handshake + embeddings through the router (all residents up)

- **Voice WS** (`voice-ws-handshake.json`): stdlib WebSocket upgrade to
  `ws://127.0.0.1:8765` **on fakoli-mini** over passwordless ssh →
  `HTTP/1.1 101 Switching Protocols` on `/v1/realtime` with a valid
  `Sec-WebSocket-Accept`. `handshake_ok: true`.
- **Embeddings through the router** (`embeddings-through-router.txt`):
  `POST /v1/embeddings` at the router front door (authed via `ANVIL_ROUTER_TOKEN`),
  routed by model name to the `embeddings-local` purpose-model (:30005) →
  **HTTP 200**, 1024-dim vector, `model: qwen3-embedding-0.6b`, 10 prompt tokens.

## 4. Eviction cycle (ledger drain path)

**Negative — residents are protected** (`eviction-denied-full-resident-set.txt`, live,
zero side effects): `serves up comfyui --evict --dry-run` against the FULL resident set is
correctly **refused** — "needs 10401 MiB but every evictable reservation combined frees
only 0 MiB" (vision is the only evictable and it is not committed; residents are never
evicted). *No container command was run; the ledger stands.*

**Positive — one full eviction cycle** (`eviction-cycle.json`, `run_eviction_cycle.py`,
`eviction-sim-*`): the real `serves.cmd_up --evict` drain path executed against an
**isolated `sim-5090` gpu_role** with trivial alpine containers (zero impact on the live
residents / production router; the ADR-0018 router quiesce/drain/readmit RPC legs were
already validated LIVE against the real router in T013 and are recorded here through the
injectable transition seam):

1. `vision-sim` (evictable) committed → committed 12288, free 7712.
2. `comfyui-sim` (on-demand) admission **without** `--evict` → **denied** (rc=1, over budget).
3. `comfyui-sim` **with** `--evict` → quiesce + drain `vision-sim-tier` → stop `vision-sim`
   → start `comfyui-sim`. Ledger: vision-sim released, comfyui-sim committed.
4. Restore: down `comfyui-sim`, up `vision-sim`, guarded readmit → back to committed vision-sim.

Transition sequence recorded: `quiesce → drain(60s) → readmit` on `vision-sim-tier`.

## 5. Budget reconciliation with measured reality

The prompt's "rebalanced budget (fast 10240, reserve 4608)" refers to the operator's
initial fast target, which T011 already found **physically unreachable** (E4B's bf16
per-layer embeddings survive fp8; ~12.4 GiB weights) and rebalanced to **fast 14336 /
reserve 4608** — the live manifest values, re-confirmed here.

| quantity | value (MiB) | source |
|----------|-------------|--------|
| device memory.total | 32607 | nvidia-smi — matches ledger capacity exactly |
| GPU0 memory.used (full set) | 31843 | nvidia-smi (stable ×3) |
| ledger committed | 26112 | serves status |
| non-ledger draw (used − committed) | **5731** (~5.6 GiB) | display + voice sidecars; consistent with the prior ~5.7 GiB note |
| nvidia-smi allocatable free | **345** | nvidia-smi |
| ledger free | 1887 | serves status |

**Finding:** the declared per-serve budgets are accurate (vLLM fills each serve to its
`--gpu-memory-utilization` cap, and all five residents stay healthy inside the 32607 MiB
device) — **no budget correction needed**. The one drift is prose: the ledger's 1887 MiB
free runs **~1.5 GiB optimistic** against the 345 MiB physically allocatable, and real
headroom is only **~0.3 GiB** (tighter than the earlier ~0.8 GiB estimate) and shrinks
under fast-tier KV pressure. The `serves.toml` `[[gpu_roles]]` HONESTY NOTE was corrected
to these T015-measured figures.

## Reproduce

```
anvil-serving serves status --manifest examples/fakoli-dark/serves.voice.toml
anvil-serving serves status --manifest examples/fakoli-dark/serves.toml
anvil-serving serves up comfyui --manifest examples/fakoli-dark/serves.comfyui.toml --evict --dry-run --confirm
python docs/findings/2026-07-13-t015-resident-set-evidence/run_eviction_cycle.py
python -m pytest tests/test_reservations.py -q
```
