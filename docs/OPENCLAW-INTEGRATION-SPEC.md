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
> `docs/concepts/model-providers.md`) and adversarially checked. This document preserves historical
> validation context, but the current operational contract is the checked-in plugin, ADR-0013,
> ADR-0014, and Operator Playbooks. Companion to [`QUALITY-GATED-ROUTER.md`](QUALITY-GATED-ROUTER.md).

## READ FIRST — verdict, caveats, and one correction

**Current implementation status (2026-07-05):** the OpenClaw reference plugin, router preset
support, harness sync, MCP control plane, and split-host controller transport now exist in this
repo. Treat this document as the source-verified integration spec and historical validation record;
for the current operational contract, also read
[ADR-0013](adr/0013-openclaw-layers-and-mcp-control-plane.md),
[ADR-0014](adr/0014-tailnet-controller-transport.md), and
[Operator Playbooks](OPERATOR-PLAYBOOKS.md).

**Verdict: GO, with caveats. Maturity risk: MEDIUM (API churn, not abandonment).** The hook signature
and the custom-provider config are **source-confirmed** and fit anvil's design. The router-side
preset support and plugin were implemented with the caveats below preserved as validation and
operational constraints.

**Environment for validation.** The original validate-first gaps were confirmed against a live
OpenClaw install and are retained below as provenance. For future re-validation, the router serves
must be reachable from the gateway and the run should capture model wire values, hook cadence, and
fallback behavior.

**Correction to record (vs the prior finding).** The earlier research said `before_model_resolve`
fires "per agent turn." The source-level dive refines this: it fires **once per run, above the attempt
loop**, and does *not* re-fire on a `before_agent_finalize` "revise" retry. For a chat-bridge harness
a "run" is plausibly *one user message* (which is exactly the cadence work-class routing needs — the
user's message determines the work-class). The live-validation record and ADR-0005 capture the
important operational consequence: the resolved provider can stick through OpenClaw's native
fallback walk, so router-side fallback remains load-bearing.

**Original validate-first gaps** are settled and retained only as provenance; see
ADR-0005 and the plugin README (the dated live-validation runbook now lives in the private
`fakoli/anvil-serving-notes` repo). The live wire form is
`providerOverride:"anvil"` plus a bare preset `modelOverride`, and the hook fires once per run above
OpenClaw's attempt loop.

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



> Status: buildable spec, rev 2026-06-29, with implementation notes through 2026-07-05. Derived
> from source-verified facet findings and anvil's own `docs/QUALITY-GATED-ROUTER.md`. Historical
> open questions remain below only when they are still genuinely unresolved.

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

// Closed plugin-emitted preset enum = the automatic OpenClaw intent vocabulary.
// The router may expose additional presets such as chat-fast for other surfaces.
type AnvilPreset = "planning" | "quick-edit" | "review" | "chat" | "long-context";
const NATIVE = { providerOverride: "anthropic", modelOverride: "claude-sonnet-4-5" };

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
  id: "openclaw-anvil-intent-router",   // MUST match the packaged plugin id + the plugins.entries key below
  name: "Anvil intent router",
  register(api) {
    api.on(
      "before_model_resolve",
      (event, _ctx) => {
        const preset = classify(event.prompt, event.attachments);
        // Cloud-preferred presets, currently planning by default, explicitly
        // route to native instead of entering anvil's providerOverride trap.
        if (preset === "planning") return NATIVE;
        return { providerOverride: "anvil", modelOverride: preset };
      },
      { priority: 50 /*, timeoutMs: 50 */ }
    );
  },
});
```

The checked-in plugin adds env/plugin-config driven `cloudClasses`, optional authoritative
`/v1/route`, route auth by env-var name, and decision logging. Use
`plugins/openclaw-anvil-intent-router/` as the reference implementation rather than copying this
abridged snippet.

**Packaging (confirmed shape; keep the plugin API floor aligned with the checked-in plugin and
re-run live validation on gateway upgrades):**
```jsonc
// package.json
{ "type": "module",
  "openclaw": { "extensions": ["./index.ts"],
    "compat": { "pluginApi": ">=2026.4.21" } } }
