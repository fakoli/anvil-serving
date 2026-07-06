# OpenClaw integration — validate-first tooling (historical T013)

This directory holds the historical **validate-FIRST** tooling for the
anvil-serving × OpenClaw integration. The production routing plugin now lives in
[`plugins/openclaw-anvil-intent-router/`](../../plugins/openclaw-anvil-intent-router/);
keep using this directory for the wire-form/cadence validator and logging hook.
The original T013 purpose was to settle the **two CRITICAL live gaps** called out in
[`docs/OPENCLAW-INTEGRATION-SPEC.md`](../../docs/OPENCLAW-INTEGRATION-SPEC.md) §6:

| # | Gap | What "pass" means |
|---|-----|-------------------|
| 1 | **Wire `model` value** | Every outbound HTTP `model` string is `^(anvil/)?<preset>$`, **and** the anvil front door accepts **both** the bare (`planning`) and the namespaced (`anvil/planning`) form. The openai-completions convention puts the bare id on the wire; OpenClaw's selection string is `anvil/<preset>` — so anvil must accept both. |
| 2 | **Firing cadence** | `before_model_resolve` fires **once per user message** (so the plugin's per-turn classification is real), confirmed by logging every fire across a multi-turn conversation. Per session: fire-count == user-message-count. |

A pass on both gaps unblocks building the real `before_model_resolve` routing
plugin and the router-side model-name parser (T014) against a confirmed contract,
instead of an assumed one.

## Files

| File | What it is |
|------|------------|
| `validate.py` | Stdlib-only CLI that checks both gaps. PASS/FAIL per check; non-zero exit only on a wire-form violation or a malformed log. |
| `hook-fire-log.jsonl` | **A REPRESENTATIVE FIXTURE — not a live capture.** Every record carries `"synthetic": true`. Models a clean 3-message session (one fire per message) so `validate.py` has something to assert against in CI. |
| `logging-hook/index.ts` | A minimal, **logging-only** `before_model_resolve` plugin. On each fire it appends a JSONL record and returns `{}` (records cadence; **does not route**). This is the instrument you install on the live Fakoli-Mini gateway to produce a REAL `hook-fire-log.jsonl`. |
| `logging-hook/package.json`, `logging-hook/openclaw.plugin.json` | Minimal packaging so the hook can be installed as a local OpenClaw plugin. |
| `skills/anvil-serving-workbench/SKILL.md` | Example OpenClaw-visible workbench skill for operator workflows. |
| `anvil-serving-workbench.example.json5` | Example `skills.load.extraDirs` and agent visibility fragment for the workbench roles. |

## Workbench skill example

The operator workbench skill is the OpenClaw counterpart to the repo-scoped
Codex and Claude Code skills. Until `anvil-serving harness sync openclaw
--skills` exists, load `examples/openclaw/skills` through OpenClaw's
`skills.load.extraDirs` and enable `anvil-serving-workbench` in agent defaults
or specific agents. The example fragment in
`anvil-serving-workbench.example.json5` keeps this narrowly scoped to skill
visibility and role names; provider/model sync remains owned by
`anvil-serving harness sync openclaw`.

## Run the validator (against the committed fixture)

This is the T013 verification command — it must PASS:

```bash
python examples/openclaw/validate.py \
    --assert-wire-form \
    --assert-fire-cadence examples/openclaw/hook-fire-log.jsonl
```

- **`--assert-wire-form`** does two things: (a) checks that the model strings it
  can see match the regex — with no `--capture`, it uses the fixture's
  `modelOverride` selection strings — and (b) the load-bearing proof: it imports
  `anvil_serving.router.intent` and asserts `resolve()` maps `planning` and
  `anvil/planning` (for **every** preset) to the *same* result. (b) is what
  proves the front door accepts both wire forms.
- **`--assert-fire-cadence <log>`** groups fires by session and asserts
  fire-count == user-message-count. If the cadence is *not* 1 fire/message it
  prints the ACTUAL cadence and does **not** fail on that basis alone (the
  criterion allows "or the actual cadence is documented" — record it in the spec).

### Why the fixture's records carry `modelOverride` but the logging hook does not

The logging-only hook (`index.ts`) records cadence and writes
`"modelOverride": null` — it never routes. The **fixture** additionally pre-fills
`modelOverride` with representative legacy `anvil/<preset>` selection strings so the one
committed file can drive *both* checks (cadence **and** wire-form `(a)`) in CI.
The current routing plugin emits `providerOverride:"anvil"` plus a bare preset
`modelOverride`; this fixture remains useful because the front door must accept
both bare and namespaced model strings.
In a real run these come from two different artifacts: cadence from the hook's
log, wire-form `(a)` from a separately **captured outbound request** (`--capture`).

## The LIVE validation (MANUAL — must be run by a human on Fakoli Mini)

> ⚠️ This step cannot be automated from this repo: it requires the real OpenClaw
> install on **Fakoli Mini** (the gateway box) talking to a running anvil-serving
> front door. The committed `hook-fire-log.jsonl` is only a representative stand-in.

### Gap 2 — firing cadence (the logging hook)

1. **Start the anvil-serving front door** somewhere OpenClaw can reach it
   (loopback is fine): `anvil-serving serve --config configs/example.toml`
   (front door defaults to `http://127.0.0.1:8000/v1`).
2. **Install the logging hook** on Fakoli Mini. Use `--link` (symlinked install) —
   OpenClaw >=2026.6.11 rejects a plain copy-install for a TypeScript/compiled
   plugin like this one:
   ```bash
   openclaw plugins install --link ./examples/openclaw/logging-hook
   ```
3. **Grant conversation access** (REQUIRED for any non-bundled plugin using
   `before_model_resolve`) in `~/.openclaw/openclaw.json`:
   ```jsonc
   plugins: { entries: { "anvil-fire-logger": { hooks: { allowConversationAccess: true } } } }
   ```
   Then restart the gateway: `openclaw gateway restart`.
4. **Point the log somewhere writable** (optional): `export ANVIL_FIRE_LOG=/abs/path/hook-fire-log.jsonl`
   (defaults to `./hook-fire-log.jsonl` in the gateway's CWD).
5. **Run a multi-turn conversation** — send **a known number N of user messages**
   in one session.
6. **Validate the REAL log**:
   ```bash
   python examples/openclaw/validate.py --assert-fire-cadence /abs/path/hook-fire-log.jsonl
   ```
   - The validator confirms *internal consistency*: each recorded user message
     has exactly one fire.
   - **You** confirm the other half: the log has **N** distinct `userMessageIndex`
     values (== the N messages you sent). If it has fewer, the hook is firing
     once per *session/run-span* rather than per message; if a message shows >1
     fire, it is firing per *attempt*. Either way the validator prints the actual
     cadence — copy it into `docs/OPENCLAW-INTEGRATION-SPEC.md` §6.

### Gap 1 — wire `model` value (capture an outbound request)

1. With the provider block from the spec §2 pointing OpenClaw's `anvil` provider
   at the front door, send one turn (e.g. with `agents.defaults.model.primary =
   "anvil/chat"`).
2. **Capture the actual outbound HTTP request** the gateway makes to the front
   door — e.g. read it off the anvil-serving access log, or put a tiny echo proxy
   in front of `:8000`. Save the request body (or just its `model` field) to a
   JSON/JSONL file, e.g. `captured-request.json`:
   ```json
   { "model": "chat", "messages": [ ... ] }
   ```
3. **Validate the captured wire form**:
   ```bash
   python examples/openclaw/validate.py --assert-wire-form --capture captured-request.json
   ```
   This settles whether the wire value is the bare id or the `anvil/`-prefixed
   ref. anvil already accepts **both** (proven by check `(b)`), so either result
   is fine — but capturing it removes the last assumption before T014.

### Full live run (both gaps at once)

```bash
python examples/openclaw/validate.py \
    --assert-wire-form --capture captured-request.json \
    --assert-fire-cadence /abs/path/hook-fire-log.jsonl
```

## Scope (what this directory is and isn't)

This is **validate-first** tooling: it ships the wire-form acceptance (already present in
`anvil_serving/router/intent.py` — `parse_model()` strips an optional `anvil/` /
`anvil:` prefix), the validator, the logging instrument, the fixture, and these
docs. The routing/classifier plugin has since been built in
`plugins/openclaw-anvil-intent-router/`; do not treat this directory as the
current plugin implementation.
