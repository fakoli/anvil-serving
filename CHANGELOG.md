# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet._

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
  [`examples/openclaw/README.md`](examples/openclaw/README.md). The committed `hook-fire-log.jsonl`
  is a representative fixture, not a live capture.
- **Most promotion verdicts are seed/expected.** Per-work-class promotion decisions in the
  shipped profile are hand-seeded and pending real-traffic calibration; only `planning` rests on
  hard eval data (in the companion notes repo `fakoli/anvil-serving-notes`).
- **The T017 traffic fixture is synthetic.** Traffic-metrics behavior is exercised against a
  synthetic fixture, not yet against real routed production traffic.

[Unreleased]: https://github.com/fakoli/anvil-serving/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/fakoli/anvil-serving/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/fakoli/anvil-serving/releases/tag/v0.3.0
