// TypeScript declarations for ./route.mjs (T008 routing decision layer).
// Lets index.ts import typed routing functions while the implementation stays
// plain ESM so `node --test test.mjs` runs without transpilation.

/**
 * Default cloud-preferred preset names.  Only "planning" by default
 * (eval-proven in T005 bake-off).  Override via ANVIL_CLOUD_CLASSES env var.
 */
export declare const DEFAULT_CLOUD_CLASSES: Set<string>;
export declare const DEFAULT_NATIVE_PROVIDER: string;
export declare const DEFAULT_NATIVE_MODEL: string;
export declare const DEFAULT_ROUTE_TIMEOUT_MS: number;
export declare const MAX_ROUTE_TIMEOUT_MS: number;
export declare const ANVIL_PRESETS: Set<string>;

/**
 * Return the effective cloud-preferred preset set.
 * Reads ANVIL_CLOUD_CLASSES (comma-separated) if set, then
 * pluginConfig.cloudClasses, otherwise DEFAULT_CLOUD_CLASSES.
 */
export declare function getCloudClasses(pluginConfig?: unknown): Set<string>;

/**
 * Return the effective authoritative route endpoint.
 * Reads ANVIL_ROUTE_ENDPOINT if set, then pluginConfig.routeEndpoint.
 */
export declare function getRouteEndpoint(pluginConfig?: unknown): string | undefined;

/**
 * Return the effective authoritative /v1/route timeout in milliseconds.
 * Reads ANVIL_ROUTE_TIMEOUT_MS if set, then pluginConfig.routeTimeoutMs,
 * otherwise DEFAULT_ROUTE_TIMEOUT_MS. Invalid values fall back.
 */
export declare function getRouteTimeoutMs(pluginConfig?: unknown): number;

/**
 * Resolve an explicit anvil preset from OpenClaw's model hook context.
 * Used for runtime overrides such as anvil/chat-fast.
 */
export declare function resolveExplicitAnvilPresetFromContext(
  ctx: unknown,
): "planning" | "quick-edit" | "review" | "chat" | "chat-fast" | "long-context" | undefined;

/**
 * Return the env var name containing the optional /v1/route auth token.
 * Reads ANVIL_ROUTE_AUTH_ENV if set, then pluginConfig.routeAuthEnv.
 */
export declare function getRouteAuthEnv(pluginConfig?: unknown): string | undefined;

/**
 * Resolve the optional /v1/route auth token from getRouteAuthEnv().
 */
export declare function resolveRouteAuthToken(pluginConfig?: unknown): string | undefined;

/**
 * Return the explicit native provider/model route for cloud-preferred presets.
 * Reads ANVIL_NATIVE_PROVIDER / ANVIL_NATIVE_MODEL if set, then plugin config,
 * otherwise the defaults.
 */
export declare function getNativeRoute(pluginConfig?: unknown): {
  providerOverride: string;
  modelOverride: string;
};

/**
 * Make the before_model_resolve routing decision for a classified preset.
 *
 * Returns an explicit native provider/model override for cloud-preferred
 * presets, or { providerOverride: "anvil", modelOverride: preset } for local
 * presets.
 */
export declare function makeRoutingDecision(
  preset: string,
  cloudClasses: Set<string>,
  nativeRoute?: { providerOverride: string; modelOverride: string },
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
  timeoutOrOptions?: number | { timeoutMs?: number; authToken?: string; workClass?: string },
  authToken?: string,
): Promise<"local" | "cloud" | null>;
