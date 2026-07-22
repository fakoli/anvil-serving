// classify.mjs — the SINGLE SOURCE OF TRUTH for the intent heuristic.
//
// Tier-0 keyword VOCABULARY now lives in one canonical data file,
// ./tier0_keywords.json — a byte-identical bundled copy of
// anvil_serving/router/tier0_keywords.json (the plugin ships its own copy
// because, installed into ~/.openclaw, it cannot read the Python package at
// runtime). BOTH this module and classify.py BUILD their keyword regexes FROM
// that JSON, and tests/router/test_keyword_parity.py fails if the two copies
// drift — so the two classifiers can no longer silently diverge (they did once;
// found in T014 review). The keyword LITERALS are no longer hand-written here.
//
// This plain-ESM module holds the real `classify` used by BOTH the plugin
// (`index.ts`, which imports it) and the fixture generator (`make-fixture.mjs`).
// Sharing one module is what makes `decision_log.fixture.jsonl` *provably* the
// plugin's real output rather than a hand-faked stand-in: there is exactly one
// implementation, imported by both. (Plain `.mjs` so `node make-fixture.mjs`
// runs with zero transpilation; `classify.d.mts` carries the TypeScript types.)
//
// Design (mirrors anvil_serving/router/classify.py's intent-FIRST principle):
//   * deterministic — same (prompt, attachments) always yields the same preset;
//   * word-boundary keyword matching, NOT substring (so the planning rule fires
//     on "plan"/"plans"/"planning" but NOT on "explaining"/"planet", and "fix"
//     does not fire on "prefix"/"fixture", "change" not on "exchange", etc.);
//   * the keyword phrase sets + their precedence order are loaded from the
//     shared ./tier0_keywords.json (a byte-identical copy of the router's
//     canonical taxonomy), then re-mapped from the router's WORK_CLASSES onto
//     this plugin's CLOSED preset enum:
//        review              -> "review"
//        planning            -> "planning"
//        multi-file-refactor -> "review"      (multi-file reasoning -> review pool)
//        bounded-edit        -> "quick-edit"
//   * over prompt TEXT + attachment KINDS only (the `before_model_resolve` event
//     carries no session messages, no thinking/tools flags — see
//     docs/OPENCLAW-INTEGRATION-SPEC.md §0/§1); so this plugin mirrors the router's
//     KEYWORD taxonomy, while the long-context / attachment signals are the
//     plugin-side analogue of the router's window-pressure / structural hints;
//   * the returned token is one of this OpenClaw integration's CLOSED preset
//     enum (PRESETS below) — it becomes the wire model `anvil/<preset>`;
//   * NEVER throws — any failure degrades to the safe default "chat".

import { readFileSync } from "node:fs";

// The closed preset enum = the OpenClaw plugin's wire vocabulary. Router-global
// optional presets may be broader; validate.py loads this exported value from
// the actual plugin runtime instead of maintaining another copy.
export const PRESETS = /** @type {const} */ ([
  "planning",
  "quick-edit",
  "review",
  "chat",
  "chat-fast",
  "long-context",
]);

// A prompt at/over this many characters is long-context regardless of keywords
// (mirrors docs/OPENCLAW-INTEGRATION-SPEC.md §1's 24_000 threshold).
const LONG_PROMPT_CHARS = 24_000;
// "many attachments" -> long-context (bulk input). Threshold is inclusive.
const MANY_ATTACHMENTS = 4;

// Non-text attachment kinds the spec enumerates. ANY of these biases away from
// the text-only fast-local default toward a capable/heavier pool ("review", to
// match the original image behavior). "other" is deliberately NOT included — it
// is not an enumerated media kind. (docs/OPENCLAW-INTEGRATION-SPEC.md §0)
const MEDIA_KINDS = new Set(["image", "video", "audio", "document"]);

// Map a ROUTER work-class (the JSON keys) onto this plugin's CLOSED preset enum.
// multi-file-refactor folds into the review pool; bounded-edit -> quick-edit.
const WORK_CLASS_TO_PRESET = {
  review: "review",
  planning: "planning",
  "multi-file-refactor": "review",
  "bounded-edit": "quick-edit",
};

// Hardcoded fallback — a verbatim mirror of tier0_keywords.json, used ONLY if
// the bundled file is missing/unreadable so the plugin never fails to load.
// (test_keyword_parity.py guards this against drift from the canonical JSON.)
const FALLBACK_PHRASES = [
  ["review", ["review", "critique", "feedback", "audit"]],
  ["planning", ["plan", "plans", "planning", "design", "architect", "decompose", "break down", "step by step", "roadmap"]],
  ["multi-file-refactor", ["refactor", "rename across", "across the codebase", "migrate the"]],
  ["bounded-edit", ["edit", "fix", "change", "add a", "update the", "implement", "patch"]],
];

