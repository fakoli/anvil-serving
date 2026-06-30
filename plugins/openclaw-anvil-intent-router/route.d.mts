// TypeScript declarations for ./route.mjs (T008 routing decision layer).
// Lets index.ts import typed routing functions while the implementation stays
// plain ESM so `node --test test.mjs` runs without transpilation.

/**
 * Default cloud-preferred preset names.  Only "planning" by default
 * (eval-proven in T005 bake-off).  Override via ANVIL_CLOUD_CLASSES env var.
 */
export declare const DEFAULT_CLOUD_CLASSES: Set<string>;

/**
 * Return the effective cloud-preferred preset set.
 * Reads ANVIL_CLOUD_CLASSES (comma-separated) if set; otherwise DEFAULT_CLOUD_CLASSES.
 */
export declare function getCloudClasses(): Set<string>;

/**
 * Make the before_model_resolve routing decision for a classified preset.
 *
 * Returns {} for cloud-preferred presets (native provider),
 * or { providerOverride: "anvil", modelOverride: preset } for local presets.
 */
export declare function makeRoutingDecision(
  preset: string,
  cloudClasses: Set<string>,
): { providerOverride?: string; modelOverride?: string };

/**
 * (Optional, async) Call anvil's POST /v1/route (T007) for an authoritative
 * local/cloud routing decision.  Returns "local", "cloud", or null on any
 * error/timeout.  Enable by setting ANVIL_ROUTE_ENDPOINT.
 */
export declare function fetchAnvilTier(
  prompt: string,
  attachments: Array<{ kind: string }> | undefined,
  endpoint: string,
  timeoutMs?: number,
): Promise<"local" | "cloud" | null>;
