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
  `serves_manage`, `serves_logs`, `voice_manage`, `doctor_summary`,
  `host_summary`, `models_inventory`, `cache_prune_plan`, `route_decision`,
  `openclaw_sync`, `openclaw_gateway_restart`,
  `preflight_probe`, `benchmark_probe`, `benchmark_artifact`,
  `external_bench_sources`, `external_bench_list`, `external_bench_report`, and
  `external_bench_compare`. Use
  `workflow_packet_validate` before treating a packet as promotion evidence.
- Use documented `anvil-serving` CLI verbs only when a structured tool is
  missing. Safe fallbacks are read-only or preview-first verbs such as
  `profile`, `models sync`, `models recipe`, `score`,
  `harness sync openclaw --out -`, and other render/inspect commands. Return
  the command preview as evidence and name the missing MCP wrapper as a product
  gap.
- Use `127.0.0.1` for local URLs. Do not use `localhost`.
- For OpenClaw Talk / Anvil Voice work, record the command host before
  interpreting loopback. Reference deployment: Fakoli Mini runs OpenClaw
  Gateway plus Anvil Voice Realtime/proxy and reserves its 16 GB RAM for
  OpenClaw, Claude Code, and Codex. Do not run STT/TTS/LLM model serves on Mini
  for reference testing. Fakoli Dark owns the router, candidate serves, and
  STT/TTS model endpoints. Prefer `dark-audio` or `mini-dark-audio-proxy` for
  OpenClaw Talk; `mini-audio` is an explicit optional same-host/local-audio
  mode only. A non-gateway checkout failing to reach Mini proxy loopback is
  topology evidence, not proof the live Mini/Dark path is down.
- Pass credentials by environment variable name only.
- Stop for a human gate before profile promotion, router policy changes,
  metered cloud enablement, destructive cache/host repair, Docker/WSL restart,
  or public/non-loopback bind.
- Treat `host_summary` and `cache_prune_plan` as read-only. Report host repair,
  Docker/WSL restart, WSL config edits, and cache deletion as `blocked` or
  `human_required` unless the human approves the existing CLI gate.
- Treat `router_promote` as preview/validation unless `confirm=true` and
  `human_approved=true` are present. Keep `promoted=false` unless a
  human-approved promotion actually ran.
- Never let the model being evaluated grade its own output.

## Playbooks

- Readiness: inspect router, serves, doctor, model inventory, and configured
  endpoint status.
- Model catalog: read or sync model inventory and use `external_bench_sources`,
  `external_bench_list`, `external_bench_report`, or `external_bench_compare`
  for benchmark priors. Keep those priors advisory-only.
- Serve swap: preview with `serves_manage`, inspect bounded logs, require exact
  target plus `confirm=true` and `dry_run=false`, then run preflight before
  benchmark.
- Voice lifecycle: preview with `voice_manage`; live native or managed STT/TTS
  start/stop on the owning host requires `confirm=true` plus `dry_run=false`.
  Profile selection is topology selection: for reference OpenClaw Talk and
  candidate benchmarks, keep Mini model-free and select Dark-host audio or a
  Mini-side proxy to Dark. Use `mini-audio` only when explicitly testing the
  optional same-host/local-audio mode.
- Harness sync: preview OpenClaw provider, skill, and agent config with
  `openclaw_sync`; apply only to an explicit `out` or `gateway_host` target.
- Promotion evidence: assemble status, decision summaries, route probes,
  preflight, benchmark artifacts, calibration, profile/config diffs, and
  reviewer recommendation with `promoted=false`.
- OpenClaw COLO smoke/eval: for Mini-to-Dark validation or release/blog stats,
  use `examples/openclaw/colo_smoke.py --live`; add `--run-generations
  --run-interaction-benchmark` for repeatable intent stats. Preserve
  `interaction_benchmarks` and recipe fields. Treat generation caps,
  exact/stream benchmark caps, benchmark reasoning effort, and per-intent
  overrides as router tier `params` owned by the model recipe, not as skill or
  plugin constants.
- Host/cache work: collect `host_summary` and `cache_prune_plan`; MCP cache
  pruning is plan-only and must not delete.

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
`needs_more_data`, and `blocked`. Validate final packets with
`workflow_packet_validate` when the MCP/control-plane tool is available.
