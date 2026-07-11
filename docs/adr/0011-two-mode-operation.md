# ADR-0011 — Two modes of operation: agentic vs maximum-flexibility (global mode switch)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Relates to:** ADR-0008 (heavy-tier spec-decode — the agentic-mode workhorse), ADR-0010 (specialized-engine tier — the flexibility-mode backbone), ADR-0009 (measured write-back loop — measures both modes); the captured live agentic config `examples/fakoli-dark/anvil-router.live.toml`.

## Context

anvil runs on Blackwell sm_120 (RTX PRO 6000 96 GB + RTX 5090). Two workloads with **opposite
serving needs** have emerged, and forcing one config to serve both compromises both:

- **Agentic** — multi-turn tool loops with long repeated prefixes, so prefix/radix cache is
  load-bearing. Served today by SGLang (NEXTN spec-decode + radix cache, ADR-0008) with a
  cache-friendly model set (`qwen35-awq`). It works well and must not be disturbed.
- **Non-agentic / maximum flexibility** — single-turn generation where prefix cache is
  **irrelevant**. Dropping the cache constraint un-parks models the batch engines can't cache
  (GDN hybrids such as Qwen3.6-27B-NVFP4 — a *measured* quality win, 0.66 vs 0.43) and makes
  llama.cpp / ktransformers / DS4 first-class engines (ADR-0010). The point is that a new or
  better model is **never gated by the agentic engine's model support** — SGLang is excellent for
  agent work but needs safetensors + day-one architecture support, which blocks fresh models.

The two model sets and serving configs genuinely conflict (cache-on vs cache-irrelevant;
SGLang-tuned vs any-engine). The user's decision is to keep both as **switchable modes**, not to
pick one.

## Decision

Introduce two named **modes of operation**, selected by a **global flag**; the whole router runs
exactly one mode's tiers + model set + serving config at a time (the chosen granularity — one mode
live at a time, switching is a config reload/restart, never per-request):

- `mode = "agentic"` — the existing SGLang configuration, **verbatim and isolated**, with the
  cache-friendly agent model set.
- `mode = "flexibility"` — any-engine specialized tiers (ADR-0010), cache-irrelevant quality
  models (roster produced by the sm_120 reconciliation), llama.cpp/ktransformers/DS4 in play.

The active mode is chosen by a global flag: `ANVIL_MODE` (env) overriding a config `active_mode`
field. Phased so Phase 1 ships with the agentic config literally untouched:

- **Phase 1 (near-zero code, maximal isolation):** two config files — the current agentic config
  stays its own **untouched** file; a new `configs/flexibility.toml`. A thin
  `anvil-serving router run --mode agentic|flexibility` (and `ANVIL_MODE`) resolves to the config path.
  anvil already loads by config path (`ANVIL_CONFIG`), so this is a resolver + docs, no serving-path
  change. Ships both modes immediately.
- **Phase 2 (observability + one source of truth, additive):** stamp the active mode into
  `decision_log`, `metrics`, and the serve **fingerprint** — so the same model measured in agentic
  vs flexibility mode is a *distinct measured identity* and the write-back loop (ADR-0009) grades
  them separately, for free. Optionally support a single config with `[modes.agentic]` /
  `[modes.flexibility]` sections selected by `active_mode`, for a single source of truth.
- **Phase 3 (optional):** per-mode routing policy — e.g. agentic = verify-and-escalate cascade;
  flexibility = quality-first ordering / single-tier — if the modes want different `policy` behaviour.

## Consequences

**Keep:** the agentic SGLang config untouched (its own file); the write-back loop measures both
modes with no extra work (mode ∈ fingerprint); the stdlib-only, urllib-only router hot path; the
RelayBackend serving path.

**Change (additive):** a global mode selector (`ANVIL_MODE` / `active_mode`) and a
`flexibility.toml` populated from the reconciliation roster. No existing tier or preset changes.

**Give up:** only one mode is live at a time (global switch, per the chosen granularity); switching
is a reload/restart, not per-request. If both modes ever need to be live simultaneously from one
gateway, revisit toward a per-request mode selector (a superseding ADR) — the model-field routing
channel already exists to carry it.

**Open / follow-up:** the flexibility roster and its engines depend on the sm_120 reconciliation
(in progress) plus a per-model **preflight** — nothing gates traffic until preflight + a measured
A/B pass (data-driven re-measure; no self-verification).

## Alternatives considered

1. **Per-request mode** (mode prefix on the model field / a header). More capable — both modes live
   at once, no restart — but requires mode-aware clients. Rejected for now per the chosen
   global-switch granularity; recorded as the superseding path if simultaneity is later needed.
2. **Two router instances** (two ports/containers, one per mode; the gateway points at whichever).
   Total isolation, but two deployments to run. Folded into Phase 1 as an operational option: the
   two config files can equally be run as two `serve` processes if hard isolation is preferred.
3. **One merged config, cache-off everywhere.** Rejected: it would degrade the agentic workload
   (which genuinely needs the cache) to suit the flexibility one — the exact compromise the two-mode
   split avoids.
