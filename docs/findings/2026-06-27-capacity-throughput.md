# Finding — the capacity premise has first-party evidence (2026-06-27)

Closes the throttling half of **open question #1** ("do the flat-rate pools throttle often
enough for the capacity premise to hold?") from
[DESIGN-DISCUSSION-2026-06-27.md](../DESIGN-DISCUSSION-2026-06-27.md), and adds a
throughput-equivalence number. Source data: `fakoli-mini:~/post-session-findings/`
(real session retros, not synthetic).

## 1. Throttling is documented inside the runs themselves

The Anvil multi-PRD v0.3 build retro (`anvil-multiprd-v0.3-2026-06-23/SESSION-REPORT.md`):

> "**API 529 overloads** killed review-workflow subagents mid-run, forcing retries (the
> 'server errors / try again' exchange) and wasting wall-time."

> "The 529 overloads happened to strike **review** workflows (retryable), never during a
> merge or release (irreversible)." (filed under "where we got lucky")

A 529 is provider **overloaded** — a capacity/availability failure striking *during* a long
autonomous run, exactly the failure the local floor exists to absorb. This is first-party
evidence, stronger than the (corroborating) Anthropic status-page history.

**Precision:** 529 (overloaded) is an *availability/capacity* failure, distinct from a
flat-rate *quota* throttle (429). The runs prove the availability axis. Whether the
flat-rate quota itself caps during these mega-runs is a separate measurement (see new
question Q1b).

### External corroboration (Anthropic status page, fetched 2026-06-27)

`status.claude.com/history` (Statuspage API): **20+ Claude API incidents logged in June
2026** alone (through the 27th), most "elevated error rate" events across Opus 4.8/4.7/4.6,
Sonnet 4.6, Haiku 4.5. Two overlap the Anvil run windows directly:
- **Jun 22 — Major**, "Elevated Error Rates for Opus 4.8, 4.7, 4.6, Sonnet 4.6, Haiku 4.5"
  (overlaps session A, the design/foundation run).
- **Jun 23 — Critical**, "Elevated error rate across multiple models", 07:08–08:33 PT
  (overlaps session B, the build run — where the first-party 529s were logged).

So the in-run 529s were the visible edge of provider-acknowledged incidents, not a local
fluke. **Caveat:** April/May show no incidents because the feed window only covers recent
history, not because those months were clean. Absence of data, not data of absence.

## 2. What the documented sessions burned

| Session | Wall-clock | Generated (output) tokens | Total processed (incl. cache) |
|---|---:|---:|---:|
| Anvil multi-PRD (Claude, 2 sessions, 2026-06-22 -> 24) | ~44.1 h | 46,358,157 | ~1.0 B |
| Codex (2026-06-25) | ~7.7 h | 418,196 | 39.6 M |
| **Combined** | **~51.8 h** | **~46.8 M** | **~1.05 B** |

The Anvil run dominates (890 workflow subagents across 36 workflows; ~92% of generated
tokens were delegated to background workflows).

## 3. The token distinction that changes the answer ~20x

~95% of "tokens" in these runs are **cache reads** (the system prompt, skills, and tool
schemas re-read every turn) — that is *prefill*, which SGLang prefix caching (RadixAttention)
makes nearly free to re-process. Only the **~46.8M generated tokens** are real *decode* work
the local box would have to produce. Dividing the 1.05B total by a decode rate is the wrong
math.

## 4. Throughput-equivalence (hours to emit 46.8M generated tokens locally)

Measured anchor: **200 tok/s on the 5090** (the welder eval). The 96GB box is **not yet
benchmarked**, so every rate above 200 is a TARGET, not a result.

| Local generation rate | Hours to emit 46.8M | Status |
|---|---:|---|
| 200 tok/s (measured, 5090) | **~65 h** | real |
| 400 tok/s | ~32 h | target |
| 800 tok/s (96GB box, batched) | ~16 h | target |
| 1,500 tok/s (aggressive aggregate) | ~9 h | target |

**Read:** at the measured rate, the box needs ~65 h of continuous generation to match ~52 h
of cloud wall-clock output: same order of magnitude. With the 96GB card's larger batch across
concurrent agents, beating cloud wall-clock on raw throughput is plausible.

## 5. What this proves, and what it does NOT

- **Proven:** raw token-generation *capacity* is not the bottleneck, and provider
  availability genuinely fails under this load. The overflow thesis is throughput-feasible.
- **NOT proven:** that local output of that volume is *correct*. 46.8M cloud tokens came from
  frontier models with the worldview; the same *quantity* locally is not the same *quality*.
  That gap is the supply-vs-demand split, and it is exactly the bake-off's false-pass/rework
  number. The engine is big enough; whether the work passes is unmeasured.

## 6. New questions this opens (added to the design log)

- **Q1b — quota vs availability.** The runs prove 529 *availability* failures; do the
  flat-rate *quotas* also cap during a mega-run? Measure 429/quota-exhaustion frequency
  distinctly from 529s.
- **Q7 — 96GB aggregate throughput.** What is the box's *actual* sustained aggregate tok/s
  under concurrent-agent load? (The 200 tok/s anchor is the 5090.) The bake-off should capture
  it; until then rows above 200 are targets.
- **Q8 — local prefix-cache hit rate.** The cloud economics depend on ~95% cache reads being
  cheap; does the local serve's prefix cache hit at a comparable rate for these long, varied
  agent contexts? It is the key local-economics variable.
- **Q9 — concurrency, not just throughput.** The cloud run compressed wall-clock with ~890
  parallel agents. Can the local box's batching match that concurrency, or does serial-ish
  local execution stretch wall-clock past usefulness even when total throughput suffices?

## Sources
- `fakoli-mini:~/post-session-findings/anvil-multiprd-v0.3-2026-06-23/` (SESSION-REPORT.md,
  both-sessions-summary.json, session_stats.json)
- `fakoli-mini:~/post-session-findings/codex-session-2026-06-25/` (SESSION-REPORT.md,
  session_stats.json)
- Measured local anchor: the welder eval (SGLang Qwen3.x-35B-A3B on the 5090), 200+ tok/s.
