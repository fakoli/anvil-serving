# Fakoli-Dark v2 — pre-purchase planning context (stored 2026-06-27)

> Stored verbatim from a multi-session planning handoff the author wrote **before purchasing
> the system**. It captures the *reasoning* behind the build, the local-serving plan, and the
> Anvil cloud+local routing strategy. Keep it as the durable "why" record.

## Plan vs. actual (read this first)
This is the PLAN. The actual invoiced build diverged on two points — note the arc:
- **Topology:** planned a **two-machine split** (PRO 6000 in the new box; 5090 relocated to the
  old 7800X3D box). The actual invoice is a **consolidated single box** (both cards in the
  Fractal North XL at x8/x8). The "do not *pool* the cards" decision still holds (independent
  replicas, one model per card), but they ended up physically *housed together*, not split.
- **PSU:** planned to keep the **HX1200i (1200W)** on a PRO-6000-only box. The actual invoice
  shows a **Seasonic Prime TX-1600 (1600W)** — consistent with feeding both cards in one chassis.
- (Confirm the current real config + what tipped the consolidation; that is the missing arc beat.)

## Confidentiality note
Contains an employer reference (§1). This repo is private, so it is fine here, but **never**
copy the employer name into any public/blog content. The blog generalizes the employer.

---

<<<VERBATIM PLANNING HANDOFF>>>

# Fakoli-Dark v2 + Anvil — Session Context Handoff

**Purpose:** self-contained context primer summarizing a multi-session planning effort
(hardware build, architectural decisions + reasoning, local-model serving plan, Anvil
cloud+local routing).

## 1. Who / What / Goal
- User: Sekou Doumbouya — 22-year infrastructure/cloud engineer. Deep expertise in PCIe,
  quantization, KV cache, MoE, SGLang/vLLM, cost modeling. Runs everything technical himself;
  values "keep it real," numbers-driven analysis, and provider independence. Long-term goal:
  cost control and not being beholden to a single provider (or a paycheck).
- Core project — Anvil: local-first, runtime-neutral "system of record for agent teams" —
  durable, evidence-gated, lease-coordinated multi-agent state in SQLite + append-only JSONL
  event log (full-replay audit), CLI + FastMCP stdio MCP server (24 tools). Multi-provider LLM
  (Anthropic / Bedrock / Custom OpenAI-compatible). Tier-aware routing (opus/sonnet/haiku) over
  5 agents: planner (opus), critic (opus), docs-scribe (sonnet), sentinel (haiku),
  state-keeper (haiku) — ~60% cost drop vs all-Opus. Related: fakoli-flow (wave orchestration),
  fakoli-crew (9 specialist agents).
- Point of the hardware: run Anvil's agentic loops (3-5 concurrent agents) partly local to
  arbitrage cost vs cloud, with large enough per-agent context to be useful.

## 2. Hardware — final build (planned as TWO machines)
- Machine 1 "Fakoli-Dark v2": RTX PRO 6000 Blackwell Max-Q 96GB GDDR7 (300W, ~$11.5K,
  ~1.79 TB/s, ECC on); Ryzen 7 9800X3D; ASUS ProArt X870E-Creator WiFi (SWAPPED from ROG
  Crosshair X870E Hero — see 3.2); Corsair 96GB (2x48GB) DDR5-6000 CL30 (never 4x48 on AM5);
  Samsung 990 Pro 4TB (M.2_1) + 2nd 4TB NVMe (M.2_3/4, never M.2_2); Corsair HX1200i (1200W —
  corrected from 1500W); ARCTIC Liquid Freezer III Pro 360 (reused; caught a double-charge);
  Fractal North XL; OS recommend native/dual-boot Ubuntu (CUDA >=12.9, open kernel module,
  driver 580+).
- Machine 2 (existing old box): Ryzen 7 7800X3D + RTX 4080 16GB + ~64GB RAM. The MSI SUPRIM
  LIQUID RTX 5090 32GB (~575W) moves here. Needs bigger case + ATX 3.1 PSU w/ native 12V-2x6
  (>=1000W, 1200W comfortable) for the 5090's transient spikes. Role: second independent SGLang
  replica (behind LiteLLM) + experimentation sandbox so risky configs never touch the PRO 6000.

