# harness-router v0.3.0 — changes introduced

> What the `harness-router` PRD added to anvil-serving: a **workload-aware, correctness-gated
> local-model router** for coding harnesses — *local where it's been proven, cloud where it hasn't,
> verified, with automatic fallback*. Design: [`QUALITY-GATED-ROUTER.md`](QUALITY-GATED-ROUTER.md) ·
> [`DIRECTION.md`](DIRECTION.md). All 18 PRD tasks (T001–T018) merged; built by two parallel sessions
> (coordination retro: the session's `RETRO-parallel-session-coordination.md`).

## New package: `anvil_serving/router/`
A request flows: **front door → resolve intent → route → verify → (fallback) → return**, every step
a typed seam, stdlib-only.

| Module | Task | What it adds |
|---|---|---|
| `internal.py` | T001 | `InternalRequest` (dialect-neutral request) + the `Backend` Protocol + helpers (`estimate_tokens`, `flatten_content`). |
| `front_door.py`, `dialects/{anthropic,openai}.py` | T001 | One HTTP server speaking **both** Anthropic Messages and OpenAI Chat Completions; streams in each caller's native SSE; passes through to one injectable backend. The `Dialect` Protocol. |
| `config.py` | T002 | `RouterConfig`/`Tier` + `load()` for the `[router]` block of `configs/example.toml`: per-tier `id`/`base_url`/`dialect`/`context_limit`/`privacy`/`tool_support`/`auth_env` (env-var **name**, never a secret) + preset→candidate-pool + `mapping_version`. |
| `intent.py`, `classify.py` | T003 | Intent resolution: a `model` field that is a named **preset** (bare or `anvil/<preset>`), a **pin** (tier id), or — when neither — a Tier-0 **classifier** that infers a work-class; ambiguous → safer (cloud) tier. |
| `discovery.py` | T004 | `/v1/models` preset discovery (derived from the `intent.PRESETS` registry — no drift). |
| `profile_store.py`, `policy.py` | T005 | The **quality profile** `(tier, work-class) → {decision ∈ allow/allow-with-verify/deny, score}` (hand-authored MVP seed, eval-grounded: planning → cloud) + a **residency-aware routing policy** that filters `deny`, preserves cost order, and avoids multiplexer thrash. |
| `backends/cloud.py`, secrets handling | T006 | Outbound cloud backend (Anthropic/OpenAI) authed via the per-tier env-var name; metrics/log never persist secrets or full prompts. |
| `verify.py` | T007 | Cheap inline **structural verifiers** (non-empty, not-truncated, tool-call JSON valid, code-parses, diff-well-formed…) behind a chainable `Verifier` Protocol; `run_verifiers`. |
| `commit_window.py` | T008 | The streaming **buffer→verify→commit-or-fallback** window for fail-prone classes (no partial local tokens leak to the harness). |
| `fallback.py`, `decision_log.py` | T009 | **The wedge:** `route_with_fallback` walks the policy's tiers, verifies each, and escalates to cloud on failure, with a **retry cap + per-tier circuit breaker + per-session token budget**; a **metadata-only** decision log (R012: tier ids, verdicts, token *counts* — never prompts/responses/secrets). |
| `decision_log.py` (transparency) | T010 | `served_model` / `response_metadata` / `decision_line` so a routed response **names the tier that actually ran** + the fallback flag, and emits one content-free audit line. |
| `seams.py`, `registry.py` | T011 | The pipeline stages as **typed `Protocol` seams** + a small in-process registry; **failure isolation** (`safe_verify`/`wrap_verifier`): a throwing or hung seam becomes a fallback, never a crash (daemon-thread latency budget). |
| `serve.py` | T012 | `anvil-serving serve --config …` — loads the tier config, binds backends/the multiplexer, starts the front door. |
| `profile_bootstrap.py` | T015 | `--replay` the shadow-eval fixtures into a quality-profile table (live run a separately-labeled step). |
| `calibrate.py`, metrics, serve-fingerprint | T016 | Async calibration sampler (opt-in, redacted) + serve-fingerprint staleness so profile rows re-measure on a model/quant/serve change. |

## OpenClaw beachhead (decoupled from the router core)
- `examples/openclaw/` (T013): `validate.py` (wire-form + fire-cadence) + a logging-only `before_model_resolve` hook + fixtures — the validate-first tooling.
- `plugins/openclaw-anvil-intent-router/` (T014): the **reference** `before_model_resolve` plugin — classifies each turn client-side and emits `modelOverride: "anvil/<preset>"`. **AC2 guaranteed: the router core has zero OpenClaw references.**
- T017/T018: real-routed-traffic validation reframe + the per-work-class promotion decision (`docs/findings/`).

## Follow-ups in this change set
- **Bugfix:** `classify.py` now matches `planning`/`plans` (not just bare `plan`) — planning requests were leaking to the fast-local tier instead of the eval-proven cloud planner (PR #32).
- **Anti-drift:** the Tier-0 keyword taxonomy is now a **single canonical `tier0_keywords.json`** consumed by both `classify.py` and the plugin's `classify.mjs` (byte-identical bundled copy), guarded by `tests/router/test_keyword_parity.py`. (Residual: the plugin's hardcoded *fallback* array — used only if the bundled JSON is unreadable — is not itself drift-tested; low severity.)
- `uv.lock` is now `.gitignore`d (an env-provisioning artifact, not a project file).
- [`OPENCLAW-LIVE-VALIDATION.md`](OPENCLAW-LIVE-VALIDATION.md): the runbook for the one remaining step that needs the live Fakoli-Mini gateway (wire value, fire cadence, `pluginApi` floor).

## Packaging / project
- `requires-python` raised to **>=3.11** (the router uses stdlib `tomllib`; 3.9 is EOL).
- `[tool.setuptools.packages.find]` ships `anvil_serving*` subpackages; `[tool.setuptools.package-data]` ships `tier0_keywords.json`; `dev` extra adds `pytest`; pytest configured (`pythonpath`/`testpaths`).
- Test suite went from 0 → **378 tests** (the router's first suite), all green.

## Verification posture
Every task: implement → 3 independent adversarial verifiers → **`/code-review max`** (10-angle, recall) → fix → squash-merge → `anvil apply`. The recall review caught a real defect on **every** task that the acceptance verifiers passed over (deny-gate-fails-open, log-injection, classifier-drowned-by-tools, a failure-isolation boundary that could itself crash/hang). Stdlib-only runtime preserved throughout.
