// openclaw-anvil-intent-router — upfront routing split (advise-and-defer:T008)
//
// Purpose (T008): classify the current turn client-side and route UPFRONT:
//   - cloud-preferred presets (e.g. `planning`) → explicit native provider/model.
//     This avoids a wasted anvil round-trip for classes eval-proven to work better
//     on the cloud subscription tier.
//   - local presets (quick-edit, review, chat, long-context) → anvil.
//     Emits { providerOverride: "anvil", modelOverride: "<bare preset>" }.
//
// T008 is an OPTIMISATION (no unnecessary anvil contact for eval-proven-cloud
// classes) — it does not itself change the keyless-503 handoff design.
//
// KNOWN DEFECT (anvil-503 native-failover loop — LIVE-CONFIRMED 2026-07-01):
// The keyless-503 -> `agents.defaults.model.fallbacks` handoff is NOT reliable
// for any turn where this plugin emitted `providerOverride:"anvil"` (i.e. every
// local-preferred preset turn): OpenClaw resolves the hook's override once,
// above the attempt loop, and that resolution appears to stick across the
// native-failover walk too, so the configured fallback models also resolve
// through the `anvil` provider and 503 again instead of reaching the native
// cloud provider. Cloud-preferred presets avoid that path by routing directly to
// the configured native provider/model. See route.mjs's module docstring,
// docs/OPENCLAW-INTEGRATION-SPEC.md, and
// docs/adr/0005-anvil-503-native-failover-unreliable.md for the root cause and
// the operator-side mitigations (`ANVIL_CLOUD_CLASSES`, anvil's opt-in metered
// cloud tier).
//
// CLOUD-CLASS SET:
//   Default: {"planning"} (eval-proven cloud-preferred, T005 bake-off).
//   Extend via ANVIL_CLOUD_CLASSES env var (comma-separated preset names), or
//   api.pluginConfig.cloudClasses when the env var is unset.
//
// NATIVE ROUTE:
//   Default: { providerOverride:"anthropic", modelOverride:"claude-sonnet-4-5" }.
//   Override with ANVIL_NATIVE_PROVIDER / ANVIL_NATIVE_MODEL or plugin config.
//
// OPTIONAL AUTHORITATIVE MODE:
//   Set ANVIL_ROUTE_ENDPOINT (e.g. "http://127.0.0.1:8000/v1/route") to call
//   anvil's POST /v1/route (T007) as the authoritative tier decision. If the env
//   var is unset, api.pluginConfig.routeEndpoint is used.
//   Falls back to client-side classify on any error.  Default: client-side only.
//
// WIRE FORM (LIVE-CONFIRMED OpenClaw 2026.6.6, 2026-06-30):
//   { providerOverride: "anvil", modelOverride: "<bare preset>" }
//   A lone `modelOverride: "anvil/<preset>"` is mis-resolved → model_not_found.
//
// REQUIRED GATE: `plugins.entries.openclaw-anvil-intent-router.hooks.allowConversationAccess: true`
// This hook does NOT mutate the prompt, so it does NOT need `allowPromptInjection`.
//
// FOCUS, NOT COUPLE: all OpenClaw-specific code lives here.
// The router core (anvil_serving/router/) is OpenClaw-free (AC2).

import { appendFileSync } from "node:fs";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import { classify, type AnvilPreset } from "./classify.mjs";
import {
  getCloudClasses,
  getNativeRoute,
  getRouteEndpoint,
  getRouteTimeoutMs,
  makeRoutingDecision,
  fetchAnvilTier,
  resolveRouteAuthToken,
} from "./route.mjs";

// Re-export so the closed preset enum + heuristic are part of this plugin's
// public surface (importable by tooling such as make-fixture.mjs).
export { classify };
export type { AnvilPreset };

// --- source-faithful hook types (docs/OPENCLAW-INTEGRATION-SPEC.md §0) -------
type Attachment = { kind: "image" | "video" | "audio" | "document" | "other"; mimeType?: string };
type BeforeModelResolveEvent = { prompt: string; attachments?: Attachment[] };
type BeforeModelResolveResult = { modelOverride?: string; providerOverride?: string };
type OpenClawAnvilPluginConfig = {
  cloudClasses?: unknown;
  routeEndpoint?: unknown;
  routeTimeoutMs?: unknown;
  routeAuthEnv?: unknown;
  nativeProvider?: unknown;
  nativeModel?: unknown;
};

