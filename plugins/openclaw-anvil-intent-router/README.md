# openclaw-anvil-intent-router — reference OpenClaw intent-router plugin (T014)

The **reference** OpenClaw `before_model_resolve` plugin for anvil-serving. On
each turn it classifies the prompt (text + attachment kinds) into one of anvil's
closed presets and emits `{ providerOverride: "anvil", modelOverride: "<preset>" }`,
so OpenClaw routes the run to the anvil provider's matching preset model.

This is the build that T013 unblocked: T013 shipped the validate-first tooling
(wire-form + fire-cadence) under [`examples/openclaw/`](../../examples/openclaw/);
T014 is the real classify->preset routing plugin built against that confirmed
contract.

> **Focus, not couple.** All OpenClaw-specific code lives in this swappable
> adapter package. The router core (`anvil_serving/router/`) contains **zero**
> OpenClaw references (AC2) — if the hook API churns, only this plugin changes.

## Files

| File | What it is |
|------|------------|
| `index.ts` | The plugin. `definePluginEntry` + `api.on("before_model_resolve", ...)`; classifies, writes a decision-log line, returns `{ providerOverride: "anvil", modelOverride: "<preset>" }`. |
| `classify.mjs` | The SINGLE SOURCE OF TRUTH heuristic (`classify`, `PRESETS`). Imported by both `index.ts` and `make-fixture.mjs`, so the fixture is provably the plugin's real output. |
| `classify.d.mts` | TypeScript declarations for `classify.mjs` (`AnvilPreset`, the closed enum). |
| `package.json`, `openclaw.plugin.json` | Plugin packaging (`type: module`, `extensions: ["./index.ts"]`, `compat.pluginApi >= 2026.4.21`, `activation.onStartup`). |
| `make-fixture.mjs` | Regenerates `decision_log.fixture.jsonl` from the real `classify` over labeled synthetic turns (asserts each label). |
| `decision_log.fixture.jsonl` | **SYNTHETIC fixture — not a live capture.** Every line carries `"synthetic": true`. The committed AC1 artifact. Regenerate with `make-fixture.mjs`. |

## How classification maps to slots

`classify(prompt, attachments)` is deterministic, word-boundary keyword matching
(intent-first, NOT substring), over prompt text + attachment kinds only — the
`before_model_resolve` event carries no session messages
([`docs/OPENCLAW-INTEGRATION-SPEC.md`](../../docs/OPENCLAW-INTEGRATION-SPEC.md) §0/§1).

> **Single taxonomy.** The keyword phrase sets (rows 4-7) and their precedence are a
> 1:1 **mirror of the router's** [`anvil_serving/router/classify.py`](../../anvil_serving/router/classify.py)
> (`_KEYWORD_PHRASES`), re-mapped onto this plugin's preset enum: router
> `multi-file-refactor` -> `review`, router `bounded-edit` -> `quick-edit`. An
> OpenClaw turn therefore classifies identically to the router's inference floor.
> (Keep them in sync; a shared vocabulary source is the durable fix, tracked as a
> follow-up.)

First match wins:

| # | Signal | Preset (`modelOverride`) | Why |
|---|--------|--------------------------|-----|
| 1 | very long prompt (>= 24,000 chars) | `anvil/long-context` | large single input |
| 2 | many attachments (>= 4) | `anvil/long-context` | bulk input |
| 3 | any **media** attachment (`image` / `video` / `audio` / `document`) | `anvil/review` | multimodal -> capable/vision tier (never plain `chat`) |
| 4 | `review` / `critique` / `feedback` / `audit` | `anvil/review` | code review |
| 5 | `plan` / `plans` / `planning` / `design` / `architect` / `decompose` / `roadmap` / `break down` / `step by step` | `anvil/planning` | multi-step planning |
| 6 | `refactor` / `rename across` / `across the codebase` / `migrate the` | `anvil/review` | multi-file -> review pool |
| 7 | `edit` / `fix` / `change` / `implement` / `patch` / `add a` / `update the` | `anvil/quick-edit` | bounded edit |
| 8 | (default) | `anvil/chat` | safe default; router biases ambiguous -> safer/cloud tier |

Notes: the planning rule matches `plan` / `plans` / `planning` (the router's bare
`\bplan\b` does not yet match the gerund/plural — a tracked follow-up on the router
side). Bare `update` is **not** a keyword (only the phrase `update the`), so
"give me an update" stays `chat`; bare `rename` is **not** a quick-edit keyword (it
lives only in the multi-file `rename across` phrase). A single media attachment
biases to `review`; **many** attachments (>= 4) bias to `long-context`.

The preset set is anvil's closed wire vocabulary
`{ planning, quick-edit, review, chat, long-context }` and matches
`anvil_serving.router.intent.PRESETS`. The emitted **bare** `<preset>` (the model
within the `anvil` provider) satisfies the wire-form contract `^(anvil/)?<preset>$`
(see `examples/openclaw/validate.py`); OpenClaw forwards it bare on the wire.

