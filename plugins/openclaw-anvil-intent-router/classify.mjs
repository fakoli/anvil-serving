// classify.mjs — the SINGLE SOURCE OF TRUTH for the intent heuristic.
//
// Tier-0 keyword taxonomy MIRRORS anvil_serving/router/classify.py — keep in sync
// (a shared vocabulary source is the durable fix; tracked as a follow-up).
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
//   * the keyword phrase sets + their precedence order are a 1:1 MIRROR of
//     classify.py's `_KEYWORD_PHRASES` / `_KEYWORD_RULES`, re-mapped from the
//     router's WORK_CLASSES onto this plugin's CLOSED preset enum:
//        review              -> "review"
//        planning            -> "planning"
//        multi-file-refactor -> "review"      (multi-file reasoning -> review pool)
//        bounded-edit        -> "quick-edit"
//   * over prompt TEXT + attachment KINDS only (the `before_model_resolve` event
//     carries no session messages, no thinking/tools flags — see
//     docs/OPENCLAW-INTEGRATION-SPEC.md §0/§1); so this plugin mirrors the router's
//     KEYWORD taxonomy, while the long-context / attachment signals are the
//     plugin-side analogue of the router's window-pressure / structural hints;
//   * the returned token is one of anvil's CLOSED preset enum (PRESETS below) and
//     must match anvil_serving.router.intent.PRESETS — it becomes the wire model
//     `anvil/<preset>`;
//   * NEVER throws — any failure degrades to the safe default "chat".
//
// KNOWN forward-divergence (intentional, tracked): the planning rule here matches
// the gerund/plural via `plan(ning|s)?`, whereas classify.py's bare `\bplan\b`
// does NOT match "planning"/"plans". The router has the SAME gap and should be
// fixed in a follow-up PR so the two stay in lockstep.

// The closed preset enum = anvil's wire vocabulary (must match the router's
// PRESETS and validate.py's WIRE_FORM_RE).
export const PRESETS = /** @type {const} */ ([
  "planning",
  "quick-edit",
  "review",
  "chat",
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

// Word-boundary keyword sets — a 1:1 MIRROR of classify.py's `_KEYWORD_PHRASES`
// (same phrases, same precedence). Each regex is `\b(?:phrase|...)\b`, built the
// same way the router builds its per-class rules, so multi-word phrases ("break
// down", "step by step", "across the codebase", "rename across", "migrate the",
// "add a", "update the") match as phrases with a single literal space — exactly
// as re.escape produces them on the Python side. Intent words win over
// file-scope/edit words: see the precedence order in `classify`.
//
// review:       review | critique | feedback | audit
const REVIEW_RE = /\b(?:review|critique|feedback|audit)\b/;
// planning:     plan(ning|s)? | design | architect | decompose | roadmap | break down | step by step
//               (the `(?:ning|s)?` is the intentional forward-fix over classify.py — see header.)
const PLANNING_RE = /\b(?:plan(?:ning|s)?|design|architect|decompose|roadmap|break down|step by step)\b/;
// multi-file-refactor -> review pool:  refactor | rename across | across the codebase | migrate the
const MULTIFILE_RE = /\b(?:refactor|rename across|across the codebase|migrate the)\b/;
// bounded-edit -> quick-edit:  edit | fix | change | implement | patch | add a | update the
//               (bare "rename" is intentionally ABSENT — it lives only in the multi-file
//                "rename across" phrase; bare "update" is absent so "give me an update" stays chat.)
const QUICK_EDIT_RE = /\b(?:edit|fix|change|implement|patch|add a|update the)\b/;

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
 * @returns {"planning"|"quick-edit"|"review"|"chat"|"long-context"}
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

    // 4-7. intent keywords (word-boundary; order = classify.py's precedence).
    if (REVIEW_RE.test(p)) return "review";
    if (PLANNING_RE.test(p)) return "planning";
    if (MULTIFILE_RE.test(p)) return "review"; // refactor / across the codebase
    if (QUICK_EDIT_RE.test(p)) return "quick-edit";

    // 8. safe default.
    return "chat";
  } catch {
    // NEVER throw: a classifier failure must not break a run.
    return "chat";
  }
}
