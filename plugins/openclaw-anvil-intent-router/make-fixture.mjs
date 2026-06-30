#!/usr/bin/env node
// make-fixture.mjs — regenerate decision_log.fixture.jsonl from the REAL plugin
// classifier (the shared ./classify.mjs that index.ts also imports).
//
// Why this exists: the committed fixture must be provably the plugin's real
// output, not hand-faked. This script imports the SAME `classify` AND the SAME
// routing-decision layer (route.mjs) the plugin uses, runs them over labeled
// synthetic turns, ASSERTS each classification equals its label (so a drift in
// classify fails generation loudly), and writes one decision-log line per turn
// in the exact shape index.ts emits (T008 routing split):
//   { synthetic, ts, runId, sessionKey, source:"openclaw", intent, destination,
//     providerOverride, modelOverride, authoritative, prompt_chars }
//
// T008 split (mirrors index.ts): a CLOUD-preferred intent (default: planning)
// routes to the native provider -> { } (no override) -> destination:"native",
// providerOverride:null, modelOverride:null. A LOCAL intent routes to anvil ->
// { providerOverride:"anvil", modelOverride:"<bare preset>" } -> destination:"anvil".
// `authoritative` is always false here (the fixture never calls /v1/route).
//
// Run:  node plugins/openclaw-anvil-intent-router/make-fixture.mjs
// Output is deterministic (fixed base timestamp + per-turn offset) so
// regenerating is a no-op diff unless classify, the routing split, or the turns
// change.

import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { classify, PRESETS } from "./classify.mjs";
import { makeRoutingDecision, getCloudClasses } from "./route.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "decision_log.fixture.jsonl");

// Labeled synthetic turns. Each `expect` is the preset the plugin MUST produce;
// generation fails if classify disagrees. Covers ALL five presets (planning
// included) plus the two attachment-kind paths (image -> review, many
// attachments -> long-context) and the multi-file -> review pool.
const TURNS = [
  {
    sessionKey: "sess-synthetic-001",
    prompt: "Plan the migration across services and produce a build plan before we touch code.",
    expect: "planning",
  },
  {
    sessionKey: "sess-synthetic-001",
    prompt: "Fix the off-by-one in pagination.ts.",
    expect: "quick-edit",
  },
  {
    sessionKey: "sess-synthetic-001",
    prompt: "Please review this diff and find bugs before I merge.",
    expect: "review",
  },
  {
    sessionKey: "sess-synthetic-001",
    prompt: "What does the word idempotent actually convey?",
    expect: "chat",
  },
  {
    sessionKey: "sess-synthetic-001",
    prompt: "Summarize the attached reports.",
    attachments: [
      { kind: "document" },
      { kind: "document" },
      { kind: "document" },
      { kind: "document" },
      { kind: "document" },
      { kind: "document" },
    ],
    expect: "long-context",
  },
  {
    sessionKey: "sess-synthetic-001",
    prompt: "Refactor the auth module across the codebase.",
    expect: "review", // multi-file -> review pool
  },
  {
    sessionKey: "sess-synthetic-001",
    prompt: "Take a look at this screenshot.",
    attachments: [{ kind: "image" }],
    expect: "review", // multimodal -> capable/vision tier
  },
  // --- regression turns for the classify.py-mirror fixes (FIX 2-5) -----------
  {
    sessionKey: "sess-synthetic-002",
    prompt: "Migrate the database to Postgres.",
    expect: "review", // "migrate the" -> multi-file-refactor -> review pool
  },
  {
    sessionKey: "sess-synthetic-002",
    prompt: "Patch the null deref.",
    expect: "quick-edit", // "patch" -> bounded-edit -> quick-edit
  },
  {
    sessionKey: "sess-synthetic-002",
    prompt: "Walk me through it step by step.",
    expect: "planning", // "step by step" -> planning
  },
  {
    sessionKey: "sess-synthetic-002",
    prompt: "Help me with planning the sprint.",
    expect: "planning", // gerund "planning" -> planning (plan(ning|s)? forward-fix)
  },
  {
    sessionKey: "sess-synthetic-002",
    prompt: "Give me an update on status.",
    expect: "chat", // bare "update" is NOT a keyword (only the phrase "update the") -> chat
  },
  {
    sessionKey: "sess-synthetic-002",
    prompt: "Have a listen to this recording.",
    attachments: [{ kind: "audio" }],
    expect: "review", // non-image media short-circuit -> review (never "chat")
  },
  {
    sessionKey: "sess-synthetic-002",
    prompt: "Watch this and tell me what you think.",
    attachments: [{ kind: "video" }],
    expect: "review", // non-image media short-circuit -> review (never "chat")
  },
];