// openclaw.plugin.json
{ "id": "openclaw-anvil-intent-router",
  "activation": { "onStartup": true },
  "configSchema": { "type": "object", "additionalProperties": false,
    "properties": {
      "cloudClasses": { "type": "array", "items": { "type": "string" } },
      "routeEndpoint": { "type": "string" },
      "routeTimeoutMs": { "type": "integer", "minimum": 1, "maximum": 5000 },
      "routeAuthEnv": { "type": "string" },
      "nativeProvider": { "type": "string" },
      "nativeModel": { "type": "string" } } } }
```
Install: `openclaw plugins install --link ./local` (dev) or `clawhub:<org>/openclaw-anvil-intent-router`; then `openclaw gateway restart`; verify `openclaw plugins inspect openclaw-anvil-intent-router --runtime --json`. **`--link` is required on OpenClaw >=2026.6.11** — that compiled-runtime loader rejects a copy-install (`openclaw plugins install <path>` without the flag) for TypeScript/compiled plugins like this one; only a linked (symlinked) install is accepted.

## 2. OpenClaw provider config recipe (point at anvil-serving)

> **`contextWindow` MUST match the real routed tier's window — see the
> live-confirmed failure mode below the recipe before changing these values.**

```jsonc
// ~/.openclaw/openclaw.json (JSON5)
{
  models: {
    mode: "merge",                          // CONFIRMED default; ADDS to built-in catalog
    providers: {
      anvil: {
        baseUrl: "http://anvil-gpu.tailnet.example:8000/v1", // split-host gateway -> router host
        // Use http://127.0.0.1:8000/v1 only when OpenClaw and anvil-serving are same-host.
        apiKey: "${ANVIL_ROUTER_TOKEN}",     // env interpolation; no literal secrets in config
        api: "openai-completions",           // CONFIRMED for self-hosted vLLM/SGLang
        models: [
          { id: "planning",     name: "Anvil · Planning",     reasoning: true, input: ["text"],          contextWindow: 131072, maxTokens: 32000 },
          { id: "quick-edit",   name: "Anvil · Quick Edit",   reasoning: true, input: ["text"],          contextWindow: 131072, maxTokens: 8192  },
          { id: "review",       name: "Anvil · Review",       reasoning: true, input: ["text","image"],  contextWindow: 131072, maxTokens: 16000 },
          { id: "chat",         name: "Anvil · Chat",         reasoning: true, input: ["text"],          contextWindow: 131072, maxTokens: 8192  },
          { id: "chat-fast",    name: "Anvil · Chat Fast",    reasoning: true, input: ["text"],          contextWindow: 131072, maxTokens: 8192  },
          { id: "long-context", name: "Anvil · Long Context", reasoning: true, input: ["text"],          contextWindow: 131072, maxTokens: 16000 }
        ]
      }
    }
  },
  agents: { defaults: {
    // Default slot when the plugin is absent → router's own Tier-0 classifier still applies.
    model: { primary: "anvil/chat" },
    // Dropdown allowlist. Keep entries present, but leave params empty:
    // the router owns per-tier reasoning/thinking defaults now.
    models: {
      "anvil/planning": {},
      "anvil/quick-edit": {},
      "anvil/review": {},
      "anvil/chat": {},
      "anvil/chat-fast": {},
      "anvil/long-context": {}
    }
  } },
  plugins: { entries: { "openclaw-anvil-intent-router": {
    hooks: { allowConversationAccess: true }
  } } } // key = packaged plugin id (CONFIRMED required gate)
}
```

**Why every preset above declares `131072` (v0.7.1 — LIVE-CONFIRMED FAILURE MODE,
2026-07-02).** `contextWindow` must be declared as the **LARGEST context window among
the tiers a preset can actually route to**, not the smallest/typical one — for the
reference deploy (`configs/example.toml`) that is `heavy-local`'s `context_limit =
131072`, since every preset's candidate pool either routes to `heavy-local` directly
(`review`, `planning`, `long-context`) or can escalate to it as a fallback
(`chat`, `quick-edit: [fast-local, heavy-local]`). An earlier version of this recipe
declared `chat`/`quick-edit` at `32000` (matching only `fast-local`'s window) — that
understated value caused a live incident:

1. OpenClaw computes `max_completion_tokens = declared contextWindow − actual prompt
   tokens`, **clamped to a floor of 1** — it does **not** reject an oversized prompt.
2. A real conversation's prompt grew past the understated `32000` (an actual ~43k-token
   payload vs. the declared 32768-class window). Every subsequent turn's
   `max_completion_tokens` computed negative, floored to **1**.
3. The local model correctly honored the 1-token cap and returned exactly one token with
   `finish_reason: "length"` — genuinely correct, caller-capped behavior.
4. **Pre-v0.7.1**, anvil's `NotTruncated` verifier had no way to distinguish "the model
   obeyed an explicit caller cap" from "an unexpected truncation" — it hard-failed every
   such response on every tier, producing a 503 exhaustion on **every turn**. Worse, the
   repeated verify-failures tripped the circuit breaker (`fallback.CircuitBreaker`),
   blacking out the whole work-class (not just the offending turn) for the cooldown
   window — collateral damage to otherwise-healthy traffic.
5. The operator-visible 503 error text ("gated candidates [...] are unbound. Configure
   that tier's credentials/endpoint...") pointed at credentials/reachability, which was
   **wrong** — the tiers were bound and reachable the whole time — costing real
   debugging time twice before the `contextWindow` misdeclaration was found.

**v0.7.1 fixes the router side** (a caller-capped `length`/`max_tokens` stop with
non-empty content now PASSES `NotTruncated` — see `anvil_serving/router/verify.py` — so
it no longer 503s or trips the breaker) **but the `contextWindow` values above are still
the correct fix on the OpenClaw side**: declaring the true largest routed window means
OpenClaw computes a realistic completion budget in the first place, rather than relying
on the router to absorb an artificially starved budget every turn. Any harness that
computes its own completion-token budget from a declared context window has the same
failure shape — a 1-token (or otherwise pathologically small) "availability probe" is
not exotic; it is a natural consequence of *any* provider-side context-window
misdeclaration once the real prompt exceeds it.

## 2a. OpenClaw realtime voice config (point Talk at Anvil Voice)

The text-router provider above is separate from OpenClaw's realtime voice
provider registry. For speech-to-speech, run `anvil-serving voice run` on the
voice host and configure OpenClaw Talk to use provider id `anvil` over the
Gateway relay:

```jsonc
// ~/.openclaw/openclaw.json (JSON5)
{
  talk: {
    consultModel: "anvil/chat-fast",
    consultThinkingLevel: "off",
    consultBootstrapContextMode: "lightweight",
    realtime: {
      mode: "realtime",
      transport: "gateway-relay",
      brain: "agent-consult",
      consultRouting: "force-agent-consult",
      provider: "anvil",
      providers: {
        anvil: {
          realtimeUrl: "ws://127.0.0.1:8765/v1/realtime",
          model: "chat-fast",
          silenceDurationMs: 200
        }
      }
    }
  }
}
```

The matching anvil-serving side is `examples/voice/openclaw-anvil-voice.toml`.
In the reference topology, Fakoli Mini runs OpenClaw Gateway plus Anvil Voice
Realtime/proxy only; its 16 GB RAM is reserved for OpenClaw, Claude Code, and
Codex. STT/TTS/LLM model serves live off Mini. Use `dark-audio` for Dark-host
STT/TTS through `anvil-serving voice bridge`, or select
`mini-dark-audio-proxy` after verifying the Mini-local proxy ports forward to
Dark audio. `mini-audio` remains an explicit optional same-host/local-audio
mode, not the normal Talk or benchmark path.

For candidate LLM A/B, keep audio selection in `--profile` and apply a
candidate overlay to `voice run` or `voice benchmark`:

```bash
anvil-serving voice run \
  --config examples/voice/openclaw-anvil-voice.toml \
  --profile dark-audio \
  --candidate-overlay examples/voice/candidates/qwen3-32b-nvfp4.toml \
  --candidate qwen3-32b-nvfp4
