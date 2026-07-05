// route.mjs — routing decision logic for openclaw-anvil-intent-router (T008).
//
// PURPOSE (advise-and-defer:T008 — upfront routing split):
//   Classify the turn client-side and emit either:
//     - {} (no override)  → native provider  for cloud-preferred work-classes
//     - { providerOverride:"anvil", modelOverride:"<preset>" } → anvil for local work-classes
//
//   This avoids a wasted anvil round-trip for classes (e.g. `planning`) that are
//   eval-proven to work better on cloud models.
//
// KNOWN DEFECT (anvil-503 native-failover loop — LIVE-CONFIRMED 2026-07-01):
//   The M0 design assumed `agents.defaults.model.fallbacks` was a safety net for
//   ANY turn that reaches anvil and exhausts (returns 503).  Live E2E testing
//   against a real OpenClaw gateway falsified that for turns where THIS plugin
//   emitted `providerOverride:"anvil"` (i.e. every local-preferred-class turn —
//   quick-edit/review/chat/long-context, the majority of traffic): OpenClaw
//   resolves `before_model_resolve`'s override ONCE, "above the attempt loop"
//   (source-confirmed, see docs/OPENCLAW-INTEGRATION-SPEC.md §0), and that
//   resolution appears to stick for the WHOLE run, including the native-failover
//   walk over `agents.defaults.model.fallbacks` — so a 503 from anvil is
//   followed by fallback attempts that ALSO resolve through the `anvil`
//   provider (and 503 again), never reaching the native cloud provider.
//   The safety net IS reliable for cloud-preferred classes (this function
//   returns `{}` — no override is ever set, so there is nothing to stick).
//   Root cause + operator workaround: docs/OPENCLAW-INTEGRATION-SPEC.md
//   ("anvil-503 native-failover loop") and docs/adr/0005-anvil-503-native-failover-unreliable.md.
//   Practical mitigations (no OpenClaw-side fix available from this repo):
//     1. Add a work-class whose local tier is known to be flaky/exhausted to
//        `ANVIL_CLOUD_CLASSES` so this plugin never emits a `providerOverride`
//        for it (sidesteps the stickiness entirely — the turn never touches
//        anvil, so there's nothing for the failover walk to inherit).
//     2. Enable anvil's own opt-in metered cloud tier (ADR-0001,
//        `configs/example-with-cloud.toml`) and list the at-risk work-classes in
//        `[router].metered_cloud`, so anvil's `fallback.py` escalates to a bound
//        cloud tier INSIDE the same `provider="anvil"` response — anvil never
//        returns 503 for those classes, so OpenClaw's failover is never invoked.
//
// CLOUD-CLASS SET (DEFAULT_CLOUD_CLASSES):
//   "planning" — the only eval-proven cloud-preferred preset (T005 bake-off finding:
//   local 35B-A3B MoE is measurably weaker on multi-step decomposition than the
//   cloud subscription tier).  All other presets (quick-edit, review, chat,
//   long-context) default to local-preferred (route to anvil).
//
//   Operators can extend the set via ANVIL_CLOUD_CLASSES (comma-separated preset
//   names) or, when that env var is unset, via api.pluginConfig.cloudClasses.
//   See getCloudClasses() below.
//
// OPTIONAL AUTHORITATIVE MODE (ANVIL_ROUTE_ENDPOINT / routeEndpoint):
//   When ANVIL_ROUTE_ENDPOINT is set, or when it is unset and
//   api.pluginConfig.routeEndpoint is set (e.g. "http://127.0.0.1:8000/v1/route"),
//   the plugin calls anvil's POST /v1/route (T007) as the AUTHORITATIVE decision:
//     + Uses the router's full quality profile + config; catches edge cases the
//       keyword heuristic misses.
//     - Adds one loopback round-trip (~1-5ms; up to timeoutMs if anvil is slow).
//     - Falls back to client-side classify if anvil is unreachable/timed out.
//   Leave ANVIL_ROUTE_ENDPOINT unset (the default) to use the fast, zero-round-trip
//   client-side path.
//
// MODULE CONTRACT:
//   Pure ESM, zero external imports, no OpenClaw dependency.  Testable with
//   `node --test test.mjs` without a running gateway.
//   Imported by `index.ts` (the plugin hook) and `test.mjs`.

