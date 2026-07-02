---
title: "anvil-serving × OpenClaw — buildable integration spec"
date: 2026-06-29
status: buildable-spec
verdict: go-with-caveats
maturity_risk: medium
beachhead: OpenClaw (per docs/QUALITY-GATED-ROUTER.md §8)
method: "5 source-level facet finders over the OpenClaw repo (docs/ + src/) + adversarial verification + synthesis. Workflow wf_ecaa16b5-b96, 11 agents."
---

> **Provenance.** Source-verified against the OpenClaw repo (`src/plugins/hook-before-agent-start.types.ts`,
> `src/plugins/hook-types.ts`, `src/agents/embedded-agent-runner/run.ts`, `docs/plugins/hooks.md`,
> `docs/concepts/model-providers.md`) and adversarially checked. Items not byte-confirmed are tagged
> **UNCONFIRMED**. Companion to [`QUALITY-GATED-ROUTER.md`](QUALITY-GATED-ROUTER.md).

## READ FIRST — verdict, caveats, and one correction

**Verdict: GO, with caveats. Maturity risk: MEDIUM (API churn, not abandonment).** The hook signature
and the custom-provider config are **source-confirmed** and fit anvil's design exactly. Build the
router-side preset support now; validate two live gaps **before** writing/shipping the plugin.

**Environment for validation.** The validate-first gaps below should be confirmed against a live
OpenClaw install. The router serves (`:30000` heavy, `:30001` fast) must be reachable from the
gateway. Confirming the gaps on a real install eliminates a fresh stand-up for each question.

