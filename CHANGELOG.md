# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-07-01

**Wire fidelity + production hardening** — the relay now forwards what the harness actually sent
(tools, tool history, sampling parameters) and streams what the model actually produced (real SSE
deltas, real token counts), with a full-codebase hardening pass behind it.

### Fixed

- **Tools and tool history were silently dropped on relay** (#96) — the headline fix. The relay
  backends rebuilt the upstream body from the flattened `InternalRequest`, which dropped the
  request's `tools` / `tool_choice` and the `tool_use` / `tool_result` conversation history — a
  routed tier could never call a tool and lost its own tool history between turns. New
  `dialects/translate.py` (pure stdlib) translates tool definitions, `tool_choice`, and
  tool-carrying message history between the Anthropic and OpenAI wire shapes;
  `CloudBackend._build_body` forwards same-dialect requests verbatim and translates cross-dialect
  ones (e.g. Claude Code → local vLLM). Tool-free requests build a byte-identical body to before
  (regression-pinned). Verified live: a real 104-tool OpenClaw agent turn now reaches the local
  model and returns a real `tool_calls` response.
- **`relay()` now actually streams** (#98). `resp.read(65536)` on an `http.client` response blocks
  until 64 KB accumulate or EOF, so SSE token deltas were delivered all at once at end-of-stream —
  TTFT equaled full completion time. `read1()` returns per-chunk. The most user-visible fix in the
  hardening pass.
- **Classifier keyword haystack** (#97): only a short (≤150-word) system prompt joins the keyword
  scan — a harness's standing multi-thousand-word system prompt permanently contains
  "plan"/"review"/"edit"/"fix", which multi-matched every request into an ambiguous verdict and
  drowned the actual intent of the last user turn.
- **Public-bind warning is auth-aware** (#97): with `[server].auth_env` configured it notes the
  token gate instead of falsely claiming the endpoint has no authentication.
- **Production hardening bug bash** (#98) — router core: `DecisionLog` is a bounded ring buffer
  (default 10k records; was an unbounded per-request append — a slow leak on a long-running
  router); `RouterConfig.tier()` is O(1); an abandoned circuit-breaker half-open probe no longer
  wedges a tier OPEN forever (probes expire after one cooldown); the fence-scan verifier is linear
  (was O(spans × delimiters) — adversarial many-fence responses cost ~10⁹ comparisons in the
  hot path); front-door keep-alive desync and trailing-slash fixes. Support modules: multiplexer
  swap-path hardening (dead-child detection, checked `docker rm -f`, zombie reaping, OOM-guard
  eviction credit, clean 4xx/5xx) and **loopback bind by default** (was `0.0.0.0` — an
  unauthenticated model-swap endpoint on the LAN); calibrate bounded backpressure
  (`max_pending=64`, drops counted); secrets redaction is component-boundaried (`context_limit`
  no longer destroyed by a substring match on `text`); prices parse-before-cache, atomic writes,
  stale-cache fallback, per-process memo; case-insensitive inferred-preset resolution;
  `PYTHONHASHSEED`-independent fingerprints (set values canonicalized — set-valued serve flags
  re-fingerprint once on upgrade).
- **`policy.Needs.needs_tools` was never populated on the serve path.** `policy.route()` has always
  honored `needs.needs_tools` (excludes `tool_support=false` tiers), but `serve.RoutingBackend`
  never constructed a `Needs` — `route()` was always called with `needs=None`, so a tools-bearing
  request could route to a tier with no tool support (the model would then be unable to call any
  tool it needed). Wired via `dialects.translate.has_tool_artifacts` (#96): both `RoutingBackend.generate`
  and `RoutingBackend.decide` now build a `Needs(needs_tools=...)` from the raw wire body before
  calling `route()`. `Needs.min_context` is deliberately left unwired — the only estimator
  available (`internal.estimate_tokens`) is an explicit word-count approximation, not a real
  tokenizer, and comparing it against a tier's real `context_limit` would be an unsound gate; wiring
  a real per-request context estimate is separate, larger work.
- **Verify: empty-content false-negative on tool-call-only local replies (regression coverage).**
  Live end-to-end testing with a real OpenClaw agent turn reported a local model reply with empty
  text `content` but a populated `tool_calls` being wrongly treated as thinking-budget starvation
  by `NonEmptyContent` and escalated/exhausted to a `503`. Investigation found the router logic was
  already correct on `main` — `NonEmptyContent` (`anvil_serving/router/verify.py`) already passes on
  a non-empty `tool_calls` list even with empty text, and `RoutingBackend._route_with_verify`
  (`anvil_serving/router/serve.py`) already threads a backend's `tool_calls`/`finish_reason` into the
  `ResponseView` via `get_last_structured()` — landed by the structured-field-passthrough work
  (#42/#52), which predates and is included in v0.6.0. A genuinely empty reply (no text AND no
  `tool_calls`) still correctly fails and escalates/defers, per the T004 safety net. Added end-to-end
  front-door regression tests (`tests/router/test_serve_fallback.py`,
  `tests/router/test_serve_verify_fallback.py`) and unit-level edge-case pins
  (`tests/router/test_verify.py`) locking in the tool-call-only-pass / truly-empty-fails contract at
  both the T004 minimal-verify local-"allow" path and the full allow-with-verify chain, since no
  end-to-end coverage previously existed for this shape. If this was observed against a *deployed*
  container, rebuild/redeploy from a commit that includes #42/#52 (any v0.6.0+ build already does).

### Added

- **Measured-profile loading** (#97): `[router].profile_path` loads a measured `profile.json`
  (written by `profile_bootstrap` / eval bootstrap) instead of always routing on the hand-authored
  seed profile. Configured-but-unloadable is a startup `ConfigError` — fail fast, never silently
  fall back to seeds the operator asked to replace.
- **Real usage passthrough** (#97): the relay backends extract the upstream's real `usage` block
  and both dialects render the real token counts when present (word-count estimate remains the
  fallback). Harnesses use these numbers for context management, so the estimated fiction was
  actively misleading.
- **Sampling-field wire fidelity (`top_p` / stop sequences).** `InternalRequest` now carries
  `top_p` and a normalized `stop` (list of strings — OpenAI's string-or-array `stop` form is
  collapsed to a list; Anthropic's `stop_sequences` is native). Both dialects parse them
  (`dialects/openai.py`: `top_p` / `stop`; `dialects/anthropic.py`: `top_p` / `stop_sequences`),
  and `CloudBackend._build_body` (`anvil_serving/router/backends/cloud.py`) forwards them with
  dialect-correct wire names, only when present, so an absent field builds the exact same body as
  before (extends the #96 byte-identical regression pin). Also forwards same-dialect-only
  `top_k` (Anthropic) and `presence_penalty` / `frequency_penalty` (OpenAI) — never invented for a
  translated cross-dialect request. Deliberately NOT forwarded: `logit_bias`, `seed`, `user`,
  `metadata` — provider-account/session-scoped fields (billing attribution, abuse tracking,
  deterministic-replay opt-in), not generation-quality knobs, so passthrough would leak
  caller-side state for little harness value. A tier's `extra_body` (applied last, #97) still
  overrides any of these — documented precedence, now test-pinned.
  Previously a harness sending `top_p` or a stop sequence had it silently dropped: the local/cloud
  model sampled with different parameters than requested.

## [0.6.0] - 2026-07-01

**Router as a service** — the front door is now a containerized, network-facing, **token-authed**
endpoint ([ADR-0004](https://github.com/fakoli/anvil-serving/blob/main/docs/adr/0004-router-as-a-service-containerized-and-authed.md)),
so the serves stay loopback-only behind one authenticated boundary and keep-alive comes from Docker.

### Added

- **Built-in front-door token auth (opt-in).** `[server].auth_env` names the env var (e.g.
  `ANVIL_ROUTER_TOKEN`) holding a shared token; the front door accepts `Authorization: Bearer <t>` or
  `x-api-key: <t>`, compares constant-time (`hmac`), and returns `401` on mismatch. **Off when unset**
  (loopback default unchanged); configured-but-env-unset fails fast. Unauthenticated `GET /healthz`.
- **Repo-root `Dockerfile`** (stdlib-only image, non-root, `HEALTHCHECK` on `/healthz`) and a
  router+serves compose topology: the `router` is the only published, authed service; the serves stay
  loopback-only and are reached by service name. Ships `configs/example-docker.toml`.

### Changed

- **`SECURITY.md`** documents the built-in bearer/`x-api-key` auth (supersedes the old "no built-in
  authentication" note); the raw serves stay loopback/internal behind the router.

## [0.5.0] - 2026-07-01

**Portable-by-default** — out-of-box router correctness and a generated bring-up
([ADR-0003](https://github.com/fakoli/anvil-serving/blob/main/docs/adr/0003-portable-defaults-and-generic-onboarding.md)),
so anvil-serving works generically, not just on the authors' setup.

### Added

- **`anvil-serving init` / `onboard`** — one command detects GPUs and emits a mutually-consistent
  compose + `serves.toml` + router config. **`anvil-serving doctor`** environment preflight. Shared
  `gpus.py` GPU-UUID pinning; `deploy` gains a vLLM engine, loopback-default publish, and serves.toml +
  router-tier emission. Per-tier **`extra_body`** (inject `chat_template_kwargs.enable_thinking=false`
  for thinking-by-default models); configurable **`[router].relay_timeout`**; `/v1/models` served-name
  auto-derive.

### Fixed

- **Shipped example configs 404'd out of the box** (a local tier without `model=` forwarded the preset
  token upstream) — `model=` is now required and warned. **verify-on-local-`allow`** catches an
  empty/truncated local `200` instead of delivering it. README states Python ≥3.11 + a pipx recipe;
  the OpenClaw plugin install uses `--link`.

## [0.4.1] - 2026-06-30

Serving-substrate hardening: model serves are now Docker-Compose-defined and `serves up`
is drift-safe, plus Blackwell sm_120 serving guidance. No router changes; no breaking
changes.

### Changed

- **Model serves are Docker-Compose-defined ([ADR-0002](https://github.com/fakoli/anvil-serving/blob/main/docs/adr/0002-serves-are-compose-defined.md)).**
  `anvil-serving serves up` delegates to `docker compose up -d <service>`, which recreates a
  container when its compose config has drifted and fast-restarts it when unchanged —
  replacing a blind `docker start` that could silently serve a stale model. Added a
  parametrized experiment-harness compose (`examples/fakoli-dark/docker-compose.experiment.yml`).
  **Docker Compose v2 is now a serving-substrate prerequisite** (the router itself stays stdlib-only).
- `serves up` gained a `--recreate` flag (force `docker rm -f` + up) and a served-vs-declared
  model drift warning for script-based serves.
- Serve ports bind `127.0.0.1` only; GPU pinning uses `CUDA_VISIBLE_DEVICES` (reliable on
  Docker-Desktop/WSL2) alongside Compose `device_ids`.

### Docs

- Blackwell **sm_120** serving gotchas (dense NVFP4 vs the MoE-NVFP4/block-FP8 kernel gaps,
  NVFP4≈1.8×FP8, the `VLLM_USE_V2_MODEL_RUNNER=0` UVA fix, the docker-volume vs 9P load path)
  in `CLAUDE.md`; ADR-0002.

## [0.4.0] - 2026-06-30

Advise-and-defer — the subscription-first routing pivot — plus the launch-hardening pass.
anvil is now **local-serve + routing brain**: the harness owns cloud on its subscription and
no cloud API key sits in the default path ($0 metered API by default). This release also closes
the six post-launch hardening issues (#42, #45, #46, #47, #52, #53).

### Changed

- **Cloud tier is now opt-in, OFF by default.** `configs/example.toml` ships as
  local-only; anvil holds no cloud API key and incurs **$0 metered API billing** in the
  default configuration. A cloud tier must be explicitly declared in
  `configs/example-with-cloud.toml` to unlock it.
- **Keyless exhaustion handoff replaces mid-request cloud escalation (default path).**
  When all local candidates are exhausted (verify-failure on an `allow-with-verify` class
  with no cloud tier configured), anvil returns an **`exhaustion_status`** (503 by
  default, configurable) with nothing streamed. A gateway like OpenClaw treats this as a
  transport failure and re-routes the request on its native subscription provider —
  flat-rate, not metered by anvil. The opt-in keyed `CloudBackend` path still works for
  single-endpoint harnesses that cannot route cloud themselves.
- **Contract C4 reshaped into two explicit modes** — *keyless* (exhaustion-503 → gateway
  transport failover) and *opt-in keyed* (router-internal escalation → 200). Documented
  in `docs/QUALITY-GATED-ROUTER.md` and `docs/PLAN-advise-and-defer.md`.
- **Docs and visual assets refreshed** to reflect advise-and-defer terminology (local-only
  default, opt-in metered cloud, keyless handoff, $0-metered framing). Internal
  design/planning/findings documents relocated to the private companion repo
  `fakoli/anvil-serving-notes`; public docs retain the product-facing surface.
- **Internal maintainability (#46).** `RelayBackend` decoupled into the backends package;
  dialect/privacy magic strings replaced with named constants; a dialect parity test pins both
  dialects' surface. Behavior-preserving — no wire change.

### Added

- **Per-intent `metered_cloud` gate.** When a cloud tier *is* configured, no work-class
  is eligible for it unless explicitly listed in `[router].metered_cloud`. No implicit
  global "use cloud" switch exists.
- **Cost dimension.** A configured cloud tier carries `cost_input_per_mtok` /
  `cost_output_per_mtok` fields (USD per million tokens). Estimated cost is surfaced in
  the decision log and a `cost_usd` metric on every metered cloud route; local tiers
  report `0`.
- **Optional off-by-default cost-sync.** A `[router] cost_sync = true` toggle fetches
  prices from the free, MIT-licensed LiteLLM pricing JSON (cached at
  `~/.cache/anvil-serving/prices.json`, 24 h TTL, stdlib `urllib` only). Static config
  is the default; sync is opt-in. Falls back to static config on any fetch failure.
- **Configurable `exhaustion_status`.** The HTTP status anvil returns when all local tiers
  are exhausted is configurable (default 503) so operators can tune the gateway-failover
  trigger to their gateway's classification.
- **`POST /v1/route` — the routing-brain endpoint.** Exposes the intent-resolve + routing
  decision without serving the request. Request: a `completions`-shaped body plus optional
  `signals` (`work_class`, `token_estimate`, `urgency`). Response:
  `{ tier, model, provider, work_class, reason, confidence, session_id }`. Status 200
  (decision, even if `cloud`), 400 (malformed), 503 (no suitable tier). Used by the
  OpenClaw plugin for upfront routing splits.
- **OpenClaw plugin upfront routing split.** The `before_model_resolve` hook in
  `plugins/openclaw-anvil-intent-router/` now routes `deny`-class and cloud-destined
  work directly to the gateway's native provider (bypassing anvil entirely), and routes
  `allow` / `allow-with-verify` classes through anvil. Uses the shared
  `tier0_keywords.json` classifier vocabulary; optionally calls `/v1/route` for the
  authoritative decision.
- **Tool-call passthrough + live structured verifiers (#42, #52).** `tool_calls` / `tool_use`
  and the real `finish_reason` / `stop_reason` now flow through the backends, dialects, and
  verifiers (streaming and non-streaming) — a coding harness's tool-calling turn is preserved
  end-to-end, and the `NotTruncated` / `ToolCallJSONValid` verifiers run live on the serve path
  (previously inert). The text path is byte-identical.

### Fixed

- **Fallback-path hardening (#45, #52).** Seam isolation (a hung verifier is bounded by a
  latency budget; a raising observer/log or response-view factory can no longer crash a served
  request), 32 MiB drain byte-caps (local + cloud) against runaway responses, and a
  **session-scoped, thread-safe circuit breaker with cooldown + half-open decay** so a transient
  blip can't permanently disable a tier.
- **Front-door HTTP polish (#53).** A `GET` to a POST-only route returns `405` + `Allow: POST`
  (not `404`); a bounded non-blocking drain after a `413` avoids a connection-reset race;
  `do_GET` body-handling keeps the socket in sync.
- **Concurrency + correctness hygiene (#47).** `DecisionLog` is guarded by a lock (it is written
  from `ThreadingHTTPServer` request threads); a structurally-malformed cloud response now
  surfaces a sanitized error instead of being masked as an empty completion.
- **`benchmark` context-clamp + `--no-thinking` (#78).** Right-sizes the replayed request
  distribution and avoids thinking-budget starvation during benchmarks.

## [0.3.0] - 2026-06-30

First public release. anvil-serving is now a **quality-gated local-model router for coding
harnesses**: point a harness (Claude Code via `ANTHROPIC_BASE_URL`, or any OpenAI/Anthropic
client) at one endpoint; per request it resolves an **intent** to a **tier** (fast-local /
heavy-local / cloud), cheaply **verifies** the output, and **falls back** up the tier chain on
failure — never silently shipping a local-quality miss. stdlib-only, Python >= 3.11.

The `harness-router` PRD (all 18 tasks, milestones M0–M3) landed in this release.

### Added

- **Protocol-standard front door** — accepts both the Anthropic Messages and OpenAI Chat
  Completions dialects on one endpoint, including SSE streaming, and normalizes them onto a
  single internal request shape.
- **Intent routing** — named-preset intents (`planning`, `quick-edit`, `review`, `chat`,
  `long-context`) carried in the `model` field, accepted bare or `anvil/`-namespaced, resolving
  to `(model, tier, params)`; a `model:`-pin escape hatch for repro/debugging.
- **Tier-0 work-class classifier** — the universal floor: infers a work-class from the raw
  payload (token count, `thinking` flag, tool types, image content, system-prompt fingerprint)
  for requests that arrive with no declared intent. Vocabulary ships as the `tier0_keywords.json`
  package-data.
- **`/v1/models` discovery** — advertises the preset vocabulary so intents surface in harness
  model pickers.
- **Tier-topology config schema** — TOML config declaring tiers, per-tier backends, presets, and
  a `mapping_version`; loaded with stdlib `tomllib`.
- **Quality profile + residency-aware routing policy** — a `(model, work-class) ->
  {quality_score, sample_n, last_measured, decision}` table (`allow` / `allow-with-verify` /
  `deny`) keyed on a serve fingerprint (model + quant + engine + serve flags); policy filters by
  hard constraints (including privacy / local-only residency) then ranks the survivors.
- **Cloud-tier credentials on the Backend seam** — Anthropic and OpenAI cloud backends with
  credentials referenced by env-var name, plus **secrets redaction** so keys never reach logs or
  the decision record.
- **Cheap structural verify** — near-zero-cost inline checks (empty/truncated content, tool-call
  JSON that does not validate, code that does not parse, a diff that does not apply).
- **Streaming commit-window + verify-gated fallback + decision log** — for fail-prone classes on
  the streaming path, a non-streamed commit window buffers and verifies before the first byte
  reaches the harness; on verify-fail / error / timeout / low-confidence the router retries up the
  tier chain (fast → heavy → cloud) with retry caps and a per-session cost budget; every decision
  is logged transparently (the response reports the *real* tier that served).
- **Typed extension seams** — Backend / verifier / policy extension points for adding tiers,
  engines, and checks without forking the core.
- **`anvil-serving serve --config ...` CLI** — starts the front door bound to the tiers declared
  in a router config; binds `127.0.0.1` by default.
- **Profile bootstrap + async calibration + traffic metrics + per-work-class promotion** —
  bootstrap the quality table from the generalized shadow-eval, opt-in async calibration with
  serve-fingerprint staleness, real-traffic metrics, and a per-work-class promotion decision
  (planning/critic stay cloud-default, failover-only).
- **OpenClaw tooling + reference adapter** — validate-first tooling (wire-form + firing-cadence
  validator, logging hook, fixture) and a thin, swappable `before_model_resolve` reference adapter
  plugin. The core stays zero-OpenClaw-coupling.

### Known limitations

- **OpenClaw live validation is manual.** Validating the integration against a real OpenClaw
  install (firing cadence and outbound wire `model` form) requires a human on the gateway box; see
  [`examples/openclaw/README.md`](https://github.com/fakoli/anvil-serving/blob/main/examples/openclaw/README.md). The committed `hook-fire-log.jsonl`
  is a representative fixture, not a live capture.
- **Most promotion verdicts are seed/expected.** Per-work-class promotion decisions in the
  shipped profile are hand-seeded and pending real-traffic calibration; only `planning` rests on
  hard eval data (in the companion notes repo `fakoli/anvil-serving-notes`).
- **The T017 traffic fixture is synthetic.** Traffic-metrics behavior is exercised against a
  synthetic fixture, not yet against real routed production traffic.

[Unreleased]: https://github.com/fakoli/anvil-serving/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/fakoli/anvil-serving/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/fakoli/anvil-serving/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/fakoli/anvil-serving/releases/tag/v0.3.0