import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";

// ---------------------------------------------------------------------------
// Cloud-class configuration
// ---------------------------------------------------------------------------

/**
 * Default set of anvil preset names that are CLOUD-PREFERRED.
 *
 * A turn classified into one of these presets bypasses anvil and lets
 * OpenClaw route to the native subscription provider.
 *
 * Rationale: "planning" is the only preset whose quality on the local
 * 35B-A3B MoE tier was measurably weaker than the cloud tier in the T005
 * bake-off (multi-step decomposition, long horizon).  All other presets
 * perform adequately on local hardware at the measured request distribution.
 *
 * Extend via ANVIL_CLOUD_CLASSES env var or api.pluginConfig.cloudClasses
 * (see getCloudClasses).
 */
export const DEFAULT_CLOUD_CLASSES = new Set(["planning"]);

function configValue(pluginConfig, key) {
  try {
    if (!pluginConfig || typeof pluginConfig !== "object") return undefined;
    return pluginConfig[key];
  } catch {
    return undefined;
  }
}

function parseClassList(value) {
  let names = [];
  if (Array.isArray(value)) {
    names = value;
  } else if (typeof value === "string") {
    names = value.split(",");
  } else {
    return null;
  }

  const normalized = names
    .map((s) => typeof s === "string" ? s.trim() : "")
    .filter(Boolean);
  return normalized.length > 0 ? new Set(normalized) : null;
}

/**
 * Return the effective set of cloud-preferred preset names.
 *
 * Priority:
 *   1. ANVIL_CLOUD_CLASSES env var (comma-separated, replaces the default).
 *   2. api.pluginConfig.cloudClasses (array of strings, replaces the default).
 *   3. DEFAULT_CLOUD_CLASSES.
 *
 * Empty / whitespace-only ANVIL_CLOUD_CLASSES and empty config arrays fall
 * through to the next source (prevents accidentally clearing the set).
 *
 * @param {unknown} [pluginConfig]
 * @returns {Set<string>}
 */
export function getCloudClasses(pluginConfig) {
  const envSet = parseClassList(process.env.ANVIL_CLOUD_CLASSES);
  if (envSet) return envSet;

  const configSet = parseClassList(configValue(pluginConfig, "cloudClasses"));
  if (configSet) return configSet;

  return DEFAULT_CLOUD_CLASSES;
}

/**
 * Return the effective authoritative route endpoint, if configured.
 *
 * Priority:
 *   1. ANVIL_ROUTE_ENDPOINT env var.
 *   2. api.pluginConfig.routeEndpoint.
 *
 * Empty / whitespace-only values are treated as unset.
 *
 * @param {unknown} [pluginConfig]
 * @returns {string|undefined}
 */
export function getRouteEndpoint(pluginConfig) {
  const envVal = process.env.ANVIL_ROUTE_ENDPOINT;
  if (typeof envVal === "string" && envVal.trim() !== "") {
    return envVal.trim();
  }

  const configVal = configValue(pluginConfig, "routeEndpoint");
  if (typeof configVal === "string" && configVal.trim() !== "") {
    return configVal.trim();
  }

  return undefined;
}

// ---------------------------------------------------------------------------
// Routing decision
// ---------------------------------------------------------------------------

/**
 * Make the before_model_resolve routing decision for a classified preset.
 *
 * Cloud-preferred presets → `{}` (no provider/model override; OpenClaw
 * resolves against its configured native provider via
 * `agents.defaults.model.primary` / `agents.defaults.model.fallbacks`).
 *
 * Local-preferred presets → `{ providerOverride: "anvil", modelOverride: preset }`
 * (LIVE-CONFIRMED wire form, OpenClaw 2026.6.6, 2026-06-30: provider MUST be
 * named separately; a lone `modelOverride: "anvil/<preset>"` is mis-resolved
 * as `<defaultProvider>/anvil/<preset>` → model_not_found).
 *
 * @param {string} preset - the classified anvil preset (from classify.mjs)
 * @param {Set<string>} cloudClasses - cloud-preferred preset names
 * @returns {{ providerOverride?: string; modelOverride?: string }}
 */
