// TypeScript declarations for ./classify.mjs (the shared runtime heuristic).
// Lets index.ts import a typed `classify` while the implementation stays plain
// ESM so `node make-fixture.mjs` runs without transpilation.

// The CLOSED preset enum = anvil's wire vocabulary. Must match the router's
// anvil_serving.router.intent.PRESETS ids and validate.py's WIRE_FORM_RE.
export type AnvilPreset = "planning" | "quick-edit" | "review" | "chat" | "long-context";

export declare const PRESETS: readonly AnvilPreset[];

/**
 * Deterministically classify a turn's prompt (+ attachment kinds) into an
 * anvil preset. Word-boundary keyword matching over prompt text + attachment
 * kinds only; never throws (degrades to "chat").
 */
export declare function classify(
  prompt: string,
  attachments?: { kind: string }[],
): AnvilPreset;
