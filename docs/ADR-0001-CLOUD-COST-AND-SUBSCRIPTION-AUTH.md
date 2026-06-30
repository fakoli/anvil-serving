# ADR-0001 — Cloud cost & subscription auth: why anvil should not relay cloud

- **Status:** Proposed — analysis captured, **decision deferred** (no implementation committed).
- **Date:** 2026-06-30
- **Context owner:** product direction (cost).
- **Supersedes / relates to:** the Agent-SDK golden rule in `CLAUDE.md`; `OPENCLAW-INTEGRATION-SPEC.md`; issues #42 (tool-call passthrough), #43 (provider-model resolution).

## Problem

The operating goal is **do not pay per-token (metered) API billing** for Claude or Codex/OpenAI. We
have flat **subscriptions** (Claude Max, ChatGPT/Codex). Today anvil's cloud tier is a
`CloudBackend` that relays harness traffic to `api.anthropic.com` / `api.openai.com` using an
operator **API key** — i.e. metered billing on every cloud-served request. We want that to be **$0
metered**.

## The hard reality: a subscription is not a relayable API

Anthropic and OpenAI deliberately split access into two products:

| | Subscription (Claude Max, ChatGPT/Codex) | API (`api.*.com`) |
|---|---|---|
| Billing | flat fee | per-token metered |
| Access | **only** via their agent apps (Claude Code, Codex CLI, ChatGPT) | raw HTTP, programmatic |
| Auth | app OAuth / device login | `*_API_KEY` |
| Shape | an **agent loop** (own system prompt, tools, turns) | transparent `Messages` / `ChatCompletions` |

Consequences:

1. **There is no supported, ToS-compliant way to relay an arbitrary request on a subscription.**
   The subscription is reachable only through an agent harness, not a raw endpoint.