export function makeRoutingDecision(preset, cloudClasses) {
  if (cloudClasses.has(preset)) {
    // Cloud-preferred: return no override.  OpenClaw uses its native provider.
    // This is the ONE routing path where the keyless 503 -> native-failover
    // story is actually sound: no providerOverride is ever emitted here, so
    // there is nothing for OpenClaw's attempt loop to stick to.
    return {};
  }
  // Local-preferred: route to anvil.
  // Wire form (LIVE-CONFIRMED): providerOverride names the provider;
  // modelOverride carries the BARE preset (not "anvil/<preset>").
  //
  // KNOWN DEFECT: once this providerOverride is emitted, a subsequent anvil
  // 503 is NOT reliably rescued by agents.defaults.model.fallbacks — see the
  // module docstring above and docs/adr/0005-anvil-503-native-failover-unreliable.md.
  return { providerOverride: "anvil", modelOverride: preset };
}

// ---------------------------------------------------------------------------
// Optional authoritative mode: POST /v1/route (T007)
// ---------------------------------------------------------------------------

/**
 * Call anvil's `POST /v1/route` endpoint (T007) for an authoritative
 * local-vs-cloud routing decision.
 *
 * The endpoint returns `{ tier: "local" | "cloud", ... }` (T007 contract).
 * Returns `"local"`, `"cloud"`, or `null` (on any error/timeout).
 *
 * Enable by setting `ANVIL_ROUTE_ENDPOINT` (e.g. `http://127.0.0.1:8000/v1/route`).
 * The caller falls back to client-side classify on a `null` return.
 *
 * Trade-off vs client-side classify:
 *   + Authoritative: router's full quality profile + config; catches edge
 *     cases the keyword heuristic misses.
 *   - Adds one loopback round-trip (~1-5ms; `timeoutMs` bounds the tail).
 *   - If anvil is unreachable, falls back to client-side (no run breakage).
 *
 * @param {string} prompt
 * @param {Array<{kind:string}>|undefined} attachments
 * @param {string} endpoint - full URL (http://host:port/v1/route)
 * @param {number} [timeoutMs=30] - request timeout in ms (30ms keeps hook
 *   overhead < 5% for co-located anvil; raise if anvil is on a separate host)
 * @returns {Promise<"local"|"cloud"|null>}
 */
export function fetchAnvilTier(prompt, attachments, endpoint, timeoutMs = 30) {
  return new Promise((resolve) => {
    try {
      // Build a minimal completions-shaped body (POST /v1/route contract).
      const body = JSON.stringify({
        model: "chat",
        messages: [{ role: "user", content: String(prompt ?? "") }],
      });

      const url = new URL(endpoint);
      const lib = url.protocol === "https:" ? httpsRequest : httpRequest;
      const port =
        url.port
          ? Number(url.port)
          : url.protocol === "https:"
            ? 443
            : 80;

      const req = lib(
        {
          hostname: url.hostname,
          port,
          path: url.pathname + (url.search || ""),
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Content-Length": Buffer.byteLength(body),
          },
          timeout: timeoutMs,
        },
        (res) => {
          let data = "";
          res.on("data", (chunk) => { data += chunk; });
          res.on("end", () => {
            try {
              const parsed = JSON.parse(data);
              const tier = parsed?.tier;
              resolve(tier === "local" || tier === "cloud" ? tier : null);
            } catch {
              resolve(null);
            }
          });
          res.on("error", () => resolve(null));
        },
      );
      req.on("timeout", () => {
        req.destroy();
        resolve(null);
      });
      req.on("error", () => resolve(null));
      req.write(body);
      req.end();
    } catch {
      resolve(null);
    }
  });
}
