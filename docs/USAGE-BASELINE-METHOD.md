# Claude Code Usage Baseline -> Inference Design

**Owner:** Sekou Doumbouya - **Date:** 2026-06-26 - **Data:** all `~/.claude/projects/**/*.jsonl` on fakoli-mini
**Window:** 2026-05-17 -> 2026-06-25 (18 active days) - 4,436 session files, 4,190 with model calls, **140,080 assistant API calls**
**Method:** `scripts/aggregate_usage.py` (mirrors your `session-retro/session_stats.py` field definitions). Raw: `data/aggregate.json`.

> **One-line verdict:** your own telemetry *is* the design spec — and it endorses the cloud-orchestrator + local-specialist split you already chose. **~86% of generation is delegated specialist work** (local-servable), while the **14% orchestration carries the entire 64K–1M-token context tail** that must stay on cloud. So size the local rig for *concurrency and prefix-cache*, not for matching Opus's context window.

---

## 1. What the month looks like

| Metric | Value |
|---|---:|
| Assistant API calls | 140,080 |
| Output (generated) tokens — main loop | 70.9M |
| Subagent tokens — delegated via workflows | 444.8M |
| **Total generative** | **~515.7M** (86% delegated) |
| Total tokens processed (incl. cache) | 18.53B |
| Cache reads (share of processed) | 95.2% |
| Workflow dispatches / total subagents | 1,090 / 6,535 |
| Sessions / median turns / longest | 4,190 / 16 / 2,101 turns (89.9 h) |
| Busiest day (2026-06-21) | 9.67M output, 11,665 calls |

**Model mix (by output tokens):** `opus-4-8` 63.6M (**89.8%**), `opus-4-7` 2.6M, `haiku-4-5` 2.17M, `sonnet-4-6` 1.33M, `fable-5` 1.16M.
**Model mix (by calls):** opus-4-8 82%, sonnet 7.5%, haiku 7.2%. The cheaper tier (sonnet+haiku = 20.6K calls) is only ~5% of output — short utility/subagent turns.
**Tool shape (agentic, shell-heavy):** Bash 37.6K, Read 19.9K, Edit 7.7K, StructuredOutput 3.2K, WebFetch 1.5K, Grep 1.5K, WebSearch 1.0K, Write 806, Agent 292, Workflow 239. Your Fakoli crew shows up as subagents: critic 36, welder 18, guido 9, sentinel 8, scout/keeper/herald/smith.

This is not "chat with an assistant." It's an autonomous engineering org: an Opus orchestrator delegating bounded work packets to fleets of specialists, gated by critics, driving PRs.

---

## 2. The four numbers that size a local inference server

### a) Context per call — the binding constraint
Per-call context = `input + cache_creation + cache_read` (what the model actually prefills):

| p50 | p90 | p95 | p99 | max | mean |
|---:|---:|---:|---:|---:|---:|
| 64.7K | 362K | 578K | 852K | **999K (~1M)** | 131.8K |

