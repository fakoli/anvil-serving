# Terminology

This page keeps the public product language consistent across README, docs, config comments, and
release notes.

## Product Naming

| Use | Notes |
|-----|-------|
| `anvil-serving` | Product, package, CLI, and docs site name. Keep lowercase with the hyphen. |
| Quality-gated local-model router | Public category phrase. |
| Coding agents | Preferred public audience phrase. |
| Coding harnesses | Use when discussing Claude Code, Codex, Aider, OpenClaw, and protocol wiring. |
| Local serving tools | User-facing name for profiling, model cataloging, serve management, preflight, benchmark, and host helpers. |

Avoid internal metaphors in public-facing pages. Prefer concrete product terms such as quality
profile, reference integration, product differentiator, and local serving tools.

## Core Concepts

| Term | Definition |
|------|------------|
| Intent preset | A closed vocabulary value sent in the wire `model` field, such as `planning`, `quick-edit`, `review`, `chat`, `chat-fast`, or `long-context`. |
| Work class | The task category used for quality decisions, such as planning, bounded edit, review, or long-context retrieval. |
| Tier | A configured backend candidate. Common tiers are `fast-local`, `heavy-local`, and an explicit cloud tier. |
| Quality profile | The per-tier, per-work-class trust table that decides `allow`, `allow-with-verify`, or `deny`. |
| Structural verification | Cheap inline checks that catch empty content, invalid tool-call JSON, malformed code/diffs, truncation, or refusal markers. |
| Verify-and-fallback | The path that serves a local candidate, verifies it when required, and escalates to the next candidate on failure. |
| Keyless default | The shipped local-only mode. No cloud API key is configured and anvil-serving cannot create metered API spend. |
| Opt-in cloud | A cloud tier explicitly configured by the operator and gated by `[router].metered_cloud`. |
| Serve fingerprint | The measured identity of a tier: model, quantization, engine, and serve flags. A changed fingerprint marks profile rows stale. |

## User-Facing Naming Rules

- Say **router** for the network-facing service that agents call.
- Say **local tier** for a model endpoint the router can call.
- Say **quality profile** for the evidence table.
- Say **intent preset** for `planning`, `quick-edit`, `review`, `chat`, and `long-context`.
- Say **OpenClaw reference integration** when describing the plugin path.
- Say **local serving tools** for `profile`, `models`, `serves`, `preflight`, `benchmark`,
  `external-bench`, `harness`, and `host`.
