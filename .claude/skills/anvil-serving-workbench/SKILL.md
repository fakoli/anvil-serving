---
name: anvil-serving-workbench
description: Operate anvil-serving from an agent harness using MCP/controller-backed playbooks, CLI fallbacks, and sub-agent evidence packets. Use when asked to inspect Anvil Serving status, choose models, run preflight or benchmarks, sync OpenClaw/Codex/Claude Code workbench config, collect promotion evidence, or coordinate small-model sub-agents for anvil-serving operations.
---

# Anvil Serving Workbench

Use this skill as the safe entry point for operating anvil-serving from a large
language model workbench. It chooses documented playbooks, prefers structured
MCP/controller tools, and returns auditable evidence instead of ad hoc shell
narration.

## Start Here

1. Read `README.md`, `CLAUDE.md`, and `docs/OPERATOR-SKILLS-AND-SUBAGENTS.md`
   before changing behavior or running operational commands.
2. List or inspect available MCP tools first. Prefer `router_status`,
   `router_logs`, `router_manage`, `decision_summary`, `router_promote`,
   `serves_status`, `serves_manage`, `serves_logs`, `doctor_summary`,
   `models_inventory`, `route_decision`, `openclaw_sync`,
   `openclaw_gateway_restart`, `preflight_probe`, `benchmark_probe`, and
   `benchmark_artifact` when they cover the request. Use
   `workflow_packet_validate` before treating a packet as promotion evidence.
3. Use documented `anvil-serving` CLI verbs only when an MCP wrapper is missing.
   Safe fallbacks are read-only or preview-first verbs such as `profile`,
   `models sync`, `models recipe`, `external-bench list/report/compare`,
   `score`, `harness sync openclaw --out -`, and `host doctor`. Return the
   command preview and mark the missing wrapper as a product gap.
4. Use `127.0.0.1` in local URLs. Do not introduce `localhost`.
5. Pass credentials by environment variable name only. Never place literal keys
   in configs, fixtures, packets, logs, or prompts.

## Gates

Stop for a human gate before profile promotion, router policy changes, metered
cloud enablement, destructive cache pruning, host repair, Docker/WSL restart,
public or non-loopback bind, or any operation that would persist a new routing
trust decision.

`router_promote` may validate and preview candidate profile/config changes, but
live apply requires `confirm=true` and `human_approved=true`. Skill packets must
keep `promoted=false` unless that human-approved promotion result is present.

Never self-verify. The model that generated a candidate output cannot be the
critic or judge that validates it.

Do not write Anvil `state.db` directly. If Anvil task entry is requested, use
the supported Anvil CLI or MCP path; if it is unavailable, report the blocker.

## Playbook Selection

- Readiness: inspect `router_status`, `serves_status`, `doctor_summary`,
  `models_inventory`, and configured endpoint status.
- Model catalog: read or sync model inventory and mark external benchmarks as
  advisory priors only.
- Serve swap: preview with `serves_manage`, inspect `serves_logs`, require exact
  target plus `confirm=true` and `dry_run=false`, then run preflight before
  benchmark.
- Harness sync: preview OpenClaw provider, skill, and agent config with
  `openclaw_sync`; apply only to an explicit `out`/`gateway_host` target and
  preserve operator-owned keys.
- Router operations: use `router_status`, bounded `router_logs`,
  `router_manage`, and `decision_summary`; lifecycle mutation is preview-first
  and live only with `confirm=true` plus `dry_run=false`.
- Promotion evidence: assemble status, decision summary, route probes,
  preflight, benchmark artifacts, calibration, profile/config diffs, and
  reviewer recommendation with `promoted=false`.
- Host repair: diagnose and preview only unless the human approves disruptive
  repair through the documented CLI.

## Sub-Agents

Use small/local models for inventory scout, route analyst, preflight runner,
benchmark runner, and evidence reporter roles. Use a stronger independent model
for quality critic or adversarial review. Keep role outputs bounded: facts,
tool calls, artifacts, recommendations, and blockers.

## Result Packet

Return an `operator-workflow/v1` packet with `schema_version`, `request`,
`gate_state`, `targets`, `tools_used`, `artifacts`, `advisory_priors`,
`recommendation`, `human_gate_required`, and `promoted`. Each `tools_used`
entry must include `source_class`, `ok`, `dry_run`, `confirmed`, `target`, and
`error`. Allowed recommendations are `promote`, `do_not_promote`,
`needs_more_data`, and `blocked`. Allowed `gate_state` values are
`not_required`, `confirm_required`, `human_required`, and `blocked`. Validate
final packets with `workflow_packet_validate` when the MCP/control-plane tool is
available.
