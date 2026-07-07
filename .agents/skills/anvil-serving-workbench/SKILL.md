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
   `serves_status`, `serves_manage`, `serves_logs`, `voice_manage`,
   `doctor_summary`, `host_summary`, `models_inventory`, `cache_prune_plan`,
   `route_decision`, `openclaw_sync`, `openclaw_gateway_restart`,
   `preflight_probe`, `benchmark_probe`, and
   `benchmark_artifact`, `external_bench_sources`, `external_bench_list`,
   `external_bench_report`, and `external_bench_compare` when they cover the
   request. Use
   `workflow_packet_validate` before treating a packet as promotion evidence.
3. Use documented `anvil-serving` CLI verbs only when an MCP wrapper is missing.
   Safe fallbacks are read-only or preview-first verbs such as `profile`,
   `models sync`, `models recipe`, `score`, `harness sync openclaw --out -`,
   and other render/inspect commands. Return the command preview and mark the
   missing wrapper as a product gap.
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
- Model catalog: read or sync model inventory and use `external_bench_sources`,
  `external_bench_list`, `external_bench_report`, or `external_bench_compare`
  for benchmark priors. Keep those priors advisory-only.
- Serve swap: preview with `serves_manage`, inspect `serves_logs`, require exact
  target plus `confirm=true` and `dry_run=false`, then run preflight before
  benchmark.
- Voice lifecycle: preview with `voice_manage`; live native or managed STT/TTS
  start/stop on the owning host requires `confirm=true` plus `dry_run=false`.
- Harness sync: preview OpenClaw provider, skill, and agent config with
  `openclaw_sync`; apply only to an explicit `out`/`gateway_host` target and
  preserve operator-owned keys.
- Router operations: use `router_status`, bounded `router_logs`,
  `router_manage`, and `decision_summary`; lifecycle mutation is preview-first
  and live only with `confirm=true` plus `dry_run=false`.
- Promotion evidence: assemble status, decision summary, route probes,
  preflight, benchmark artifacts, calibration, profile/config diffs, and
  reviewer recommendation with `promoted=false`.
- OpenClaw COLO smoke/eval: when validating the Mini-to-Dark path or gathering
  release/blog evidence, use `examples/openclaw/colo_smoke.py --live`; add
  `--run-generations --run-interaction-benchmark` for repeatable intent stats.
  Preserve the artifact's `interaction_benchmarks` and recipe fields. Treat
  generation caps, exact/stream benchmark caps, benchmark reasoning effort, and
  per-intent overrides as router tier `params` owned by the model recipe, not
  as skill or plugin constants.
- Host/cache work: use `host_summary` and `cache_prune_plan` for read-only
  checks and plans. Report host repair, Docker/WSL restart, WSL config edits,
  and cache deletion as `blocked` or `human_required` unless the human approves
  the existing CLI gate; MCP cache pruning is plan-only.

## Sub-Agents

Use small/local models for inventory scout, route analyst, preflight runner,
benchmark runner, and evidence reporter roles. Use a stronger independent model
for quality critic or adversarial review. Keep role outputs bounded: facts,
tool calls, artifacts, recommendations, and blockers.

## Result Packet

Return a packet with this shape:

```json
{
  "schema_version": "operator-workflow/v1",
  "request": "preflight and benchmark fast tier",
  "gate_state": "human_required",
  "targets": {
    "endpoint": "http://127.0.0.1:30001/v1",
    "model": "fast-local"
  },
  "tools_used": [
    {
      "name": "preflight_probe",
      "source_class": "mcp",
      "ok": true,
      "dry_run": false,
      "confirmed": true,
      "target": "http://127.0.0.1:30001/v1",
      "error": null
    }
  ],
  "artifacts": [],
  "advisory_priors": [],
  "recommendation": "needs_more_data",
  "human_gate_required": true,
  "promoted": false
}
```

Allowed `gate_state` values are `not_required`, `confirm_required`,
`human_required`, and `blocked`. Allowed `recommendation` values are `promote`,
`do_not_promote`, `needs_more_data`, and `blocked`. Validate final packets with
`workflow_packet_validate` when the MCP/control-plane tool is available.
