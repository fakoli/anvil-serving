![anvil-serving — the quality-gated local-model router for coding harnesses](assets/banner.png)

# anvil-serving

> **The quality-gated local-model router for coding harnesses.**
>
> *Local where it's been proven, cloud where it hasn't — verified, with automatic fallback.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/fakoli/anvil-serving/blob/main/LICENSE)
[![Version](https://img.shields.io/badge/version-0.4.0-blue.svg)](https://github.com/fakoli/anvil-serving/blob/main/CHANGELOG.md)
[![Docs](https://img.shields.io/badge/docs-fakoli.github.io%2Fanvil--serving-blue.svg)](https://fakoli.github.io/anvil-serving/)

Point your coding harness (Claude Code, Codex, Aider, Cline, Continue — OpenClaw as the
near-first-class beachhead) at **one** anvil-serving endpoint. Per request, the router resolves an
**intent** to a **tier** — fast-local, heavy-local, or cloud — using a **measured per-(model,
work-class) quality profile**, cheaply **verifies** the output, and **falls back** to the next
tier (ultimately cloud) when the local answer fails. The harness sees one reliable endpoint and
never silently eats a local-quality failure mid-run.

---

## Why a router, and not just another proxy

Transport is a commodity — LiteLLM, claude-code-router, Ollama, OpenRouter all move tokens.
None of them know **whether local can actually do *this* work.** They route by static rules
(model name, cost, regex). On anvil's real PRD→tasks planning prompt, the gap was measured
directly:

- Local output is **structurally valid ≥92%** of the time — structural validity is **not** the differentiator.
- But on **dependency/ordering reasoning** local collapses: frontier **24.75/25**, fast **16.0**, heavy **13.25** (local ≈ 55–65% of frontier).

A dumb proxy sends that planning request to local and silently corrupts a long agent run. The
defensible asset is therefore **not** the transport — it's the **quality profile** (per model ×
work-class, measured on the operator's own workload) plus the **verify-and-fallback loop.**

---

## Intent presets in the `model` field

Callers declare an **intent** — a closed enum of named presets — instead of a model name:

```
planning   quick-edit   review   chat   long-context
```

Accepted bare (`planning`) or namespaced (`anvil/planning`). Each preset resolves internally to
hard constraints (context length, privacy, tool support, cost ceiling) that *filter* the candidate pool,
plus a quality intent that *ranks* the survivors via the profile. **Filter, then rank.**

---

## Quickstart

```bash
pip install anvil-serving

# copy and edit the local-only example config
cp configs/example.toml ~/.config/anvil-serving/config.toml

# start the router
anvil-serving serve --config ~/.config/anvil-serving/config.toml
```

See [Model Settings Example](MODEL-SETTINGS-EXAMPLE.md) for a full annotated configuration, and
[How it works](QUALITY-GATED-ROUTER.md) for the full design reference.

---

## Navigation

| Section | What's there |
|---------|-------------|
| [How it works](QUALITY-GATED-ROUTER.md) | Full architecture — intents, tiers, quality gate, verify-and-fallback |
| [Model settings](MODEL-SETTINGS-EXAMPLE.md) | Annotated config file with all options |
| [Serves & eval](SERVES-AND-EVAL.md) | Managing model serves + running evals |
| [OpenClaw integration](OPENCLAW-INTEGRATION-SPEC.md) | Plugin spec for the OpenClaw gateway |
| [OpenClaw live validation](OPENCLAW-LIVE-VALIDATION.md) | Validation runbook for OpenClaw |
| [Cost model](PLAN-advise-and-defer.md) | advise-and-defer plan — local-only default, opt-in metered cloud |
| [ADRs](adr/README.md) | Architecture decisions |
| [Changelog](changelog.md) | Release history |

---

## Cloud is off by default

The default config ships **local-only, $0 metered API billing.** Cloud is opt-in and explicit —
you must declare a `CloudBackend` section in your config to unlock it. See
[ADR-0001](adr/0001-cloud-cost-and-subscription-auth.md) and the
[advise-and-defer plan](PLAN-advise-and-defer.md) for the full rationale.