2. The Claude **Agent SDK** *does* run on the subscription — but it is an **agent loop**, not a
   transparent passthrough. Driving a harness's raw request through it loses wire fidelity (C1),
   loses tool calls (#42), imposes Claude Code's own system prompt/tools, and uses the subscription
   as an API backend for *another* harness's traffic — outside its intended use and fragile.

**Therefore: "swap the API key for the subscription inside `CloudBackend`" is not viable.** The cost
goal cannot be met by changing how anvil *relays* cloud.

## Considered: driving `claude -p` (headless Claude Code) as the cloud backend

A natural idea: `claude -p "..." --model opus` runs on the **subscription** (Agent SDK under the
hood) and lets you pick the model — and shims that wrap it behind an OpenAI-compatible endpoint
exist. So getting subscription-billed Claude output programmatically is **technically possible**. It
is **not** a viable transparent cloud backend for the router, and the failures land hardest on the
coding traffic that matters:

1. **It is an agent, not a completion endpoint.** `claude -p` runs the full Claude Code agent — its
   own system prompt, its own tools, its own multi-step loop. The harness's request already carries
   *its own* system prompt, messages, and tool definitions; feeding that in yields
   Claude-Code-*doing-the-task* (editing files in its sandbox, running bash) and summarizing, not a
   transparent completion of the harness's request. Two agent contexts collide.
2. **Tool-calling breaks (fatal for coding turns).** A coding harness sends tool definitions and
   expects `tool_use` blocks back that **it** executes against **its** workspace. `claude -p` runs
   its *own* tools internally; the harness never receives the `tool_use` blocks, so its agent loop
   is broken. This is #42 made unrecoverable.
3. **Wire fidelity is lost.** The harness expects Messages / ChatCompletions SSE (`tool_use`,
   `stop_reason`, token counts). `claude -p` emits Claude Code's own event schema; collapsing "used
   5 tools, edited 3 files" into one Messages completion is ill-defined.
4. **Outside intended use / ToS — and a public-product liability.** Using a Max subscription's
   Claude Code as a programmatic API backend for other traffic is outside its interactive-agent
   intent. Baking it into a *shipped* product could get users' accounts flagged.
5. **Overhead.** A full agent turn (process spawn, system-prompt + tool init) per request is heavy
   and slow for a serving tier.

**Narrow exception:** pure **text-only, non-tool** requests — planning / reasoning / chat, with
Claude Code's tools disabled and the system prompt overridden — could be approximated this way. That
includes the *planning* work-class (cloud-preferred and text-shaped). For a personal / self-hosted
box this is a plausible subscription-billed planner; it is still ToS-gray and slow, and is **not**
appropriate as a shipped default.

**Decisive point:** the subscription you would reach for via `claude -p` is the **same** one the
harness already holds. OpenClaw / Claude Code already calls Claude on the subscription,
protocol-correctly. The clean way to "use the subscription for cloud" is to let the **harness** serve
cloud (the advise-and-defer design below) — not to have anvil puppet a nested `claude -p` that breaks
the protocol. `claude -p`-as-backend is a redundant, lossy second path to a resource the harness
already has cleanly.

## The cost-optimal architecture: anvil is never in the cloud path

The harness in front of anvil (OpenClaw, and behind it Claude Code / Codex) **already holds the
subscription** and already talks to the cloud on it. So the fix is not to make anvil's cloud relay
cheaper — it is to **remove anvil from the cloud path entirely** and let the harness's own
subscription-authed provider serve cloud.

```
        ┌─ local-class  → anvil → free local GPU tiers         ($0, no key)
harness ┤
        └─ cloud-class  → harness's own Claude/Codex provider  (flat subscription)
```

anvil becomes a **local accelerator + routing brain that sits beside the cloud path, not in front of
it.** The OpenClaw `before_model_resolve` plugin (which already classifies client-side using the
shared `tier0_keywords.json` vocabulary) decides per request: **local → anvil; cloud → the harness's
native provider.** anvil holds **no cloud API key**; the metered surface is gone from the default
path.

## Downstream impact

### Keep (the moat is untouched)
- Local serving (`RelayBackend` → SGLang/vLLM) — already keyless and free.
- The structural **verify** gate (catches local misses).
- The measured per-(model, work-class) **quality profile** — this *is* the local-vs-cloud decision
  engine and remains the product's IP.

### Change
- **`CloudBackend` (API-key relay) → optional, off by default**, documented as "metered $; only for
  single-endpoint harnesses that cannot route cloud themselves."
- **C4 (verify-and-fallback) is reshaped.** Today anvil falls back to cloud *itself* mid-request.
  New model: decide **upfront** from the profile — `allow` / `allow-with-verify` serve local;
  `deny` defers to the harness's cloud. On the rare local **verify-failure**, anvil returns a
  **retryable signal** so the harness redoes that one request on its subscription — a latency cost
  on the uncommon miss, not a metered dollar. (This depends on harness retry/fallback support.)
- The **routing decision** moves client-side (the plugin already has the classifier) or to a cheap
  anvil "decision" endpoint that returns local-vs-cloud without serving.

### Give up (all acceptable)
- anvil no longer *sees* cloud responses, so it cannot verify them — but cloud is the **trusted**
  tier (you escalate there *because* it is higher quality), so that verification never mattered.
- No unified decision-log/observability for cloud traffic inside anvil (the harness owns it). The
  plugin can report routing decisions back if observability is wanted.
- Seamless mid-request cloud fallback becomes a **harness retry** on the rare local verify-failure.

### Hard constraint
- This requires a harness that can do **per-request provider routing with a fallback path**.
  **OpenClaw can** (the `before_model_resolve` hook). Raw **Claude Code / Codex pointed at a single
  base URL cannot** — they send everything to one endpoint. This is precisely why the OpenClaw
  gateway is the integration point; a single-endpoint harness is the only case that still needs the
  metered `CloudBackend` relay.

## Product recap (cost-reframed)

**anvil-serving** turns your **free local GPU** into the *default* for coding-agent traffic. A
measured quality profile decides, per (model, work-class), what is safe to serve locally; a cheap
structural verify gate catches local misses. Everything local cannot handle stays on your
**existing Claude / Codex subscription**. The bill becomes **flat subscription + free GPU, with $0
metered API**. The moat is not the proxy — it is the **profile** that knows what your local models
are actually good enough for. The cost goal does not merely survive this design; it **defines** it:
anvil's job is to shrink the slice that ever needs cloud, and to route that slice through auth you
already pay for at a flat rate.

## Decision

**Deferred.** This ADR records the reality and the recommended direction; the pivot is not yet
committed. Options on the table:

1. **Pivot to advise-and-defer (recommended):** anvil = local serve + routing brain; the harness
   owns cloud on its subscription; no cloud API key in the default path. Requires plugin routing
   changes, making `CloudBackend` opt-in, and reshaping C4. Land an implementation plan first.
2. **Document only (this ADR):** capture the analysis, decide later.
3. **Keep the relay, opt-in + minimize:** leave `CloudBackend` in place but off by default and
   clearly marked metered; rely on maximizing local quality to shrink the cloud slice. Smallest
   change, but still pays metered $ on the residual cloud traffic.

## Open questions for the implementation phase (when/if option 1 is chosen)
- Does OpenClaw expose a per-request **fallback** path (try anvil-local, on a signal use the native
  provider) — or only a single provider selection per request? (Determines whether mid-request
  verify-fail can defer, or whether the decision must be fully upfront.)
- Codex/Claude-Code-direct (non-OpenClaw) users: documented as "needs the gateway, or accepts the
  metered relay." Is that acceptable for the launch audience?
- Should anvil expose a standalone **decision endpoint** (`POST /route` → local|cloud + reason) so
  non-OpenClaw harnesses can integrate the routing brain without the serve path?
