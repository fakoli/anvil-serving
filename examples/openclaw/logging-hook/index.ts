// anvil-fire-logger — a MINIMAL, LOGGING-ONLY OpenClaw `before_model_resolve` plugin.
//
// Purpose (anvil-serving T013): instrument the live Fakoli-Mini OpenClaw gateway
// to PROVE the firing cadence — that `before_model_resolve` fires once per user
// message. It records every fire to a JSONL log and returns `{}`; it changes
// NOTHING about routing. Run a multi-turn conversation, then point
// `examples/openclaw/validate.py --assert-fire-cadence <log>` at the produced log.
//
// REQUIRED GATE: a non-bundled plugin using `before_model_resolve` MUST be granted
// conversation access in ~/.openclaw/openclaw.json:
//   plugins: { entries: { "anvil-fire-logger": { hooks: { allowConversationAccess: true } } } }
// (This hook does NOT mutate the prompt, so it does NOT need `allowPromptInjection`.)
//
// Hook types are source-faithful to docs/OPENCLAW-INTEGRATION-SPEC.md §0.

import { appendFileSync } from "node:fs";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

type Attachment = { kind: "image" | "video" | "audio" | "document" | "other"; mimeType?: string };
type BeforeModelResolveEvent = { prompt: string; attachments?: Attachment[] };
type BeforeModelResolveResult = { modelOverride?: string; providerOverride?: string };

const LOG_PATH = process.env.ANVIL_FIRE_LOG ?? "./hook-fire-log.jsonl";

// Per-session runId -> user-message ordinal. A NEW runId in a session is a new
// user message (`before_model_resolve` fires once per run, above the attempt
// loop); a REPEATED runId reuses the ordinal, so the validator sees >1 fire for
// that message and flags the cadence. This is exactly what we are proving.
const seen = new Map<string, Map<string, number>>();
function userMessageIndex(sessionKey: string, runId: string): number {
  let runs = seen.get(sessionKey);
  if (!runs) { runs = new Map(); seen.set(sessionKey, runs); }
  if (!runs.has(runId)) runs.set(runId, runs.size);
  return runs.get(runId)!;
}

export default definePluginEntry({
  id: "anvil-fire-logger",
  name: "Anvil fire-cadence logger (logging-only)",
  register(api) {
    api.on(
      "before_model_resolve",
      (event: BeforeModelResolveEvent, ctx): BeforeModelResolveResult => {
        const sessionKey = String((ctx as { sessionKey?: string })?.sessionKey ?? "unknown-session");
        const runId = String((ctx as { runId?: string })?.runId ?? "unknown-run");
        const record = {
          ts: new Date().toISOString(),
          runId,
          sessionKey,
          userMessageIndex: userMessageIndex(sessionKey, runId),
          prompt_chars: event.prompt?.length ?? 0,
          modelOverride: null, // logging-ONLY: record cadence, never route
        };
        try {
          appendFileSync(LOG_PATH, JSON.stringify(record) + "\n");
        } catch {
          /* never break a run because logging failed */
        }
        return {}; // no override — this plugin only records the firing cadence
      },
      { priority: 50 },
    );
  },
});