## 3. Key decisions & reasoning
- 3.1 PSU — keep HX1200i on the PRO 6000 box: 5090-removed draw ~580W peak -> 1200W runs <50%
  load, no power-limiting. PSU scrutiny moves to Machine 2 (the 5090's new home).
- 3.2 Mobo — SWAP Hero -> ProArt X870E-Creator: AM5 has 24 usable CPU PCIe5 lanes (16+4+4);
  both boards split x16 to x8/x8. ProArt isolates lane-stealing to M.2_2 (leave empty) -> GPUs
  stay clean x8/x8; Hero entangles 3 CPU-lane M.2 with the 2nd x16 (wrong slot drops it to x4 /
  disables it). x8/x8 costs ~nothing for inference (no NVLink anyway). Non-negotiable: flash
  latest stable AGESA before first boot, EXPO, multi-hour idle with zero reboots.
- 3.3 GPU topology — SPLIT into two machines, do NOT pool: mismatched VRAM + no NVLink + x8/x8
  -> can't TP efficiently; pooling only enables slow PP of oversized second-tier models; KV
  under PP is pinned per-layer, NOT a 128GB pool; best 2026 open models fit in 96GB at 4-bit;
  the 5090 is worth more as an independent worker; unified-memory boxes (DGX Spark ~273 GB/s,
  Strix Halo ~256 GB/s) are capacity-at-low-bandwidth vs the PRO 6000's 1.79 TB/s (~6.56x).
  Honest note: part of the value is the joy of the engineering itself, which aligns with the
  split. TRIP-WIRE to reverse: a second matched RTX PRO 6000 -> pool for 192GB TP.
- 3.4 Storage tiering: 990 Pro = hot working drive; 2nd 4TB NVMe = warm model library; Synology
  DS225+ NAS (2x WD Red Plus 6TB) = cold archive + Anvil state backups (back up SQLite + JSONL
  on a schedule — most irreplaceable, smallest thing in the setup).
- 3.5 Assembly + warranty: 20-year DIY builder chose to pay Central Computers for assembly +
  warranty — worth it for VALIDATION not labor (cheap insurance on an $11.5K card + early-AGESA
  board). Acceptance checklist handed over.

## 4. Local model lane
- 4.1 Picks (96GB): primary Qwen3-Coder-Next FP8 (80B/3B-active MoE, 256K ctx, ~75GB,
  SWE-bench Verified ~70.6%); co-resident gpt-oss-120b MXFP4 (116.8B/5.1B-active, 128K, ~63GB,
  Harmony format mandatory); honorable mention Qwen3-Next-80B-A3B (linear attention, tiny KV,
  best long-context retention).
- 4.2 KV math: KV_bytes/token = 2 x n_layers x n_kv_heads x head_dim x bytes; GQA + sliding
  window + linear attention shrink it; after 4-bit weights, ~27-50GB KV headroom on the 96GB.
- 4.3 KEY INSIGHT: Anvil's agents share a large common prefix (system prompt + skills + state,
  ~9K+ tokens). SGLang RadixAttention / vLLM APC stores it ONCE and serves it as a cache hit
  across all agents -> each agent's marginal KV cost is only its unique tail. Turns "3 agents x
  32K cramped" (hit on the 32GB 5090) into "3-5 agents x 100K+ context" on one 96GB card.
  Imperative: keep the prefix byte-identical. Same optimization as cloud prompt caching.
- 4.4 SM120 caveats: CUDA >=12.9, driver 580+; NVFP4 weights NOT reliable yet on SGLang SM120
  (use MXFP4 / FP8); SGLANG_ENABLE_JIT_DEEPGEMM=0; prefer --attention-backend flashinfer; FP8
  KV can break GLM-class (use BF16 KV); MTP/speculative works (~2.75x on Qwen MoE); pin a
  known-good nightly; budget a weekend for bring-up.
- 4.5 Effective context: treat ~128K as the reliable working ceiling; lean on Anvil's
  evidence-gated state, not stuffing context.
- 4.6 Starter SGLang launch: Qwen3-Coder-Next FP8, --tp-size 1, --context-length 262144,
  --mem-fraction-static 0.90, --fp8-gemm-backend triton, --attention-backend flashinfer,
  --kv-cache-dtype fp8_e4m3, NEXTN speculative; fall back to bf16 KV / disable-radix on FP8
  correctness issues. gpt-oss-120b on port 30001 (Harmony format).

