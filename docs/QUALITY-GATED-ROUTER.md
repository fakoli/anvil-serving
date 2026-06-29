# anvil-serving as a harness product: the quality-gated router

> **Status:** design / proposal — 2026-06-28. Sets the product direction for anvil-serving
> as a **harness-facing** tool (Claude Code, Codex, Cursor, Cline, Aider, any OpenAI/Anthropic
> client) rather than an anvil-coupled serving tier.
> **Grounded in:** [`findings/2026-06-28-anvil-integration-audit.md`](findings/2026-06-28-anvil-integration-audit.md)
> (the integration point is the runtime, not anvil) and
> [`findings/2026-06-28-planning-capability-eval.md`](findings/2026-06-28-planning-capability-eval.md)
> (local quality is work-class-dependent and *measurable*).

## 1. Thesis

> anvil-serving is the **workload-aware, correctness-gated local-model router** for coding
> harnesses. **Local where it's been proven, cloud where it hasn't — verified, with automatic
> fallback.**

A coding harness points at one anvil-serving endpoint. Per request, anvil-serving decides which
**tier** (fast-local / heavy-local / cloud) should serve it, based on a **measured per-(model,
work-class) quality profile**; cheaply **verifies** the result; and **falls back** to the next
tier (ultimately cloud) when the local answer fails. The harness sees one reliable endpoint and
never eats a silent local-quality failure mid-run.

## 2. Why this is the wedge (and not "another proxy")

Transport is commodity (LiteLLM, claude-code-router, Ollama, OpenRouter). None of them know
**whether local can actually do *this* work** — they route by static rules (model name, cost,
regex). Our planning eval is the proof that the missing primitive exists and is buildable:

- local output is ~100% **structurally** valid but ~**2/5** on dependency reasoning — a dumb proxy
  sends that to local and silently corrupts a long agent run.
- the gap is **per-work-class and measurable**, so routing can be **evidence-based**.

The defensible asset is therefore **not** the proxy — it's the **quality profile** (per model ×
work-class, measured on the user's own workload) plus the **verify-and-fallback loop**.
Competitors can copy transport in a weekend; they can't copy "we measured that `gpt-oss-20b` is
safe for bounded edits but unsafe for dependency planning on *your* repos."

## 3. Architecture

```
          ┌─────────────── anvil-serving router ───────────────┐
harness → │ front door → classify → route → [verify] → return  │ → harness
(CC/Codex)│   (Anthropic    (work-    (quality      │  on fail ↘        │
          │    + OpenAI      class)    profile +     │  fall back to     │
          │    dialects)              live health)   │  next tier/cloud  │
          └──────────────────────────┬──────────────┴───────────────────┘
                                      ▼
                fast-local :30001   heavy-local :30000   cloud (Anthropic/OpenAI)
                  (multiplexer-managed)                    (user's existing key/sub)
```

**Control plane (slow, offline):** `profile` → shadow-eval → **routing table** (the quality
profile). Refreshed on demand and continuously calibrated from sampled production traffic.

**Data plane (fast, per-request):** classify → route → optional inline verify → fallback. Must add
negligible latency and must stream.

## 4. The quality profile (the moat)

A table keyed `(model, work-class)` → `{quality_score, sample_n, last_measured, decision}` where
`decision ∈ {allow, allow-with-verify, deny}`. Populated by:

1. **Bootstrap** from the shadow-eval harness already built
   (`findings/eval-data/2026-06-28-planning-capability/`): replay representative requests per
   work-class to each local tier, grade against cloud (deterministic checks + blind/LLM judge),
   emit the table. Generalize that harness from "planning" to arbitrary work-classes.
2. **Calibrate** continuously: async-sample a small % of production responses, grade with cloud
   off the hot path, update scores. The table tracks model/quant/serve changes over time.
3. **Right-size** from the user's real usage via existing `profile` (which work-classes dominate
   *their* traffic — focus measurement where it matters).

This is what makes routing *evidence-based* instead of vibes.

## 5. Work-class classification

The router must label each request cheaply. Signals, in increasing cost:
- **Cheap, deterministic:** prompt size / context length, presence of tools, requested
  `max_tokens`, system-prompt fingerprint (harnesses have recognizable agent prompts — e.g. a
  planner vs an edit vs a chat turn), task hints the harness already sends.
- **Lightweight model:** a tiny local classifier (or the fast tier itself) tags work-class when
  heuristics are ambiguous — but only if it stays off the latency budget.
- **Taxonomy v0** (start coarse; the eval shows class matters more than precision): `chat/Q&A`,
  `bounded-edit`, `multi-file-refactor`, `planning/decomposition`, `review/critique`,
  `long-context-retrieval`. Each gets a profile row per model.

## 6. Verify-and-fallback (the honest hard part)