More than **half your calls exceed 64K context**, and the tail runs to the full 1M window. Your current local serve is **32K** — it covers a *minority* of calls. But the giant contexts are the **orchestrator re-reading a long, growing session every turn** (that's why cache reads are 95% of all tokens). Specialists work on bounded packets; the orchestrator owns the tail.

### b) Generation per call — tiny, prefill-bound
| p50 | p95 | max | mean |
|---:|---:|---:|---:|
| **56** | 2,465 | 31,355 | ~506 |

Median completion is **56 tokens** — most calls are quick tool-turns. Cost lives in **prefill of large, shared-prefix contexts**, not decode. Decode throughput is *not* your bottleneck.

### c) Concurrency — bursty fan-out, not parallel sessions
- Workflow fan-out per dispatch: p50 **6**, p95 **20**, **max 160** parallel subagents.
- Peak request rate: **287 calls/min** (p99 123/min, p95 60/min).
- Peak parallel *top-level* sessions: only 7 — so the load is **fan-out inside a workflow**, not many separate chats.

Your current local serve allows **3 concurrent**. A single workflow wave (p95 = 20 agents) would queue 7-deep instantly.

### d) Throughput — comfortably within reach
Peak hour: 1.34M output tokens = **373 tok/s sustained** (output). Your SGLang Qwen3.6 box already does ~144/378 tok/s. Throughput is the *easy* axis; context + concurrency are the hard ones.

---

## 3. Inference-design implications

**The split is correct, and now quantified.** Keep **Opus (cloud)** as orchestrator + critic — it owns the 14% of generation that needs frontier judgment *and* the entire 64K–1M context tail. Serve **specialists locally** — they're 86% of generation, on bounded contexts, and tolerant of a strong open model (your Qwen3.6-35B-A3B). Don't try to clone the 1M-context orchestrator on local silicon; the KV math forbids it (below).

**1. Size local context to the specialist packet, not to Opus.**
For a 32B-class model (≈64 layers, GQA), KV is on the order of **~256 KB/token at fp16, ~128 KB/token at fp8**. So on the 96 GB RTX PRO 6000:
- 32K ctx ≈ 8 GB/seq (fp16) — today's setting, too small for your calls.
- **128K ctx ≈ 32 GB/seq** — feasible with room for a few concurrent + fp8 KV.
- 256K ctx ≈ 64 GB/seq — one sequence nearly fills the card.
- 1M ctx ≈ 256 GB — **infeasible** locally; stays cloud.

Recommendation: serve local at **128K context with fp8 KV cache**. That covers the specialist distribution and a good chunk of mid-size orchestration, while the 578K–1M tail routes to cloud. (A precise specialist-only context percentile needs the split in §5.)

> **Update (2026-06-26, refined by deep research):** the ~256 KB/token above assumes a *dense* 32B model. Your served model is a Qwen3 **MoE** (~48 layers, 4 KV heads, head_dim 128) -> KV is only **~96 KB/token (fp16) / ~48 KB/token (fp8)** — ~2.7x cheaper. So context is *not* your binding constraint: at FP8 weights + FP8 KV the 96 GB card holds **256K context with several concurrent** streams. Full serving recommendation: `LOCAL-SERVING-STACK-BLUEPRINT.md`.

**2. Build for fan-out, not for parallel chats.** Turn up continuous batching to absorb a **p95 of ~20 concurrent** with headroom to ~32, and queue gracefully beyond. On the dual-GPU rig this is where the second card earns its keep — either more batch slots on the 96 GB card, or a **second model replica on the 5090** for overflow (data-parallel, not tensor-parallel — see `DUAL-GPU-BLACKWELL-SETUP.md`).

**3. Prefix caching is your single highest-leverage lever.** Median 56-token generations + 1,090 workflows that fork agents sharing the same harness prompt = enormous shared-prefix reuse. SGLang RadixAttention (already your pick) computes that prefix KV once per wave. **Track prefix-cache hit rate as the #1 serving KPI** — it matters more than raw tok/s for your pattern.

**4. Map onto the Blackwell pair.**
- **RTX PRO 6000 (96 GB, 300 W, always-on):** primary specialist endpoint — Qwen3.6-35B-A3B at **128K ctx, fp8 KV, ~20-32 batch slots**, RadixAttention on.
- **RTX 5090 (32 GB, toggleable):** overflow replica for fan-out bursts and/or a **draft model for speculative decoding**; frees for gaming without taking the endpoint down.
- **Cloud Opus:** orchestrator + critic + any call whose context exceeds the local ceiling.

**5. Cost framing.** ~444.8M delegated subagent tokens/month is the offload target. Even partial migration of specialist execution to the local 96 GB card removes that volume from per-token cloud billing, while the flat-rate orchestrator stays cloud. The local rig pays for itself on the *delegated* tier, which is exactly the high-volume, bounded-context, quality-tolerant 86%.

---

## 4. Recommended starting config (local SGLang)

Versus today's `32K ctx / 3 concurrent`:

```
--context-length 131072            # 128K — covers the specialist packets
--kv-cache-dtype fp8_e5m2          # halve KV; ~doubles context/batch headroom
--max-running-requests 24          # absorb a p95=20 workflow wave + headroom
--enable-radix-cache               # prefix reuse across forked agents (default on)
--served-model-name qwen3.6-35b-a3b-local
# host on the RTX PRO 6000 (CUDA_VISIBLE_DEVICES=1); keep the 5090 free/overflow
```
Validate against the real load: replay a busy day's request sizes and watch prefix-cache hit rate, queue depth at fan-out, and KV-eviction.

---

## 5. Next analysis (to tighten the numbers)
- ✅ **DONE — Split context by role** (`scripts/role_split.py`, `data/role_split.json`): orchestrator (main) = median **350K** ctx (p95 850K, max 1M) → stays cloud. Subagent (specialist) = median **55K** ctx (p95 159K). Local-context coverage of specialist calls: 32K→22%, **64K→60%, 128K→91%, 256K→99.5%**. So set the **local context ceiling at 128K (covers 91%) or 256K (covers 99.5%)**.
- **Inter-arrival timing inside workflow waves** to size max batch and queue policy.
- **Prefix-overlap measurement** across a workflow's agents to estimate achievable cache-hit rate.
- Fold in the **Codex rollouts** (`~/.codex/sessions`) for the full cross-harness picture.

---

## Provenance
- Aggregator: `projects/claude-usage-analysis/scripts/aggregate_usage.py` (stdlib only; reads local logs, no network).
- Field definitions mirror `~/code/claude-env/fakoli-plugins/plugins/session-retro/scripts/session_stats.py`.
- Raw results: `projects/claude-usage-analysis/data/aggregate.json`. All five internal consistency checks passed (totals, percentile monotonicity, per-model call sum, daily-vs-total output).
- Companion: `projects/fakoli-local-stack/DUAL-GPU-BLACKWELL-SETUP.md`.

### Changelog
- **2026-06-26** — Created. Aggregated 140,080 calls across 4,190 sessions (May 17–Jun 25); derived context/generation/concurrency/throughput distributions and the cloud-orchestrator + local-specialist sizing for the Blackwell pair.