// Deterministic clock: fixed base + 47s per turn (no wall-clock noise).
const BASE_MS = Date.parse("2026-06-30T17:00:00.000Z");
const STEP_MS = 47_000;

// Resolve the cloud-class set ONCE (same call index.ts makes per fire). Reads
// ANVIL_CLOUD_CLASSES if set; otherwise the default {"planning"}.
const CLOUD_CLASSES = getCloudClasses();

const lines = [];
const failures = [];

TURNS.forEach((turn, i) => {
  const intent = classify(turn.prompt, turn.attachments);
  if (intent !== turn.expect) {
    failures.push(
      `turn[${i}] ${JSON.stringify(turn.prompt)} -> classify=${intent} but expected ${turn.expect}`,
    );
  }
  // T008 routing split — the SAME decision index.ts makes (route.mjs), so the
  // fixture is provably the plugin's real output: cloud-preferred intents ->
  // {} (native); local intents -> {providerOverride:"anvil", modelOverride:intent}.
  const routeOverride = makeRoutingDecision(intent, CLOUD_CLASSES);
  const destination =
    routeOverride.providerOverride === "anvil" ? "anvil" : "native";
  const record = {
    synthetic: true,
    ts: new Date(BASE_MS + i * STEP_MS).toISOString(),
    runId: `run-synthetic-${String(i + 1).padStart(4, "0")}`,
    sessionKey: turn.sessionKey,
    source: "openclaw",
    intent,
    destination,
    providerOverride: routeOverride.providerOverride ?? null,
    modelOverride: routeOverride.modelOverride ?? null,
    authoritative: false,
    prompt_chars: turn.prompt.length,
  };
  lines.push(JSON.stringify(record));
});

if (failures.length > 0) {
  console.error("make-fixture: classify disagreed with the labeled turns:\n  " + failures.join("\n  "));
  process.exit(1);
}

// Sanity: every preset in the closed enum must appear at least once.
const records = lines.map((l) => JSON.parse(l));
const covered = new Set(records.map((r) => r.intent));
const missing = PRESETS.filter((p) => !covered.has(p));
if (missing.length > 0) {
  console.error(`make-fixture: presets not represented in fixture: ${missing.join(", ")}`);
  process.exit(1);
}

// Sanity (T008): the fixture must demonstrate BOTH sides of the routing split —
// at least one "native" line (a cloud-preferred intent) AND at least one "anvil"
// line. Guards against a degenerate regen (e.g. ANVIL_CLOUD_CLASSES="" collapsing
// every turn to anvil, or all-cloud), which would make the fixture a non-faithful
// replica of the split index.ts guarantees.
const destinations = new Set(records.map((r) => r.destination));
for (const d of ["native", "anvil"]) {
  if (!destinations.has(d)) {
    console.error(
      `make-fixture: fixture does not exercise destination="${d}" — the T008 ` +
        `routing split is not demonstrated (check ANVIL_CLOUD_CLASSES; the ` +
        `default {"planning"} must route planning turns to native).`,
    );
    process.exit(1);
  }
}

writeFileSync(OUT, lines.join("\n") + "\n");
console.log(`make-fixture: wrote ${lines.length} synthetic decision-log line(s) -> ${OUT}`);
console.log(`make-fixture: presets covered = ${[...covered].sort().join(", ")}`);
console.log(`make-fixture: destinations covered = ${[...destinations].sort().join(", ")}`);