**Correction to record (vs the prior finding).** The earlier research said `before_model_resolve`
fires "per agent turn." The source-level dive refines this: it fires **once per run, above the attempt
loop**, and does *not* re-fire on a `before_agent_finalize` "revise" retry. For a chat-bridge harness
a "run" is plausibly *one user message* (which is exactly the cadence work-class routing needs — the
user's message determines the work-class), but **this must be confirmed live**: that a run maps to a
single user message and doesn't span turns, and that multi-step internal model calls within a run
share one intent (fine for us). The "classify each turn" premise rests on this.

**Two CRITICAL validate-first gaps** (write no build code against them until settled):
1. **Wire `model` value** — does the outbound HTTP request carry the bare id (`planning`) or the full
   ref (`anvil/planning`)? Capture a real outbound request. (anvil should accept **both** to be safe.)
2. **Firing cadence** — confirm `before_model_resolve` fires per user message (see correction above),
   by logging every fire with `ctx.runId`/`ctx.sessionKey` across a multi-turn conversation.

**API-churn risk + mitigation.** OpenClaw is real, MIT, and very active, but **young** (CalVer
`v2026.6.x`, no semver 1.0, multiple releases/week) and the extension surface is mid-refactor —
`before_agent_start` is already "compatibility-only" in favor of the `before_model_resolve` we target,
so our hook could shift on the same cadence. *(The reported star count was extremely high; treat the
exact number as unverified — the load-bearing point is "very active but churning," not the figure.)*
Mitigation is the **focus-not-couple** architecture, and it's genuine: the router core stays
protocol-standard with **zero OpenClaw import**; the OpenClaw piece is a **~50-line, one-hook,
swappable adapter plugin**. If the hook churns, only the adapter changes; if OpenClaw stalls, the
router is unaffected. Guardrails: pin the `pluginApi` compat + OpenClaw release; keep all
OpenClaw-specific code in the plugin package; **verify+fallback lives in the router** (the design
already assumes this — OpenClaw has no response-swap hook, so this is the only correct place).



> Status: buildable spec, rev 2026-06-29. Derived from source-verified facet findings
> (`src/plugins/hook-before-agent-start.types.ts`, `src/plugins/hook-types.ts`,
> `src/agents/embedded-agent-runner/run.ts`, `docs/plugins/hooks.md`,
> `docs/concepts/model-providers.md`, `docs/gateway/config-tools.md`) and anvil's own
> `docs/QUALITY-GATED-ROUTER.md`. Every item not byte-confirmed against OpenClaw source/docs is
> tagged **UNCONFIRMED — validate before relying on in build code**.

## 0. What is CONFIRMED (build against these)

- **Hook signature (source-verified).** `before_model_resolve` event = `{ prompt: string; attachments?: { kind: "image"|"video"|"audio"|"document"|"other"; mimeType?: string }[] }`; result = `{ modelOverride?: string; providerOverride?: string }`. Types live in `src/plugins/hook-before-agent-start.types.ts` (file named for the @deprecated `before_agent_start`, but holds the current `before_model_resolve` types). Handler is `(event, ctx) => result`, `ctx: PluginHookAgentContext`.
- **Fires once per run, above the attempt loop** (`run.ts` L1033 `resolveHookModelSelection`, applied at `setup.ts` L98–103). It does **not** re-fire on a `before_agent_finalize` "revise" retry (the revise path reuses the resolved provider/modelId; `run.ts` L4063–4078).
- **Provider registration.** `models.providers.<id>` accepts `baseUrl`, `apiKey`, `api` (enum `openai-completions | openai-responses | anthropic-messages | google-generative-ai`), `headers`, inline `models[]`. `models.mode: "merge"` (default) ADDS to the built-in catalog. Models are referenced everywhere as the string `"<providerId>/<modelId>"`. Docs say verbatim: self-hosted `/v1/chat/completions` (MLX/vLLM/SGLang) → use `openai-completions`.
- **Preset-as-model.** Any arbitrary string declared as a model `id` in a provider's `models[]` becomes a first-class selectable model `"<providerId>/<modelId>"`.
- **Per-model sampling/thinking knobs.** `agents.defaults.models["anvil/<preset>"].params.chat_template_kwargs` and `.params.extra_body` — covers anvil gotcha #5 (`enable_thinking:false`).
- **Verify primitives (client-side).** `llm_output` (observe-only: `assistantTexts[]`, `usage`), `before_agent_finalize` (decision: `{ action?: "continue"|"revise"|"finalize"; reason?; retry?: { instruction; idempotencyKey?; maxAttempts? } }`, cap `MAX_BEFORE_AGENT_FINALIZE_REVISIONS=3`), `model_call_ended` (transport telemetry). **No** `after_model_response`/response-swap hook.
- **Native failover** (`agents.defaults.model.fallbacks`) triggers on transport-class errors only (auth/429/overloaded/timeout/billing) — **never** on a correctness/quality verdict. **LIVE-CONFIRMED CAVEAT (2026-07-01, see §7 below):** the fallback walk itself fires correctly on anvil's exhaustion-503, but when the failing attempt was resolved via `before_model_resolve`'s `providerOverride`, the fallback attempts also resolve through that same overridden provider — so the native provider is never actually reached. Treat this as a live-confirmed gap, not the "safety net" §1/§4 previously assumed.
- **Gating.** Non-bundled plugins using `before_model_resolve` MUST set `plugins.entries.<id>.hooks.allowConversationAccess=true`. Prompt-mutating hooks additionally need `allowPromptInjection`.
- **Trust/deploy.** MIT (OpenClaw Foundation). Node 22.19+/24, TS ESM. Gateway defaults to loopback; a loopback anvil endpoint needs no TLS and no gateway auth. Plugin install = "running code", gated by `security.installPolicy` + `plugins.allow`/`plugins.deny`; config change requires `openclaw gateway restart`.

## 1. Reference `before_model_resolve` plugin (Tier-0 classify → preset)

The plugin classifies the current turn and emits an anvil preset id. Because the event carries **only `prompt` + attachment metadata** (CONFIRMED — "No session messages are available yet in this phase"), the classifier is a lightweight heuristic over prompt text + attachment kinds, not a full-context judge.

```ts
// index.ts  — package "type":"module", ESM, Node 22.19+
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

// Closed preset enum = anvil's wire vocabulary (must match models[] ids in §2 and the router).
type AnvilPreset = "planning" | "quick-edit" | "review" | "chat" | "long-context";

function classify(prompt: string, attachments?: { kind: string }[]): AnvilPreset {
  const p = prompt.toLowerCase();
  if (attachments?.some(a => a.kind === "image")) return "review"; // multimodal → capable tier
  if (prompt.length > 24_000) return "long-context";
  if (/\b(plan|design|decompose|architect|break (this )?down)\b/.test(p)) return "planning";
  if (/\b(review|critique|audit|find bugs?)\b/.test(p)) return "review";
  if (/\b(fix|edit|rename|tweak|typo|small change)\b/.test(p)) return "quick-edit";
  return "chat"; // safe default; router biases ambiguous → safer/cloud tier
}

export default definePluginEntry({
  id: "anvil-intent-router",
  name: "Anvil intent router",
  register(api) {
    api.on(
      "before_model_resolve",
      (event, _ctx) => ({
        providerOverride: "anvil",
        modelOverride: classify(event.prompt, event.attachments),
      }),
      { priority: 50 /*, timeoutMs: 50 */ }
    );
  },
});
```

**UNCONFIRMED — must validate on a live gateway before shipping:**
- **Firing cadence (load-bearing).** Source structure says once-per-run, and the doc comment says "before session messages load" — confirm empirically that `before_model_resolve` fires **per agent turn** (so per-turn classification is real) and not once per session. The entire "classify each turn" premise hinges on this. Validate by logging every fire with `ctx.runId`/`ctx.sessionKey` across a multi-turn conversation.
- **`registerEntry` arg shape.** Two doc/example forms appear: object form `definePluginEntry({ id, name, register(api){...} })` and callback form `definePluginEntry(async (api) => {...})`. Pin the form against the installed SDK's `plugin-entry` export.
- **`ctx` field availability.** `PluginHookAgentContext` is documented to include `workspaceDir`, `channel`, `contextTokenBudget`, etc., but which fields are populated at the `before_model_resolve` phase is UNCONFIRMED — the classifier above deliberately uses only `event` to stay safe.

**Packaging (CONFIRMED shape, version string UNCONFIRMED):**
```jsonc
// package.json
{ "type": "module",
  "openclaw": { "extensions": ["./index.ts"],
    "compat": { "pluginApi": ">=2026.3.24-beta.2" } } }  // UNCONFIRMED exact floor; before_model_resolve reportedly added ~2026.4.21 — confirm against CHANGELOG/release tags and pin.
// openclaw.plugin.json
{ "id": "anvil-intent-router", "activation": { "onStartup": true } }
```
Install: `openclaw plugins install --link ./local` (dev) or `clawhub:<org>/anvil-intent-router`; then `openclaw gateway restart`; verify `openclaw plugins inspect anvil-intent-router --runtime --json`. **`--link` is required on OpenClaw >=2026.6.11** — that compiled-runtime loader rejects a copy-install (`openclaw plugins install <path>` without the flag) for TypeScript/compiled plugins like this one; only a linked (symlinked) install is accepted.

## 2. OpenClaw provider config recipe (point at anvil-serving)

```jsonc
// ~/.openclaw/openclaw.json (JSON5)
{
  models: {
    mode: "merge",                          // CONFIRMED default; ADDS to built-in catalog
    providers: {
      anvil: {
        baseUrl: "http://127.0.0.1:8000/v1", // anvil-serving OpenAI-compat front door (loopback ⇒ no TLS/auth)
        apiKey: "${ANVIL_API_KEY}",          // env interpolation CONFIRMED; anvil may ignore on loopback
        api: "openai-completions",           // CONFIRMED for self-hosted vLLM/SGLang
        models: [
          { id: "planning",     name: "Anvil · Planning",     reasoning: false, input: ["text"],          contextWindow: 128000, maxTokens: 32000 },
          { id: "quick-edit",   name: "Anvil · Quick Edit",   reasoning: false, input: ["text"],          contextWindow: 32000,  maxTokens: 8192  },
          { id: "review",       name: "Anvil · Review",       reasoning: false, input: ["text","image"],  contextWindow: 128000, maxTokens: 16000 },
          { id: "chat",         name: "Anvil · Chat",         reasoning: false, input: ["text"],          contextWindow: 32000,  maxTokens: 8192  },
          { id: "long-context", name: "Anvil · Long Context", reasoning: false, input: ["text"],          contextWindow: 256000, maxTokens: 16000 }
        ]
      }
    }
  },
  agents: { defaults: {
    // Default slot when the plugin is absent → router's own Tier-0 classifier still applies.
    model: { primary: "anvil/chat" },
    // anvil gotcha #5 (thinking-by-default models) — per-preset:
    models: {
      "anvil/planning":     { params: { chat_template_kwargs: { enable_thinking: false } } },
      "anvil/long-context": { params: { chat_template_kwargs: { enable_thinking: false } } }
    }
  } },
  plugins: { entries: { "anvil-intent-router": { hooks: { allowConversationAccess: true } } } } // CONFIRMED required gate
}
```

## 3. Preset / model-id contract

- **Vocabulary:** closed enum `{ planning, quick-edit, review, chat, long-context }` (matches `docs/QUALITY-GATED-ROUTER.md` §9). Same strings in three places: plugin `modelOverride`, provider `models[].id`, anvil router's accepted `model` names.
- **Selection string** inside OpenClaw is `"anvil/<preset>"` (CONFIRMED ref format).
- **Wire value — UNCONFIRMED (CRITICAL build gap).** It is NOT documented whether the upstream HTTP `model` field receives the bare id (`"planning"`) or the full ref (`"anvil/planning"`). The `openai-completions` convention is the bare id, so **anvil-serving must accept `planning`/`quick-edit`/… as servable model names** — but **capture an actual outbound request** (local echo server or proxy log) to confirm before building the router's model-name parser. To be robust, anvil should accept BOTH the bare preset and the `anvil/<preset>` form. This is the single most important thing to verify hands-on.
- **`/v1/models` discovery:** anvil should serve the preset tokens with human names so they also surface for non-plugin/closed harnesses (Tier-1). Whether OpenClaw auto-imports a custom provider's catalog from `/v1/models` vs requiring the inline `models[]` is UNCONFIRMED — the inline `models[]` above is the safe, CONFIRMED path; treat catalog auto-pull as a bonus to verify.
- **Override resolution — UNCONFIRMED.** Whether `modelOverride` must name a model already in the resolved catalog (i.e. it MUST be pre-registered in `models[]`) or can be an arbitrary opaque preset id is not confirmed. Build defensively: always pre-register every preset in `models[]` (§2).

## 4. Where each stage lives (client plugin vs router)

| Stage | Lives in | Why (source-grounded) |
|---|---|---|
| **Tier-0 classify** | **Client plugin** (`before_model_resolve`), with the **router's own classifier as the floor** | Plugin sees the raw turn prompt and can emit a per-turn preset. Closed harnesses (Claude Code/Codex) lack the hook, so the router must still classify. |
| **Intent → (model, tier, params)** | **Router** | Preset is opaque to OpenClaw; only anvil owns the quality profile + tier mapping. |
| **Verify (cheap structural)** | **Router (primary)**; optional client mirror via `llm_output` | `llm_output` is observe-only; it can feed a client-side verdict but cannot change the served response. Inline verify on the hot path belongs in the router. |
| **Cross-tier quality fallback (within a turn)** | **Router ONLY** | CONFIRMED: `before_model_resolve` fires once per run; `before_agent_finalize` "revise" retries the **same** model (no provider/model field), cap 3; native failover excludes quality verdicts. The client cannot escalate tier mid-turn on a quality miss. |
| **Client-side escalation** | **Client plugin (next-turn only, optional)** | A plugin may store an `llm_output`/`model_call_ended` verdict and bias the NEXT turn's `before_model_resolve` upward. Same-turn `before_agent_finalize` "revise" can only nudge the same model with an instruction string. |

Net: this matches anvil's existing design (`QUALITY-GATED-ROUTER.md` §7) — verify+fallback is a **router** responsibility; the OpenClaw plugin is a thin Tier-0 classifier that pushes per-turn intent to the wire. No design change needed.

## 5. MVP build steps

1. **Router accepts presets (no OpenClaw needed).** Make anvil-serving's OpenAI front door accept `{planning,quick-edit,review,chat,long-context}` (and `anvil/<preset>`) as `model`, map to tier, serve. Add `/v1/models` listing the presets. (anvil M0–M1.)
2. **Use the available OpenClaw install on the gateway** (already installed; no fresh stand-up). Confirm/pin its version (`openclaw --version`), then add the §2 provider block pointing at the router. Smoke-test `openclaw models list` shows `anvil/*`; send a turn with `agents.defaults.model.primary="anvil/chat"`; **capture the outbound request to settle §3 wire-value gap.** (Reproducing elsewhere: `npm i -g openclaw@<pinned stable>` + `openclaw onboard --install-daemon`.)
3. **Reference plugin.** Build §1, `openclaw plugins install --link ./local`, set `allowConversationAccess=true`, restart. Log every `before_model_resolve` fire → **confirm per-turn cadence**. Verify a returned `modelOverride` actually routes the turn to the anvil endpoint with the expected wire model.
4. **Router-side verify+fallback.** Implement cheap structural verify + tier fallback server-side (anvil M2). Optionally add a client `llm_output` observer that logs verdicts for next-turn biasing.
5. **Harden + publish.** Pin `pluginApi` compat once confirmed; publish plugin to ClawHub; document the `security.installPolicy`/`plugins.allow` install path.

## 6. Open questions needing hands-on validation (all UNCONFIRMED)

- Wire `model` field = bare id vs `anvil/<preset>` (step 2 capture). **CRITICAL.**
- `before_model_resolve` per-turn vs per-session firing cadence (step 3 logging). **CRITICAL.**
- Whether `modelOverride` must reference a catalog-registered model (build defensively: always register).
- Exact `pluginApi` compat floor string + the release that introduced `before_model_resolve` (pin from CHANGELOG/tags).
- `definePluginEntry` arg form (object vs callback) in the installed SDK.
- Whether a `before_agent_finalize` "revise" instruction can be parsed by the router to coerce escalation (likely no clean channel — treat router fallback as the real path).
- Plugin runtime config/secret access (`plugins.entries.<id>.config`) and whether plugins are sandboxed (affects shipping-safety claims).
- Timeout key name(s) on the provider (`timeoutSeconds` vs `timeoutMs`) — not byte-confirmed.

## 7. LIVE-CONFIRMED DEFECT (2026-07-01): the anvil-503 native-failover loop

**Symptom (observed live, real OpenClaw agent turn, v0.6.0):** `before_model_resolve` set
`providerOverride:"anvil"` for a local-preferred-class turn (quick-edit/review/chat/long-context —
`plugins/openclaw-anvil-intent-router`'s T008 upfront split). anvil returned 503 (`"no
quality-gated tier is available for this request"` — the keyless-handoff signal, ADR-0001).
OpenClaw's native failover (`agents.defaults.model.fallbacks -> [openai/gpt-5.5,
openai/gpt-5.4-mini]`) DID fire (so the 503-is-a-transport-failure-trigger assumption from
`docs/PLAN-advise-and-defer.md` Phase 1 is confirmed correct) — but **both configured fallback
models also 503'd through the `anvil` provider**, never reaching the native cloud provider. The
agent turn ended in "couldn't generate a response" instead of a graceful cloud handoff.

**Root cause (source-grounded, not guessed).** §0 above is source-confirmed: `before_model_resolve`
"fires once per run, above the attempt loop" (`run.ts` L1033 `resolveHookModelSelection`, applied at
`setup.ts` L98–103). The live symptom is consistent with that resolution — specifically the
`providerOverride` component — being applied for the **whole run's attempt loop**, not just the
first (primary) attempt: the fallback walk over `agents.defaults.model.fallbacks` re-resolves a
model string for each fallback entry, but the *provider* component of that resolution appears to
stay pinned to whatever `before_model_resolve` returned, regardless of the provider named in the
fallback entry itself (`openai/gpt-5.5` still resolved through `anvil`). This would explain why
**both** fallback attempts hit anvil's 503 rather than the native provider.

This is consistent with, and sharpens, the "validate live (currently UNCONFIRMED)" item in
`docs/adr/0001-cloud-cost-and-subscription-auth.md` — the exhaustion-503 DOES trip OpenClaw's
"overloaded" failover category (that part of ADR-0001's mechanism holds), but the *result* of that
failover is not the native provider when a `providerOverride` is in play.

**Scope of the defect:** every turn where the plugin emits `{ providerOverride: "anvil", ... }` —
i.e. every local-preferred-class turn (quick-edit, review, chat, long-context; the large majority of
traffic in the default classify table). It does **not** affect cloud-preferred turns (`planning` by
default): those return `{}` (no override at all), so there is no `providerOverride` to stick, and
OpenClaw's own default/native resolution runs normally.

**No repo-side code fix exists.** The router (`anvil_serving/router/`) is behaving correctly per its
own contract (503 with zero streamed local tokens, C3) — the bug is in how OpenClaw's attempt loop
re-resolves the fallback chain after a `before_model_resolve` override. This repo cannot patch
OpenClaw. Two operator-side mitigations (see `plugins/openclaw-anvil-intent-router/README.md` for
the exact config):

1. **`ANVIL_CLOUD_CLASSES`** — move a work-class whose local tier is known to be flaky/exhausted into
   the cloud-preferred set. Its turns then never touch anvil (no `providerOverride` emitted), so
   there is nothing for the failover walk to inherit. Zero-cost, but drops local-first routing for
   that class.
2. **anvil's own opt-in metered cloud tier** (ADR-0001, `configs/example-with-cloud.toml` +
   `[router].metered_cloud`) — let anvil's `fallback.py` escalate to a bound cloud tier *inside* the
   same `provider="anvil"` request/response. anvil then never returns 503 for the at-risk classes, so
   OpenClaw's (unreliable) native failover is never invoked at all. This is the durable fix, gated by
   the explicit billing opt-in ADR-0001 already requires.

See also `docs/adr/0005-anvil-503-native-failover-unreliable.md` (the ADR record of this finding) and
`docs/OPENCLAW-LIVE-VALIDATION.md` (Gap 4).