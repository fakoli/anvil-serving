// openclaw-anvil-intent-router — the REFERENCE OpenClaw `before_model_resolve`
// intent-router plugin for anvil-serving (T014).
//
// Purpose: classify the current turn (prompt text + attachment kinds) into one
// of anvil's CLOSED presets and emit `{ providerOverride: "anvil", modelOverride:
// "<preset>" }`, so OpenClaw routes the run to the anvil provider's matching preset
// model. It also appends a decision-log line per fire (for the AC1 fixture + live
// assertion).
//
// LIVE-CONFIRMED on OpenClaw 2026.6.6 (Fakoli-Mini, 2026-06-30): a lone
// `modelOverride: "anvil/<preset>"` is resolved as `<defaultProvider>/anvil/<preset>`
// (e.g. `openai/anvil/planning`) → `model_not_found`. The provider MUST be named
// separately via `providerOverride`; the wire then carries the BARE preset
// (`model: "planning"`). See docs/findings/2026-06-30-openclaw-live-validation.md.
//
// REQUIRED GATE: a non-bundled plugin using `before_model_resolve` MUST be
// granted conversation access in ~/.openclaw/openclaw.json:
//   plugins: { entries: { "openclaw-anvil-intent-router": { hooks: { allowConversationAccess: true } } } }
// (This hook does NOT mutate the prompt, so it does NOT need `allowPromptInjection`.)
//
// Focus-not-couple: ALL OpenClaw-specific code lives here, in this swappable
// adapter plugin. The router core (anvil_serving/router/) stays OpenClaw-free
// (AC2). The classify heuristic is the shared ./classify.mjs (single source of
// truth) so the committed decision_log.fixture.jsonl is provably this plugin's
// real output.
//
// Hook types are source-faithful to docs/OPENCLAW-INTEGRATION-SPEC.md §0/§1.

import { appendFileSync } from "node:fs";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { classify, type AnvilPreset } from "./classify.mjs";

// Re-export so the closed preset enum + heuristic are part of this plugin's
// public surface (and importable by tooling such as make-fixture.mjs).
export { classify };
export type { AnvilPreset };

// --- source-faithful hook types (docs/OPENCLAW-INTEGRATION-SPEC.md §0) -------
type Attachment = { kind: "image" | "video" | "audio" | "document" | "other"; mimeType?: string };
type BeforeModelResolveEvent = { prompt: string; attachments?: Attachment[] };
type BeforeModelResolveResult = { modelOverride?: string; providerOverride?: string };

// Decision log: one JSONL line per fire. Default is the gateway CWD; override
// with ANVIL_DECISION_LOG. This is what AC1 asserts against (the synthetic,
// regenerable fixture is decision_log.fixture.jsonl; a live run produces
// decision_log.jsonl — see README.md).
const DECISION_LOG = process.env.ANVIL_DECISION_LOG ?? "./decision_log.jsonl";

export default definePluginEntry({
  id: "openclaw-anvil-intent-router",
  name: "Anvil intent router",
  register(api) {
    api.on(
      "before_model_resolve",
      (event: BeforeModelResolveEvent, ctx): BeforeModelResolveResult => {
        // OUTER never-break guard: a routing plugin must NEVER break a user's
        // run. ANY error escaping this body (a throwing getter / Proxy on
        // `event`/`ctx`, a future field access, etc.) degrades to a no-op
        // override `{}` (OpenClaw keeps its own model resolution). The inner
        // classify + appendFileSync guards stay too — defense in depth.
        try {
          // Coerce ONCE to the exact string classify sees, then derive both the
          // classification AND prompt_chars from it — so a non-string prompt logs
          // its real classified length, not 0.
          const promptText = String(event?.prompt ?? "");
          // classify NEVER throws; default "chat" is the safe floor.
          const preset: AnvilPreset = classify(promptText, event?.attachments);
          // Route to the anvil provider by NAMING IT (providerOverride) and putting
          // the BARE preset in modelOverride. LIVE-CONFIRMED (OpenClaw 2026.6.6):
          // OpenClaw forwards the bare preset on the wire (model="planning"); a lone
          // "anvil/<preset>" in modelOverride is mis-resolved under the default
          // provider. Bare preset satisfies validate.py's WIRE_FORM_RE
          // ^(anvil/)?<preset>$ and the anvil front door accepts it.
          const providerOverride = "anvil";
          const modelOverride = preset;

          const record = {
            ts: new Date().toISOString(),
            runId: String((ctx as { runId?: string })?.runId ?? "unknown-run"),
            sessionKey: String((ctx as { sessionKey?: string })?.sessionKey ?? "unknown-session"),
            source: "openclaw",
            intent: preset,
            providerOverride,
            modelOverride,
            prompt_chars: promptText.length,
          };
          try {
            appendFileSync(DECISION_LOG, JSON.stringify(record) + "\n");
          } catch {
            // NEVER break a run because a logging write failed.
          }

          return { providerOverride, modelOverride };
        } catch {
          // Anything unexpected -> no override; let OpenClaw resolve normally.
          return {};
        }
      },
      { priority: 50 /*, timeoutMs: 50 */ },
    );
  },
});