## 5. Anvil routing economics (cloud + local fabric)
- 5.1 Thesis (validated): volume -> local, stakes -> cloud. Local absorbs high-frequency
  low-stakes structured tiers (sentinel, state-keeper, docs-scribe); rare high-stakes reasoning
  (planner, critic) stays cloud frontier. Expensive planner is rare; cheap sentinel is constant.
- 5.2 Routing policy: planner/critic -> always cloud (high blast/review risk); docs-scribe ->
  local (Qwen) w/ cloud spill on context-load/saturation; sentinel/state-keeper -> local-first,
  cloud on saturation. Score-driven via Anvil's six dimensions. Differentiated capability:
  durable evidence-gated state + score-driven cost/risk arbitrage across a unified fabric.
- 5.3 Capability reality check: keep planner/critic cloud — open-vs-frontier SWE-bench gap is
  real and WIDENS under standardized harnesses (gpt-oss-120b 62.4% vendor -> 26% standardized).
  Open models strong for execution-tier; review/planning carry blast radius.
- 5.4 Economics: local all-in ~$0.55-0.97/hr; capex sunk -> true marginal ~$0.20/hr power. At
  saturation 2,000+ tok/s. Wins decisively when displacing premium-tier or high-volume work
  (break-even vs Sonnet mix ~15-40M tok/mo). Does NOT beat budget APIs (DeepSeek-Flash) on price
  — there the value is privacy, latency, sovereignty.
- 5.5 LiteLLM gateway: local-first with cloud spillover across two replicas;
  usage-based-routing-v2; fallbacks local->cloud; MODEL_TIERS opus->cloud, sonnet->local,
  haiku->local.
- 5.6 Cloud pricing reference (June 2026, re-verify): Opus 4.8 $5/$25; Sonnet 4.6 $3/$15; Haiku
  4.5 $1/$5; GPT-5.5 $5/$30; Gemini 3.1 Pro $2/$12; DeepSeek V4-Flash $0.14/$0.28.

## 6. Status & action items (at handoff)
System at Central Computers, 5-7 day turnaround. Prep offline: pin SGLang nightly; pre-stage
models to NAS; draft systemd + LiteLLM config; sort the 5090's new home (bigger case + ATX 3.1
PSU). Staged bring-up: Ubuntu/CUDA -> gpt-oss-120b first -> Qwen3-Coder-Next FP8 (find the
backend x KV-dtype x radix combo) -> LiteLLM across replicas + Anvil MODEL_TIERS -> observability
+ NAS backups. Security: SGLang binds 0.0.0.0 -> firewall to LAN, LiteLLM w/ key in front,
WireGuard/Tailscale for remote; never port-forward.

## 7. Decision trip-wires
- Second matched RTX PRO 6000 -> reverse the split, pool for 192GB TP (only condition that flips
  split-vs-pool).
- Move planner/critic local -> only if open models close the *standardized-harness* gap to within
  a few points AND validated on the author's own tasks. Any false-approve on high-blast diffs ->
  revert critic to cloud.
- Re-enable NVFP4 -> when SGLang/vLLM SM120 NVFP4 is bug-free.
- Prefix-cache hit rate < 50% -> fix prompt hygiene (byte-identical prefix) before adding local
  roles; the economic case rests on prefix sharing.
- Sustained low utilization (<~2-4 busy GPU-hr/day) -> the sunk capex isn't amortizing; a
  Spark-class box + cloud APIs would've been cheaper. Watch the utilization dashboard.

## 8. One-line summary
Local-inference setup (96GB RTX PRO 6000 + a 32GB RTX 5090) serving the best-fitting 2026 open
MoE models (Qwen3-Coder-Next FP8 + gpt-oss-120b MXFP4) via SGLang with prefix caching to give
3-5 Anvil agents large context — feeding a cloud+local routing fabric where high-volume
structured roles run local at marginal-power cost and rare high-stakes reasoning stays cloud
frontier, arbitraged by Anvil's task scores, made coherent by Anvil's durable system of record.

<<<END VERBATIM>>>