// Decision log: one JSONL line per fire. Default is the gateway CWD; override
// with ANVIL_DECISION_LOG. This is what AC1 asserts against (the synthetic,
// regenerable fixture is decision_log.fixture.jsonl; a live run produces
// decision_log.jsonl — see README.md).
const DECISION_LOG = process.env.ANVIL_DECISION_LOG ?? "./decision_log.jsonl";

function getPluginConfig(api: unknown): OpenClawAnvilPluginConfig | undefined {
  try {
    const config = (api as { pluginConfig?: unknown })?.pluginConfig;
    return config && typeof config === "object"
      ? config as OpenClawAnvilPluginConfig
      : undefined;
  } catch {
    return undefined;
  }
}

export default definePluginEntry({
  id: "openclaw-anvil-intent-router",
  name: "Anvil intent router",
  register(api) {
    api.on(
      "before_model_resolve",
      async (event: BeforeModelResolveEvent, ctx): Promise<BeforeModelResolveResult> => {
        // OUTER never-break guard: a routing plugin MUST NEVER break a user's
        // run.  ANY error escaping this body degrades to a no-op override {}
        // (OpenClaw keeps its own model resolution).
        try {
          // Coerce ONCE to the exact string classify sees.
          const promptText = String(event?.prompt ?? "");
          // classify NEVER throws; default "chat" is the safe floor.
          const preset: AnvilPreset = classify(promptText, event?.attachments);

          // ── ROUTING SPLIT (T008) ─────────────────────────────────────────
          //
          // Two paths:
          //   A. AUTHORITATIVE (opt-in): call POST /v1/route (T007) when
          //      ANVIL_ROUTE_ENDPOINT is set.  Uses the router's full quality
          //      profile; adds one controller round-trip.  Falls back to B on
          //      any non-route error/timeout.
          //   B. CLIENT-SIDE (default): fast, zero-round-trip heuristic.
          //      Uses DEFAULT_CLOUD_CLASSES (+ env/plugin config override).
          //
          let routeOverride: BeforeModelResolveResult;
          let authoritative = false;
          let routingSource = "client-side";

          const pluginConfig = getPluginConfig(api);
          const nativeRoute = getNativeRoute(pluginConfig);
          const routeEndpoint = getRouteEndpoint(pluginConfig);
          const routeTimeoutMs = getRouteTimeoutMs(pluginConfig);
          if (routeEndpoint) {
            // Path A: authoritative POST /v1/route.
            const tier = await fetchAnvilTier(
              promptText,
              event?.attachments as Array<{ kind: string }> | undefined,
              routeEndpoint,
              { timeoutMs: routeTimeoutMs, authToken: resolveRouteAuthToken(pluginConfig), workClass: preset },
            );
            if (tier === "cloud") {
              routeOverride = nativeRoute; // native provider, no anvil contact
              authoritative = true;
              routingSource = "anvil-route";
            } else if (tier === "local") {
              routeOverride = { providerOverride: "anvil", modelOverride: preset };
              authoritative = true;
              routingSource = "anvil-route";
            } else {
              // /v1/route unreachable / timed out / unexpected response →
              // fall back to client-side classify (no run breakage).
              routeOverride = makeRoutingDecision(preset, getCloudClasses(pluginConfig), nativeRoute);
              routingSource = "client-side-fallback";
            }
          } else {
            // Path B: fast client-side classify (default).
            routeOverride = makeRoutingDecision(preset, getCloudClasses(pluginConfig), nativeRoute);
          }

          // "anvil" if routed to anvil; "native" if left to native provider.
          const destination = routeOverride.providerOverride === "anvil"
            ? "anvil"
            : "native";

          const record = {
            ts: new Date().toISOString(),
            runId: String((ctx as { runId?: string })?.runId ?? "unknown-run"),
            sessionKey: String((ctx as { sessionKey?: string })?.sessionKey ?? "unknown-session"),
            source: "openclaw",
            intent: preset,
            destination,
            providerOverride: routeOverride.providerOverride ?? null,
            modelOverride: routeOverride.modelOverride ?? null,
            authoritative,
            routeEndpointConfigured: Boolean(routeEndpoint),
            routingSource,
            prompt_chars: promptText.length,
          };
          try {
            appendFileSync(DECISION_LOG, JSON.stringify(record) + "\n");
          } catch {
            // NEVER break a run because a logging write failed.
          }

          return routeOverride;
        } catch {
          // Anything unexpected -> no override; let OpenClaw resolve normally.
          return {};
        }
      },
      { priority: 50 /*, timeoutMs: 50 */ },
    );
  },
});
