---
name: anvil-serving-workbench
description: Operate anvil-serving from OpenClaw using MCP/controller-backed playbooks, CLI fallbacks, and sub-agent evidence packets. Use for router status, model selection, preflight, benchmark probes, OpenClaw sync, promotion evidence, and safe small-model workflow slices.
---

# Anvil Serving Workbench

Use this skill as the OpenClaw-visible entry point for operating anvil-serving.
It mirrors the repo workbench skill used by Codex and Claude Code.

## Rules

- Prefer anvil-serving MCP/controller tools: `router_status`, `router_logs`,
  `router_manage`, `decision_summary`, `router_promote`, `serves_status`,
  `serves_manage`, `serves_logs`, `doctor_summary`, `models_inventory`,
  `route_decision`, `openclaw_sync`, `openclaw_gateway_restart`,
  `preflight_probe`, `benchmark_probe`, and `benchmark_artifact`.
- Use documented `anvil-serving` CLI verbs only when a structured tool is
  missing. Safe fallbacks are read-only or preview-first verbs such as
  `profile`, `models sync`, `models recipe`, `external-bench list/report/compare`,
  `score`, `harness sync openclaw --out -`, and `host doctor`. Return the
  command preview as evidence and name the missing MCP wrapper as a product gap.
- Use `127.0.0.1` for local URLs. Do not use `localhost`.
- Pass credentials by environment variable name only.
- Stop for a human gate before profile promotion, router policy changes,
  metered cloud enablement, destructive cache/host repair, Docker/WSL restart,
  or public/non-loopback bind.
- Treat `router_promote` as preview/validation unless `confirm=true` and
  `human_approved=true` are present. Keep `promoted=false` unless a
  human-approved promotion actually ran.
- Never let the model being evaluated grade its own output.

## Playbooks

- Readiness: inspect router, serves, doctor, model inventory, and configured
  endpoint status.
- Model catalog: read or sync model inventory and mark external benchmarks as
  advisory priors only.
- Serve swap: preview with `serves_manage`, inspect bounded logs, require exact
  target plus `confirm=true` and `dry_run=false`, then run preflight before
  benchmark.
- Harness sync: preview OpenClaw provider, skill, and agent config with
  `openclaw_sync`; apply only to an explicit `out` or `gateway_host` target.
- Promotion evidence: assemble status, decision summaries, route probes,
  preflight, benchmark artifacts, calibration, profile/config diffs, and
  reviewer recommendation with `promoted=false`.

## Roles

Use small/local models for inventory scout, route analyst, preflight runner,
benchmark runner, and evidence reporter. Use a stronger independent critic for
promotion recommendations and adversarial review.

## Packet

Return `schema_version: "operator-workflow/v1"`, `request`, `gate_state`,
`targets`, `tools_used`, `artifacts`, `advisory_priors`, `recommendation`,
`human_gate_required`, and `promoted`. Each `tools_used` entry must include
`source_class`, `ok`, `dry_run`, `confirmed`, `target`, and `error`. Allowed
`gate_state` values are `not_required`, `confirm_required`, `human_required`,
and `blocked`. Allowed recommendations are `promote`, `do_not_promote`,
`needs_more_data`, and `blocked`.