// Escape regex metacharacters so phrases match literally. Spaces are NOT special
// in a JS regex (and Python's re.escape only backslash-escapes the space, which
// still matches a literal space), so multi-word phrases ("break down", "across
// the codebase", "migrate the", "add a", "update the") keep their single literal
// space and match identically on both sides.
function escapeRegex(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Load the canonical keyword taxonomy from the co-located bundled JSON and build
// one word-boundary regex per work-class, in priority order, paired with the
// preset it maps to. Same construction as classify.py's `_KEYWORD_RULES`
// (`\b(?:phrase|...)\b`). Keys beginning with "_" are metadata and skipped.
// Falls back to FALLBACK_PHRASES on ANY error — module load must never throw.
function loadKeywordRules() {
  let entries;
  try {
    const url = new URL("./tier0_keywords.json", import.meta.url);
    const data = JSON.parse(readFileSync(url, "utf8"));
    entries = Object.entries(data).filter(([k]) => !String(k).startsWith("_"));
    if (entries.length === 0) entries = FALLBACK_PHRASES;
  } catch {
    entries = FALLBACK_PHRASES;
  }
  return entries.map(([workClass, phrases]) => {
    const list = Array.isArray(phrases) ? phrases : [];
    const alt = list.map(escapeRegex).join("|");
    return {
      preset: WORK_CLASS_TO_PRESET[workClass] ?? "chat",
      // Empty phrase list -> a regex that never matches (defensive; the
      // canonical JSON never ships an empty class).
      re: alt ? new RegExp("\\b(?:" + alt + ")\\b") : /(?!)/,
    };
  });
}

// Ordered [{ preset, re }, ...] — the intent rules in classify.py's precedence.
const KEYWORD_RULES = loadKeywordRules();

/**
 * Classify one turn's prompt (+ attachment kinds) into an anvil preset.
 *
 * Precedence (first match wins; deterministic):
 *   1. very long prompt              -> "long-context"  (>= LONG_PROMPT_CHARS)
 *   2. many attachments              -> "long-context"  (>= MANY_ATTACHMENTS, bulk input)
 *   3. any media attachment          -> "review"        (image|video|audio|document ->
 *                                                        capable/heavier pool; never "chat")
 *   4. review/critique/feedback...   -> "review"
 *   5. plan/design/architect...      -> "planning"
 *   6. refactor/across the codebase  -> "review"        (multi-file -> review pool)
 *   7. edit/fix/change/implement...  -> "quick-edit"
 *   8. default                       -> "chat"          (safe default; router biases
 *                                                        ambiguous -> safer/cloud tier)
 *
 * Note (3) is placed AFTER the bulk long-context checks so a turn carrying MANY
 * attachments routes to long-context, while a turn carrying a SINGLE media
 * attachment routes to the review (vision/capable) pool.
 *
 * @param {string} prompt
 * @param {{ kind: string }[]} [attachments]
 * @returns {"planning"|"quick-edit"|"review"|"chat"|"chat-fast"|"long-context"}
 */
export function classify(prompt, attachments) {
  try {
    const text = typeof prompt === "string" ? prompt : String(prompt ?? "");
    const atts = Array.isArray(attachments) ? attachments : [];
    const p = text.toLowerCase();

    // 1-2. long-context: a very long prompt OR many attachments (bulk input).
    if (text.length >= LONG_PROMPT_CHARS) return "long-context";
    if (atts.length >= MANY_ATTACHMENTS) return "long-context";

    // 3. multimodal: any non-text attachment needs a capable/vision tier -> review.
    //    A short text turn carrying a video/audio/document/image must NOT be "chat".
    if (atts.some((a) => a && MEDIA_KINDS.has(a.kind))) return "review";

    // 4-7. intent keywords (word-boundary; order = classify.py's precedence,
    //      sourced from tier0_keywords.json). First match wins; review and
    //      multi-file-refactor both map to "review", bounded-edit to "quick-edit".
    for (const rule of KEYWORD_RULES) {
      if (rule.re.test(p)) return rule.preset;
    }

    // 8. safe default.
    return "chat";
  } catch {
    // NEVER throw: a classifier failure must not break a run.
    return "chat";
  }
}