Inline LLM-grading every response would defeat the purpose (cost + latency). So **most "quality
control" is routing done ahead of time** (§4–5); verification is a cheap safety net, tiered:

1. **Prevent (primary):** never send a `deny` work-class to local. Free; catches the biggest risks
   (e.g. dependency planning → cloud, always).
2. **Cheap structural verify (inline):** near-zero-cost checks that caught real failures in our
   eval — empty/truncated content (thinking-budget starvation), tool-call JSON that doesn't
   validate, code that doesn't parse, a diff that doesn't apply, malformed format. Fail → fallback.
3. **Confidence signals (inline, where available):** logprob/entropy thresholds, refusal/uncertainty
   markers.
4. **Async LLM-judge (off hot path):** sampled cloud grading that feeds the profile (§4.2), not a
   blocking gate.

**Fallback policy:** on verify-fail / error / timeout / low-confidence → retry next tier up
(fast→heavy→cloud). Guardrails: cap retries, prevent thrash, make fallback idempotent for the
harness (especially mid-stream — see §10), and **log every fallback** (a fallback is a profile
signal: that class may need to be downgraded to `deny`).

## 7. Harness integration (drop-in)

One front door speaking two dialects so it's zero-config for the major harnesses:
- **Anthropic Messages API** → Claude Code points at it via `ANTHROPIC_BASE_URL` /
  `ANTHROPIC_AUTH_TOKEN`; honor `CLAUDE_CODE_SUBAGENT_MODEL` semantics.
- **OpenAI Chat Completions** → Codex / Cursor / Cline / Aider / generic clients.
- Translate between dialects and to each backend's quirks (the gotchas: thinking-default models
  need generous `max_tokens` and can't take `chat_template_kwargs` over some paths; sm_120
  engine/quant matrix; etc.). **Absorbing this friction is itself a feature.**

## 8. Reuse map — most of this exists

| Capability | Module | Status |
|---|---|---|
| Right-size from real usage | `profile` | exists |
| Per-model serving facts / sane defaults | `models sync`, `analyze` | exists / designed |
| Bring up + on-demand model swap on one GPU | `multiplexer` (multi-engine, single-resident) | exists |
| Correctness gate | `preflight` | exists |
| Throughput / capacity measurement | `benchmark` | exists |
| Per-work-class quality measurement | shadow-eval harness | **built this session** (generalize) |
| **Front door + classify + route + verify + fallback** | new `router` module | **the build** |

The genuinely-new surface is the router data plane + the quality-profile control plane. The serving
substrate is already here.

## 9. MVP milestones

- **M0 — front door:** Anthropic + OpenAI endpoints, pass-through to one backend, streaming. Makes
  anvil-serving drop-in for Claude Code today.
- **M1 — static router + multiplexer:** work-class classify (heuristics only) → tier rules over the
  multiplexer (bounded→fast, long-ctx→heavy, planning/review→cloud). Hand-authored table.
- **M2 — the wedge:** cheap structural verify + fallback-to-cloud on failure, with fallback logging.
  This is the first release that delivers the unique promise.
- **M3 — measured table:** generalize the shadow-eval to populate the quality profile per
  work-class; replace the hand-authored table; add async calibration. This is the moat turning on.

Ship M0–M2 to be *useful and unique*; M3 makes it *defensible*.

## 10. Risks / open questions

- **Classification accuracy** — wrong class → wrong route. Mitigation: coarse taxonomy, bias
  ambiguous→safer tier, log+calibrate. (Eval shows class matters more than precision.)
- **Verify cost/latency** — must stay structural/cheap inline; anything heavier goes async.
- **Streaming + mid-stream fallback** — hardest engineering problem: detecting failure after tokens
  have streamed to the harness. Likely need a short non-streamed "commit window" for fail-prone
  classes, or speculative buffering. Spike this early.
- **Fallback thrash / cost blowups** — caps, circuit-breakers, per-session budget awareness.
- **Stateful agents** — harnesses keep conversation/tool state; switching tiers mid-conversation must
  preserve context. Define tier-switch boundaries (turn-level, not token-level).
- **Profile staleness** — model/quant/serve swaps invalidate rows; key the table on a serve
  fingerprint and re-measure on change.
- **Privacy** — async cloud calibration sends sampled local traffic to a cloud grader; must be
  opt-in and redactable (a selling point for the local-first crowd, so get it right).

## 11. Success metrics

- **% of agent traffic safely served local** (the capacity/cost win) at a **bounded rework rate**
  (the quality guarantee) — the two numbers that define the product.
- **Silent-failure rate ≈ 0** (verify+fallback catches local misses before the harness does).
- **Cloud tokens saved** vs all-cloud, holding accept-rate constant.
- **Drop-in time** (minutes from `pip install` to a harness running through it).
