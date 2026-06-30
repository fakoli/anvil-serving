# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  hard eval data
  ([`docs/findings/2026-06-28-planning-capability-eval.md`](docs/findings/2026-06-28-planning-capability-eval.md)).
- **The T017 traffic fixture is synthetic.** Traffic-metrics behavior is exercised against a
  synthetic fixture, not yet against real routed production traffic.

[Unreleased]: https://github.com/fakoli/anvil-serving/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/fakoli/anvil-serving/releases/tag/v0.3.0