`classify` **never throws** — any internal failure degrades to `chat`, and the
decision-log write is wrapped in `try/catch`, so a logging error never breaks a
run.

## Install (on the OpenClaw gateway — e.g. Fakoli Mini)

1. **Install the plugin** (dev/local):
   ```bash
   openclaw plugins install ./plugins/openclaw-anvil-intent-router
   ```
2. **Grant conversation access — REQUIRED.** Any non-bundled plugin using
   `before_model_resolve` must be granted conversation access in
   `~/.openclaw/openclaw.json`:
   ```jsonc
   plugins: {
     entries: {
       "openclaw-anvil-intent-router": { hooks: { allowConversationAccess: true } }
     }
   }
   ```
   (This hook does **not** mutate the prompt, so it does **not** need
   `allowPromptInjection`.)
3. **Register the anvil provider — REQUIRED, not optional.** This plugin
   **unconditionally** returns `{ providerOverride: "anvil", modelOverride: "<preset>" }`
   on every turn, so `~/.openclaw/openclaw.json` **MUST** define the `anvil` provider with `models[]`
   entries for **every** preset id (`planning`, `quick-edit`, `review`, `chat`,
   `long-context` — spec §2). If the provider/model is missing, forcing an override
   for an unresolvable id can fail model resolution and break the run — there is no
   fallback inside the plugin (it does not check whether the id resolves before
   overriding). Point a custom provider's inline `models[]` at the preset ids
   (full recipe: `docs/OPENCLAW-INTEGRATION-SPEC.md` §2):
   ```jsonc
   models: {
     mode: "merge",
     providers: {
       anvil: {
         baseUrl: "http://127.0.0.1:8000/v1",   // anvil-serving front door (loopback)
         api: "openai-completions",
         models: [
           { id: "planning",     name: "Anvil · Planning" },
           { id: "quick-edit",   name: "Anvil · Quick Edit" },
           { id: "review",       name: "Anvil · Review",       input: ["text", "image"] },
           { id: "chat",         name: "Anvil · Chat" },
           { id: "long-context", name: "Anvil · Long Context" }
         ]
       }
     }
   }
   ```
   > **LIVE-CONFIRMED (OpenClaw 2026.6.6, Fakoli-Mini, 2026-06-30).** The plugin
   > names the provider (`providerOverride: "anvil"`) and emits the **bare** preset
   > (`modelOverride: "planning"`); OpenClaw then forwards the **bare** id on the
   > wire (captured `model: "planning"`). A lone `modelOverride: "anvil/<preset>"`
   > is mis-resolved as `<defaultProvider>/anvil/<preset>` → `model_not_found`,
   > which is why the provider MUST be named separately. The anvil front door
   > accepts both forms (`WIRE_FORM_RE` `^(anvil/)?<preset>$`). Full results +
   > the two bugs the live run caught: `docs/findings/2026-06-30-openclaw-live-validation.md`.
4. **Restart the gateway:** `openclaw gateway restart`.
5. **(Optional) point the decision log somewhere writable:**
   `export ANVIL_DECISION_LOG=/abs/path/decision_log.jsonl`
   (defaults to `./decision_log.jsonl` in the gateway's CWD).

## LIVE integration step (MANUAL — run by a human on the gateway)

> ⚠️ This is the live half of AC1, separately labeled from the committed
> synthetic fixture (mirrors T015's `--replay` vs live split). It requires the
> real OpenClaw install talking to a running anvil-serving front door.

1. Install + gate + register the provider (steps above); restart the gateway.
2. **Send one user message** whose intent is unambiguous, e.g.
   *"Plan the migration across services."*
3. **Assert the routed preset from the decision log** the plugin produced:
   ```bash
   jq -e 'select(.source=="openclaw" and .intent=="planning")' decision_log.jsonl
   ```
   Exit 0 means the run routed to `anvil/planning` (the expected preset) as the
   wire model — AC1 satisfied against a live run. Confirm OpenClaw actually
   dispatched to the anvil endpoint (e.g. the anvil-serving access log shows the
   `anvil/planning` / `planning` request).
4. **(Optional) wire-form check** the live log's `modelOverride` strings:
   ```bash
   python examples/openclaw/validate.py --assert-wire-form --capture decision_log.jsonl
   ```

## The committed fixture is SYNTHETIC

`decision_log.fixture.jsonl` is a **synthetic, regenerable** stand-in for a live
decision log — every line carries `"synthetic": true`. It exists so AC1 can be
asserted in CI without a live gateway. It is produced by `make-fixture.mjs`,
which imports the **same** `classify` the plugin runs, so the fixture is provably
the plugin's real output (the script asserts each labeled turn and that all five
presets are represented). Regenerate it any time with:

```bash
node plugins/openclaw-anvil-intent-router/make-fixture.mjs
```

AC1 (synthetic half) is asserted exactly as the live step, against the fixture:

```bash
jq -e 'select(.source=="openclaw" and .intent=="planning")' \
   plugins/openclaw-anvil-intent-router/decision_log.fixture.jsonl
```
