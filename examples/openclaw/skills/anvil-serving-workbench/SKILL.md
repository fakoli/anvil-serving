---
name: anvil-serving-workbench
description: Operate anvil-serving from OpenClaw using MCP/controller-backed playbooks, CLI fallbacks, and sub-agent evidence packets. Use for router status, model selection, preflight, benchmark probes, OpenClaw sync, promotion evidence, and safe small-model workflow slices.
---

# Anvil Serving Workbench

Use this skill as the OpenClaw-visible entry point for operating anvil-serving.
It mirrors the repo workbench skill used by Codex and Claude Code.

## Rules

- Prefer anvil-serving MCP/controller tools for status, route probes, preflight,
  bounded benchmarks, OpenClaw sync, and gateway restart.
- Use documented `anvil-serving` CLI verbs only when a structured tool is
  missing, and return the command preview as evidence.
- Use `127.0.0.1` for local URLs. Do not use `localhost`.
- Pass credentials by environment variable name only.
- Stop for a human gate before profile promotion, router policy changes,
  metered cloud enablement, destructive cache/host repair, Docker/WSL restart,
  or public/non-loopback bind.
- Never let the model being evaluated grade its own output.

## Roles

Use small/local models for inventory scout, route analyst, preflight runner,
benchmark runner, and evidence reporter. Use a stronger independent critic for
promotion recommendations and adversarial review.

## Packet

Return `schema_version: "operator-workflow/v1"`, `request`, `gate_state`,
`targets`, `tools_used`, `artifacts`, `advisory_priors`, `recommendation`,
`human_gate_required`, and `promoted`. Each `tools_used` entry must include
`source_class`, `ok`, `dry_run`, `confirmed`, `target`, and `error`. Keep
`promoted=false` unless a human-approved promotion actually ran.
