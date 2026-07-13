# ADR-0017 — GPU residency reservations for purpose-driven serves

- **Status:** Accepted
- **Date:** 2026-07-13
- **Relates to:** ADR-0002, ADR-0012, ADR-0016; `docs/findings/2026-07-12-green-context-mps-capability.md`; `anvil_serving/router/admission.py` (#230)

## Context

The RTX 5090 (`dark-fast`, 32 GB) is becoming a multi-tenant GPU: always-on
voice STT/TTS sidecars landed there (2026-07-13), and small purpose-driven
models are planned next — text embeddings, a reranker, and a document-OCR VLM.
At the same time the fast LLM tier (Qwen3.6-35B-A3B-NVFP4, ~20 GB) targets the
same card. Today nothing arbitrates VRAM: each serve hand-tunes
`--gpu-memory-utilization`, the multiplexer only knows single-resident LLM
swap, and a new serve can OOM an existing one at load time.

Hardware partitioning is not available on this deployment:

- The 5090 has no MIG (consumer sm_120 silicon). The RTX PRO 6000 Blackwell
  does support MIG, but it is fully committed to the heavy tier.
- CUDA MPS static partitioning is **unknown under WSL2/Docker Desktop** per
  `docs/findings/2026-07-12-green-context-mps-capability.md` (absence of the
  control binary is not evidence of architectural unsupport). Even if later
  proven to work, MPS partitions SM allocation, not VRAM, so it would not
  solve the VRAM-ledger problem this ADR addresses.
- Per-process VRAM attribution is impossible under WSL2 passthrough —
  `nvidia-smi --query-compute-apps` reports `[N/A]` for every PID. Any
  accounting must therefore be **declarative**, not measured.

## Decision

Introduce **GPU residency reservations**: a declarative VRAM ledger enforced by the
serve lifecycle, not by the driver.

> **Terminology.** "Reservation" (not "lease") is deliberate: #230's
> `router/admission.py` already owns `AdmissionLease` — a process-local
> *request-admission* handle with bounded drain for safe tier transitions.
> VRAM reservations are the capacity layer beneath it; eviction acquires a
> transition and drains through `AdmissionLease` before `serves down`
> releases the reservation. When this ADR becomes code, the type is
> `GpuReservation`, never a second `*Lease`.

1. **Manifest fields.** Each `[[serve]]` entry may declare `gpu_role`,
   `vram_mib` (its reservation), and `residency`:
   - `resident` — always-on (voice, embeddings, OCR); never evicted.
   - `evictable` — may be stopped to make room (experiment serves).
   - `on-demand` — started for a task, evicts `evictable` serves if needed
     (LLM tiers driven by the multiplexer).
2. **Topology capacity.** `[[gpu_roles]]` entries gain `vram_mib` (capacity)
   and `reserve_mib` (display/system reserve — the 5090 is also the Windows
   display GPU; ~2 GB is never reservable).
3. **Ledger enforcement at the lifecycle verbs.** `serves up` (and
   `voice audio up`) acquires a reservation: if the sum of running reservations plus the
   request exceeds `vram_mib - reserve_mib` for that `gpu_role`, the command
   fails with the ledger printed, or evicts `evictable` reservations when the
   requester is `on-demand` and policy allows. `serves down` releases the
   reservation. `nvidia-smi` device totals are used as a sanity check only
   (declared-vs-observed drift is reported, never auto-remediated).
4. **Engine-level enforcement where possible.** For vLLM/SGLang serves,
   `serves render` derives `--gpu-memory-utilization` from
   `vram_mib / (capacity - reserve)` so the declared reservation is what the engine
   actually respects. llama.cpp/audio serves rely on model-size discipline.
5. **Multiplexer generalizes.** Single-resident swap becomes the special case
   of "evict `evictable` reservations until the requested reservation fits."
6. **Router integration.** An evicted or reservation-rejected serve is an
   unavailable tier; the router already skips those (#225) and the
   serve-lifecycle reconciler design (desired/observed serve state) publishes
   the state transitions. Reservations are the capacity layer of that same state
   machine, not a parallel one.
7. **New inference surfaces ride the same rails.** Embedding/reranker/OCR
   serves are ordinary `[[serve]]` entries (engine values extend the
   `audio` precedent, e.g. `embedding`, `ocr`) with `resident` reservations; the
   front door grows `/v1/embeddings` and routes OCR/rerank via model-field
   presets. Purpose-model serving does not need new lifecycle machinery.

## Consequences

- VRAM contention becomes an explicit, reviewable property of the manifest
  instead of an emergent OOM at load time. The 5090's fast-tier-vs-sidecars
  conflict must be resolved in the manifest (fast tier declared `on-demand`
  and sized to the remaining budget, moved to another `gpu_role`, or its
  eviction policy forbidden from touching `resident` reservations) — it can no
  longer happen by accident.
- Reservations are honest but voluntary: a serve that lies about `vram_mib` can
  still OOM the card. The declared-vs-observed drift report (device-total
  deltas around serve start/stop) is the detection mechanism WSL2 allows.
- `serves status` and the MCP `serves_status`/`reservation_status` surface show the
  per-GPU ledger, giving agents a safe way to answer "can model X fit right
  now?" without starting anything.
- Existing manifests without reservation fields keep working (no reservation → no ledger
  participation), so adoption is incremental.

## Alternatives considered

- **MIG / MPS partitioning** — MIG is unavailable on the 5090; MPS is
  unknown-under-WSL2 and VRAM-irrelevant even if available (above).
- **K8s-style fractional GPU schedulers (HAMi, KAI)** — require a cluster
  scheduler this single-host Docker Compose deployment doesn't have; the
  reservation ledger delivers the useful subset (admission control) inside the
  product's existing lifecycle verbs, per ADR-0012 ownership.
- **Measured (nvidia-smi) accounting** — impossible per-process under WSL2;
  device-total deltas are too racy to be the source of truth, so they are
  demoted to a drift check.
