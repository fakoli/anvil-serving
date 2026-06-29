---
title: "OpenClaw & Hermes Agent — router customization surface vs the closed harnesses"
date: 2026-06-29
status: research-result
question: "Do openclaw.ai or Nous hermes-agent expose router-relevant customization (esp. per-request hooks) beyond Codex/Claude Code?"
method: "2 web-fetched tool finders → adversarial verification of each load-bearing claim → synthesis. Workflow wf_b7292eb6-835, 5 agents."
verdict: "OpenClaw: YES (per-request routing via hook). Hermes Agent: NO over the wire (strong Tier-1, multi-slot)."
---

> **Provenance.** Multi-agent research that fetched each tool's site/repo/docs and adversarially
> verified the load-bearing claims. Homepages are JS-gated/marketing; load-bearing facts come from
> the GitHub `docs/` and issue trackers, not the landing pages. Low-confidence items flagged below.
> Evidence base for the harness scope in [`../QUALITY-GATED-ROUTER.md`](../QUALITY-GATED-ROUTER.md).

## What they are

- **OpenClaw** (`openclaw.ai`, github.com/openclaw/openclaw): open-source self-hosted "personal AI
  assistant" gateway bridging chat apps (WhatsApp/Telegram/Discord/Slack/Signal/iMessage) to coding
  agents on your machine. Both harness and gateway; model-agnostic; BYO key. TypeScript Plugin SDK.
- **Hermes Agent** (`hermes-agent.nousresearch.com`, github.com/NousResearch/hermes-agent, MIT):
  open-source self-improving multi-platform autonomous agent from Nous Research; one gateway across
  Telegram/Discord/Slack/WhatsApp/Signal/Email/CLI. Coding-capable; model-agnostic. **Distinct from
  the Nous "Hermes" *model* family.**

## Comparison vs the verified baseline

| Tool | base_url override | Free-form model string | Per-request hook / extra fields | Extensibility | Model slots | Net tier |
|---|---|---|---|---|---|---|
| **OpenClaw** | Yes — `models.providers.<id>.baseUrl` (OpenAI/Anthropic compat) | Yes — self-allowlisted explicit ids sent verbatim | **Yes (model/provider only)** — `before_model_resolve` hook returns `modelOverride`/`providerOverride` **per turn**; no documented per-request header/body hook (static `headers`/`extra_body` only) | Open-source (TS Plugin SDK, ~28 hooks; patchable) | per-agent primary + allowlist + failover; **per-turn override via hook** | **Per-request Tier 1, reaching toward Tier 2 (routing only)** |
| **Hermes Agent** | Yes — `provider: custom` + `base_url` (any OpenAI-compat `/v1`); first-class | Yes — free-form strings matching your provider's catalog, verbatim | **No (over the wire)** — hooks CANNOT set model/provider/base_url/headers/extra_body; `pre_llm_call` injects text into the user message only | Open-source MIT (Python plugins, ~13 hooks, MCP); Tier 2 only via fork | **main + ~11 aux slots** (vision/compression/web/…) + fallback + MoA + subagents | **Tier 1 (strong, multi-slot)** |
| Claude Code | Yes — `ANTHROPIC_BASE_URL` | Yes — `ANTHROPIC_CUSTOM_MODEL_OPTION` skips validation | No — only fixed opaque `metadata.user_id` | Closed | ~3–4 fixed slots | Tier 1 |
| Codex CLI | Yes — `config.toml` | Yes | No per-request — headers/query_params at config level only | Closed | few | Tier 1 (+ static Tier-2) |

## Verdict

**OpenClaw — YES (HIGH confidence).** `before_model_resolve` runs **per agent turn**, receives the
prompt + attachment metadata, and can return `providerOverride`/`modelOverride`. A local plugin can
classify each turn and override the resolved provider/model verbatim — making the Tier-1 model-string
carrier **per-request**, escaping the session-coarse limit that binds Claude Code/Codex. It does
**not** expose a per-request arbitrary header/body channel (only static config-level), so it's
per-request *routing*, not a structured Tier-2 side-channel.

**Hermes Agent — NO over the wire (HIGH confidence).** Stronger Tier-1 (first-class custom base_url,
verbatim free-form models, and many more slots than Claude Code), but its hooks **explicitly cannot
modify the outgoing request**; `pre_llm_call` only injects user-message text (reaches the router as
Tier-0-parseable content). Confirmed by an **open, unmerged feature request (#23739 / PR #23898)**
asking for exactly this — today plugins must "monkey-patch internals." True Tier 2 requires a fork.

## What it changes for the router design

Keep **Tier 0 (infer)** as the floor and **Tier 1 (verbatim free-form preset in the `model` field)**
as the primary contract. Add an explicit **Tier-2 "hook plugin" seam**:

> When a harness exposes an in-process model-resolution hook (**OpenClaw today**), per-request intent
> is achievable **client-side** — a published `before_model_resolve` plugin classifies the turn and
> emits an anvil-serving preset id (e.g. `planning`, `quick-edit`) as the `modelOverride`. The router
> needs **no new wire field** — just a stable preset vocabulary. This pushes Tier-0 classification to
> the client (cheaper, better-informed) and turns the router into a clean executor.

Concrete actions: (1) ship anvil's named-preset contract so it accepts verbatim free-form preset
strings in the model field; (2) provide a **reference OpenClaw plugin** as the proof-point; (3) size
the preset namespace richly enough to absorb Hermes's many per-session model strings. **No dependency
on either tool.** Hermes shows the alternative (payload text injection) collapses back to Tier-0 parsing.

## Scope recommendation

- **OpenClaw — IN SCOPE; near-first-class as a documented Tier-2 integration seam** (not a core
  dependency). It's the clearest external proof that a hook-plugin seam is worth designing for. Hold
  short of full first-class because the supporting docs are vendor-published (finding confidence
  medium, though the routing verdict is HIGH).
- **Hermes Agent — support as a strong Tier-1 client; DEFER as a Tier-2 target** (gives the router
  nothing beyond Tier 0/1 without a fork).

## Low-confidence / flagged items

- OpenClaw per-request **arbitrary header/body** injection: **UNCONFIRMED** — only static config-level
  `headers`/`extra_body` documented. Don't claim a structured Tier-2 side-channel.
- OpenClaw **fixed named role slots** (main/fast/subagent): **UNCONFIRMED** in docs; selection is
  per-agent/session except the per-turn `before_model_resolve` override.
- OpenClaw evidence is largely **vendor docs** (docs.openclaw.ai) + repo; finding confidence medium,
  but the load-bearing hook capability is corroborated across hooks/agent-loop/trust-config (verdict HIGH).
- Hermes **`runtime_override`** hook return shape: **REFUTED as shipped** — absent from `hooks.md`,
  contradicted by open issue #23739; describes the unmerged PR.
- Hermes issue **#2817** alleges `pre_llm_call`/`post_llm_call` may be "documented but never invoked"
  in some versions — if true, hooks are weaker still (only strengthens the negative verdict).
- Both homepages JS-gated/marketing; facts taken from repos' `docs/` + issue trackers.
