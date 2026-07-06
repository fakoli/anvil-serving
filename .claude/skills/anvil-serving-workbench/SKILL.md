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
   `serves_status`, `doctor_summary`, `route_decision`, `openclaw_sync`,
   `openclaw_gateway_restart`, `preflight_probe`, and `benchmark_probe` when
   they cover the request.
3. Use documented `anvil-serving` CLI verbs only when an MCP wrapper is missing.
   Return the command preview and mark the missing wrapper as a product gap.
4. Use `127.0.0.1` in local URLs. Do not introduce `localhost`.
5. Pass credentials by environment variable name only. Never place literal keys
   in configs, fixtures, packets, logs, or prompts.

## Gates

Stop for a human gate before profile promotion, router policy changes, metered
cloud enablement, destructive cache pruning, host repair, Docker/WSL restart,
public or non-loopback bind, or any operation that would persist a new routing
trust decision.

Never self-verify. The model that generated a candidate output cannot be the
critic or judge that validates it.

Do not write Anvil `state.db` directly. If Anvil task entry is requested, use
the supported Anvil CLI or MCP path; if it is unavailable, report the blocker.

## Playbook Selection

- Readiness: inspect router, serves, doctor, and configured endpoint status.
- Model catalog: read or sync model inventory and mark external benchmarks as
  advisory priors only.
- Serve swap: preview the serve operation, require exact target and confirm,
  then run preflight before benchmark.
- Harness sync: preview OpenClaw config/skill changes before apply and preserve
  operator-owned keys.
- Promotion evidence: assemble status, preflight, benchmark, calibration,
  profile/config diffs, and reviewer recommendation with `promoted=false`.
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
`needs_more_data`, and `blocked`.