```

`harness sync openclaw --voice` renders this Talk block together with the
normal anvil model provider config. When the router config includes
`chat-fast`, the rendered Talk provider model defaults to `chat-fast`, and
`talk.consultModel` defaults to `anvil/chat-fast`, so spoken turns and forced
Talk agent consults use the fast preset without persisting a session model
change:

```bash
anvil-serving harness sync openclaw \
  --config configs/example.toml \
  --base-url http://100.87.34.66:8000/v1 \
  --voice \
  --voice-realtime-url ws://127.0.0.1:8765/v1/realtime \
  --voice-consult-model anvil/chat-fast \
  --voice-consult-thinking-level off \
  --voice-consult-bootstrap-context-mode lightweight \
  --out ./openclaw.anvil.json
```

Use `--voice-consult-model anvil/chat` when an operator intentionally wants to
switch forced voice consults back to the standard chat preset.
`--voice-consult-thinking-level` defaults to `off` for lower spoken-turn
latency and replaces stale `talk.consultThinkingLevel` values during sync.
`--voice-consult-bootstrap-context-mode` defaults to `lightweight` so forced
Talk consults skip workspace bootstrap-file injection; set it to `full` only
when a voice workflow needs the normal OpenClaw agent bootstrap context.

Loopback Anvil Voice can omit a realtime bearer token. A private/tailnet
Realtime bind must set `voice.realtime_token_env` in the voice manifest and
pass `--voice-api-key-env ANVIL_VOICE_REALTIME_TOKEN` during sync, so OpenClaw
stores only an env-backed SecretRef.

## 3. Preset / model-id contract

- **Vocabulary:** the OpenClaw intent plugin emits `{ planning, quick-edit, review, chat, long-context }`.
  The router can expose additional presets, such as `chat-fast`, for non-OpenClaw or manually selected
  surfaces. Same emitted strings appear in three places: plugin `modelOverride`, provider `models[].id`,
  and anvil router accepted `model` names.
- **Selection string** inside OpenClaw is `"anvil/<preset>"` (CONFIRMED ref format).
- **Wire value:** live validation confirmed OpenClaw forwards the bare model id (`"planning"`, not
  `"anvil/planning"`) when the plugin returns `providerOverride:"anvil"` with a bare `modelOverride`.
  anvil-serving accepts both bare presets and the `anvil/<preset>` form for robustness.
- **`/v1/models` discovery:** anvil serves preset tokens with human names for non-plugin/closed
  harnesses. Inline provider `models[]` remains the safe OpenClaw path; catalog auto-import should be
  treated as optional convenience, not a dependency.
- **Override resolution:** pre-register every emitted preset in `models[]`; that is the supported
  operational path.

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

> **Status:** these steps describe the original build path. The current source includes the
> reference plugin, harness sync verbs, stdio MCP server, and tailnet controller transport. Use
> [Operator Playbooks](OPERATOR-PLAYBOOKS.md) for day-to-day operations.

1. **Router accepts presets (no OpenClaw needed).** Done: anvil-serving's OpenAI front door accepts the
   configured router presets and `anvil/<preset>` as `model`, maps them to tiers, serves them, and lists
   them through `/v1/models`.
2. **Use the available OpenClaw install on the gateway.** Done for the original wire/cadence gaps; future
   gateway upgrades should re-run the live-validation playbook before changing the plugin contract.
3. **Reference plugin.** Done: install with `openclaw plugins install --link`, set
   `allowConversationAccess=true`, and restart. The current plugin also supports cloud-preferred preset config,
   optional `/v1/route`, and route auth by env-var name.
4. **Router-side verify+fallback.** Implement cheap structural verify + tier fallback server-side (anvil M2). Optionally add a client `llm_output` observer that logs verdicts for next-turn biasing.
5. **Harden + publish.** Keep `pluginApi` compat aligned with the checked-in plugin; publish plugin to ClawHub; document the `security.installPolicy`/`plugins.allow` install path.

## 6. Remaining OpenClaw upgrade questions

- Whether future OpenClaw releases require raising the checked-in `pluginApi` compat floor.
- Whether future OpenClaw releases change the object-form `definePluginEntry` contract.
- Whether provider timeout key names are stable across OpenClaw releases.
- Whether future plugin runtime sandboxing changes env-var access for
  `ANVIL_CLOUD_CLASSES`, `ANVIL_ROUTE_ENDPOINT`, `ANVIL_ROUTE_TIMEOUT_MS`,
  `ANVIL_ROUTE_AUTH_ENV`, and `ANVIL_DECISION_LOG`.

## 7. LIVE-CONFIRMED DEFECT (2026-07-01): the anvil-503 native-failover loop

**Symptom (observed live, real OpenClaw agent turn, v0.6.0):** `before_model_resolve` set
`providerOverride:"anvil"` for a local-preferred preset turn (quick-edit/review/chat/long-context —
`plugins/openclaw-anvil-intent-router`'s T008 upfront split). anvil returned 503 (`"no
quality-gated tier is available for this request"` — the keyless-handoff signal, ADR-0001).
OpenClaw's native failover (`agents.defaults.model.fallbacks -> [openai/gpt-5.5,
openai/gpt-5.4-mini]`) DID fire (so the 503-is-a-transport-failure-trigger assumption from the ADR-0001
advise-and-defer plan is confirmed correct) — but **both configured fallback
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

This is consistent with, and sharpens, the previously open live-validation item in
`docs/adr/0001-cloud-cost-and-subscription-auth.md` — the exhaustion-503 DOES trip OpenClaw's
"overloaded" failover category (that part of ADR-0001's mechanism holds), but the *result* of that
failover is not the native provider when a `providerOverride` is in play.

**Scope of the defect:** every turn where the plugin emits `{ providerOverride: "anvil", ... }` —
i.e. every local-preferred preset turn (quick-edit, review, chat, long-context; the large majority of
traffic in the default classify table). It does **not** affect cloud-preferred turns (`planning` by
default): those now route directly to the configured native provider/model and never touch anvil.

**No repo-side code fix exists.** The router (`anvil_serving/router/`) is behaving correctly per its
own contract (503 with zero streamed local tokens, C3) — the bug is in how OpenClaw's attempt loop
re-resolves the fallback chain after a `before_model_resolve` override. This repo cannot patch
OpenClaw. Two operator-side mitigations (see `plugins/openclaw-anvil-intent-router/README.md` for
the exact config):

1. **`ANVIL_CLOUD_CLASSES`** — move a preset whose local tier is known to be flaky/exhausted into
   the cloud-preferred set. Its turns then never touch anvil (no `providerOverride:"anvil"` emitted), so
   there is nothing for the failover walk to inherit. Zero-cost, but drops local-first routing for
   that class.
2. **anvil's own opt-in metered cloud tier** (ADR-0001, `configs/example-with-cloud.toml` +
   `[router].metered_cloud`) — let anvil's `fallback.py` escalate to a bound cloud tier *inside* the
   same `provider="anvil"` request/response. anvil then never returns 503 for the at-risk classes, so
   OpenClaw's (unreliable) native failover is never invoked at all. This is the durable fix, gated by
   the explicit billing opt-in ADR-0001 already requires.

See also `docs/adr/0005-anvil-503-native-failover-unreliable.md` (the ADR record of this finding)
and `docs/findings/2026-07-04-openclaw-keyless-failover.md` (the dated evidence snapshot).
