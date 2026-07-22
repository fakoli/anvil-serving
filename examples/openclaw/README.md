# OpenClaw integration — validate-first tooling (historical T013)

This directory holds the historical **validate-FIRST** tooling for the
anvil-serving × OpenClaw integration. The production routing plugin now lives in
[`plugins/openclaw-anvil-intent-router/`](../../plugins/openclaw-anvil-intent-router/);
keep using this directory for the wire-form/cadence validator and logging hook.
The original T013 purpose was to settle two critical live gaps now recorded in the
[`OpenClaw integration contract`](../../docs/OPENCLAW-INTEGRATION-SPEC.md) history (§7). The model-id
contract is current §3, and both wire-form and cadence checks remain in the upgrade gate (§6):

| # | Gap | What "pass" means |
|---|-----|-------------------|
| 1 | **Wire `model` value** | Every Anvil-bound HTTP `model` string names a plugin preset, **and** the anvil front door accepts **both** the bare (`planning`) and the namespaced (`anvil/planning`) form. The openai-completions convention puts the bare id on the wire; OpenClaw's selection string is `anvil/<preset>` — so anvil must accept both. Native-bound routes use their configured native model id and are not Anvil wire evidence. |
| 2 | **Firing cadence** | `before_model_resolve` fires **once per user message** (so the plugin's per-turn classification is real), confirmed by logging every fire across a multi-turn conversation. Per session: fire-count == user-message-count. |

The aggregate command is green on the committed fixture. The validator loads the shipped plugin's
actual runtime preset export, so optional router-global presets such as `ocr` and `vision` do not
silently broaden the OpenClaw contract.

A pass on both gaps unblocks building the real `before_model_resolve` routing
plugin and the router-side model-name parser (T014) against a confirmed contract,
instead of an assumed one.

## Files

| File | What it is |
|------|------------|
| `validate.py` | Python-stdlib CLI that checks both gaps and uses the OpenClaw/Node runtime to load the shipped plugin preset export. PASS/FAIL per check; malformed capture evidence fails closed. |
| `colo_smoke.py` | Stdlib-only COLO smoke/eval runner for Fakoli Mini OpenClaw -> Fakoli Dark anvil-serving. Writes story-mapped JSON evidence in fixture or live mode. |
| `hook-fire-log.jsonl` | **A REPRESENTATIVE FIXTURE — not a live capture.** Every record carries `"synthetic": true`. Models a clean 3-message session (one fire per message) so `validate.py` has something to assert against in CI. |
| `logging-hook/index.ts` | A minimal, **logging-only** `before_model_resolve` plugin. On each fire it appends a JSONL record and returns `{}` (records cadence; **does not route**). This is the instrument you install on the live Fakoli-Mini gateway to produce a REAL `hook-fire-log.jsonl`. |
| `logging-hook/package.json`, `logging-hook/openclaw.plugin.json` | Minimal packaging so the hook can be installed as a local OpenClaw plugin. |
| `skills/anvil-serving-workbench/SKILL.md` | Example OpenClaw-visible workbench skill for operator workflows. |
| `anvil-serving-workbench.example.json5` | Example `skills.load.extraDirs` and agent visibility fragment rendered by `harness sync openclaw --skills`. |

## COLO smoke/eval runner

Use `colo_smoke.py` when validating the live Mini-to-Dark OpenClaw path before
trusting broader promotion evidence. It is story-driven: every proof in the
artifact references a product story for provider visibility, local-safe edits,
planning-route clarity, long-context budget, plugin wiring, router auditability,
performance evidence, drift repair, and explicit cloud usage.

The deterministic fixture mode is the CI-safe baseline and requires no OpenClaw,
SSH, router token, Docker, or model serve:

```bash
python examples/openclaw/colo_smoke.py \
  --fixture \
  --artifact .anvil/evidence/openclaw-colo-fixture.json \
  --pretty
```

The live COLO run collects Fakoli Mini gateway diagnostics over SSH and probes
the Fakoli Dark router URL configured for the `anvil` provider:

```bash
python examples/openclaw/colo_smoke.py \
  --live \
  --gateway-host fakoli-mini \
  --router-base-url http://100.87.34.66:8000/v1 \
  --artifact .anvil/evidence/openclaw-colo-live.json \
  --pretty
```

Live generation and performance probes are explicit because they can consume
model time. Add `--run-generations` only when the operator wants latency,
output-token, and tokens/sec measurements from bounded `/v1/chat/completions`
turns. The bounded generation budget is model/tier-specific: `colo_smoke.py`
first reads `params.generation_probe_max_tokens` from the routed tier in the
router config, then falls back to the CLI defaults. When swapping the heavy
model, recalibrate that tier parameter rather than editing the runner:

```bash
python examples/openclaw/colo_smoke.py \
  --live \
  --gateway-host fakoli-mini \
  --router-base-url http://100.87.34.66:8000/v1 \
  --run-generations \
  --heavy-generation-max-tokens 256 \
  --expect-min-tokens-per-second <operator-threshold> \
  --artifact .anvil/evidence/openclaw-colo-live-generations.json
```

For release or blog evidence, add the repeatable interaction benchmark. This
runs fixed direct-router intent prompts from the OpenClaw gateway host, records
route provider/model from companion `/v1/route` probes, exact usage token counts
for non-streaming calls, TTFT for streaming calls, finish reasons, and the
recipe dimensions that were applied. These calls validate the gateway-to-router
path and router behavior; they are not full OpenClaw agent turns. The dimensions
are read from the routed tier's `params`, not from hidden constants in the
runner:

```toml
[[router.tiers]]
id = "heavy-local"

[router.tiers.params]
generation_probe_max_tokens = 256
interaction_benchmark_max_tokens = 1024
interaction_benchmark_stream_max_tokens = 512
interaction_benchmark_reasoning_effort = "low"
interaction_benchmark_max_tokens_by_intent = { planning = 2048 }
interaction_benchmark_stream_max_tokens_by_intent = { planning = 1024 }
```

Run it explicitly:

```bash
python examples/openclaw/colo_smoke.py \
  --live \
  --gateway-host fakoli-mini \
  --router-base-url http://100.87.34.66:8000/v1 \
  --run-generations \
  --run-interaction-benchmark \
  --artifact .anvil/evidence/openclaw-colo-live-interactions.json \
  --pretty
```

If a new serve recipe changes model family, reasoning controls, context, or
throughput, update these `params` in the router config alongside the serve
recipe. The benchmark artifact should explain truncation, 503 quality-gate
failures, and token throughput through recipe metadata rather than through a
one-off command-line tweak.

The live `2026-07-07` Mini-to-Dark run is summarized in
[`docs/findings/2026-07-07-openclaw-colo-interaction-benchmark.md`](../../docs/findings/2026-07-07-openclaw-colo-interaction-benchmark.md).
Use that findings note as the site/blog citation block: it records the exact
artifact hash, recipe, route/model evidence, latency, TTFT, token throughput,
and caveat language for bounded benchmark claims.

The artifact has these top-level sections: `stories`, `proofs`,
`environment`, `openclaw_config`, `plugin_runtime`, `router_probes`,
`e2e_turns`, `benchmarks`, `interaction_benchmarks`, `drift`, `cloud_usage`,
`repair`, and `verdict`.
`verdict.status` is `pass`, `warn`, or `fail`; warnings are acceptable for
skipped live generation or advisory hygiene findings, but they should be copied
into the operator report instead of being ignored.

Secret hygiene is part of the contract. The runner reports only API-key shape
such as `env-ref` or `literal`, redacts bearer/token-shaped values, and keeps
environment variable names such as `ANVIL_ROUTER_TOKEN` as names only.
For the macOS OpenClaw LaunchAgent, `${ANVIL_ROUTER_TOKEN}` must be present in
the gateway service environment, not just the interactive shell. Validate with
`openclaw config validate` from a process that has the same env, and keep the
literal token out of `~/.openclaw/openclaw.json`.

Repair mode is a human-gated preview. It does not write OpenClaw config or
restart the gateway. It records the product command the operator should review:

```bash
anvil-serving harness sync openclaw \
  --config examples/fakoli-dark/anvil-router.live.toml \
  --base-url http://100.87.34.66:8000/v1 \
  --gateway-host fakoli-mini \
  --restart
```

Use that path instead of manual edits to `~/.openclaw/openclaw.json`. The smoke
artifact is evidence for a recommendation; it never promotes a router profile,
changes routing policy, or enables metered cloud.

## Overnight operator checklist

Use this checklist on Fakoli Mini when validating that OpenClaw can see the
anvil-serving workbench skill. Non-interactive shells may need the Homebrew path
first:

```bash
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH
```

Prerequisite: the `anvil` provider/model config must already be registered by
`anvil-serving harness sync openclaw` or the setup steps in the
[`openclaw-anvil-intent-router` README](../../plugins/openclaw-anvil-intent-router/README.md).
The example agents use `anvil/planning`, `anvil/chat-fast`, and `anvil/review`,
so they are only usable after the provider models exist.

1. Confirm the intent router plugin is loaded:

   ```bash
   openclaw plugins inspect openclaw-anvil-intent-router --runtime --json
   ```

   Success means `status:"loaded"`, `activated:true`, and a
   `before_model_resolve` hook.

2. Install the workbench skill from a checked-out or copied skill directory:

   ```bash
   openclaw skills install /path/to/examples/openclaw/skills/anvil-serving-workbench \
     --as anvil-serving-workbench
   ```

3. Confirm the skill is visible:

   ```bash
   openclaw skills info anvil-serving-workbench --json
   openclaw skills check --json
   ```

   Success means `eligible`, `modelVisible`, `userInvocable`, and
   `commandVisible` are all `true`.

4. Preview the provider, skill, and agent config that anvil-serving would render:

   ```bash
   anvil-serving harness sync openclaw \
     --config configs/example.toml \
     --skills \
     --out -
   ```

   The rendered roles are `anvil-orchestrator`, `anvil-inventory-scout`,
   `anvil-route-analyst`, `anvil-serve-operator`,
   `anvil-preflight-runner`, `anvil-benchmark-runner`, and
   `anvil-evidence-reporter`. Independent critic/reviewer roles are not
   generated through the Anvil provider because it may route them back to the
   candidate being evaluated.

5. Save evidence with the PR or task packet:

   ```bash
   openclaw plugins inspect openclaw-anvil-intent-router --runtime --json > openclaw-plugin.json
   openclaw skills info anvil-serving-workbench --json > openclaw-skill.json
   openclaw skills check --json > openclaw-skills-check.json
   ```

`anvil-serving harness sync openclaw` owns provider/model config. Add
`--skills` to render the OpenClaw-visible workbench skill and Anvil sub-agent
roles in the same config payload.

## Workbench skill example

The operator workbench skill is the OpenClaw counterpart to the repo-scoped
Codex and Claude Code skills. Use one of two loading modes:

- Workspace install: run `openclaw skills install <skill-dir> --as
  anvil-serving-workbench`, then preview with `anvil-serving harness sync
  openclaw --skills --out -` and apply with `--out ~/.openclaw/openclaw.json`
  locally or `--gateway-host <mini>` remotely. No `skills.load.extraDirs` is
  needed.
- Checkout load: point `skills.load.extraDirs` at an absolute checkout path such
  as `/absolute/path/to/anvil-serving/examples/openclaw/skills` by passing
  `--skill-dir /absolute/path/to/anvil-serving/examples/openclaw/skills`.

The sync keeps this narrowly scoped to Anvil-owned skill visibility and role
names while provider/model sync remains owned by the same command. When applying
over an existing plain-JSON OpenClaw config through the merge path, unrelated
providers, agents, plugins, and existing skill directories are preserved and the
tool attempts a backup before write. Commented JSON5 configs cannot be merged
by the stdlib renderer; edit them manually or use `--overwrite` only after
making a separate backup.

Role model mapping is intentionally split: small operational roles use
`anvil/chat-fast` when present and the orchestrator prefers `anvil/planning`.
Configure quality/adversarial critics through a separate provider or external
harness so they are independent from the candidate route. Small-model roles
must not change routing policy or promote profiles; live promotion, cloud
enablement, host repair, public bind, and destructive cache work remain
human-gated.

## Run the validator (against the committed fixture)

This is the T013 verification command — it must PASS:

```bash
python examples/openclaw/validate.py \
    --assert-wire-form \
    --assert-fire-cadence examples/openclaw/hook-fire-log.jsonl
```

- **`--assert-wire-form`** does two things: (a) loads the actual `PRESETS` export
  from the shipped plugin and checks visible Anvil-bound model strings against
  that vocabulary — with no `--capture`, it uses the fixture's `modelOverride`
  selection strings — and (b) the load-bearing proof: it imports
  `anvil_serving.router.intent` and asserts `resolve()` maps each plugin preset
  and its `anvil/`-prefixed form to the same configured result. Optional
  router-global presets are reported separately and do not fail this plugin
  contract. `--config FILE` selects the router config for that resolver/mapping
  proof; it defaults to the committed `configs/example.toml`.
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
A current plugin `decision_log.jsonl` is also accepted: schema-consistent native
routes are skipped, every Anvil-bound `modelOverride` is checked, and a capture
with no Anvil-bound evidence fails rather than claiming proof.

## The LIVE validation (MANUAL — must be run by a human on Fakoli Mini)

> ⚠️ This step cannot be automated from this repo: it requires the real OpenClaw
> install on **Fakoli Mini** (the gateway box) talking to a running anvil-serving
> front door. The committed `hook-fire-log.jsonl` is only a representative stand-in.

### Gap 2 — firing cadence (the logging hook)

1. **Start the anvil-serving front door** somewhere OpenClaw can reach it
   (loopback is fine): `anvil-serving router run --config configs/example.toml`
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
   python examples/openclaw/validate.py \
     --config /path/to/deployed-router.toml \
     --assert-wire-form --capture captured-request.json
   ```
   This settles whether the wire value is the bare id or the `anvil/`-prefixed
   ref. anvil already accepts **both** (proven by check `(b)`), so either result
   is fine — but capturing it removes the last assumption before T014.

### Full live run (both gaps at once)

```bash
python examples/openclaw/validate.py \
    --config /path/to/deployed-router.toml \
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
