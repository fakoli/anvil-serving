---
name: anvil-serving-workbench
description: Operate anvil-serving from OpenClaw using MCP/controller-backed playbooks, CLI fallbacks, and sub-agent evidence packets. Use for router status, model selection, preflight, benchmark probes, OpenClaw sync, promotion evidence, and safe small-model workflow slices.
---

# Anvil Serving Workbench

Use this skill as the OpenClaw-visible entry point for operating anvil-serving.
It mirrors the repo workbench skill used by Codex and Claude Code.

## Rules

- List the current MCP catalog first. Use `operation_contracts` before
  topology-aware or controller-backed work so the selected operation, target
  context, transport, and MCP wrapper agree. Use the complete grouped map below
  instead of relying on a memorized subset.
- Use documented `anvil-serving` CLI verbs only when a structured tool is
  missing. Safe fallbacks are read-only or preview-first verbs such as
  `eval usage`, `models sync`, `models recipes`, `models score`,
  `harness sync openclaw --out -`, and other render/inspect commands. Return
  the command preview as evidence and name the missing MCP wrapper as a product
  gap.
- Use `127.0.0.1` for local URLs. Do not use `localhost`.
- For resource-owned commands, pass the deployed topology and declare the
  actual command host/runtime; do not infer command identity from the target.
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
- Treat `host_summary`, `gpu_inventory`, and `cache_prune_plan` as read-only.
  Use `host_manage` only for an exact reviewed repair after a human gate.
  Report Docker/WSL restart, WSL config edits, and cache deletion as `blocked`
  or `human_required` unless the human approves the existing CLI gate.
- Treat `router_promote` as preview/validation unless `confirm=true` and
  `human_approved=true` are present. Keep `promoted=false` unless a
  human-approved promotion actually ran.
- Treat `serves_promote` as preview/validation unless `confirm=true`,
  `dry_run=false`, and `human_approved=true` are present. Mutating
  `router_transition` actions are also preview-first and human-gated.
- Never let the model being evaluated grade its own output.

## MCP Tool Map

- Router: `router_status`, `router_logs`, `router_manage`,
  `router_transition`, `decision_summary`, `route_decision`, and
  `router_promote`.
- Serves and residency: `serves_status`, `reservation_status`,
  `serves_manage`, `serves_logs`, and `serves_promote`.
- Voice: `voice_manage` and `voice_proxy_manage`.
- Host, models, and telemetry: `doctor_summary`, `host_summary`,
  `gpu_inventory`, `observability_collect`, `host_manage`,
  `models_inventory`, and `cache_prune_plan`.
- Harness: `openclaw_sync`, `openclaw_gateway_status`, and
  `openclaw_gateway_restart`.
- Evaluation and evidence: `preflight_probe`, `benchmark_probe`,
  `benchmark_artifact`, `workflow_packet_validate`,
  `external_bench_sources`, `external_bench_list`, `external_bench_report`,
  and `external_bench_compare`.
- Transport discovery: `operation_contracts`.

`benchmark_probe` and `benchmark_artifact` are bounded capacity tools, not
quality graders. Repeated quality evaluation remains the CLI-only
`anvil-serving eval benchmark quality` workflow until a dedicated wrapper
exists.

## Playbooks

- Readiness: inspect `operation_contracts`, router, serves,
  `reservation_status`, doctor, host/GPU, model inventory, gateway status, and
  configured endpoint status. Use `observability_collect` only for bounded
  declared capabilities.
- Model catalog: read or sync model inventory and use `external_bench_sources`,
  `external_bench_list`, `external_bench_report`, or `external_bench_compare`
  for benchmark priors. Keep those priors advisory-only. Inspect recorded
  configurations with `models recipes list/show`; gate `models pull` on its
  network, disk, and target volume.
- Serve swap: inspect `reservation_status`, preview lifecycle work with
  `serves_manage` or named transactions with `serves_promote`, and inspect
  bounded logs. The role-based recipe flow is
  `anvil-serving serves switch ROLE [MODEL]`; it is CLI-only, so return its
  preview and name the missing MCP wrapper. Require an exact target and the
  documented confirmation gate, then run preflight before benchmarking.
- Voice lifecycle: preview audio with `voice_manage` and the persistent
  Realtime proxy with `voice_proxy_manage`; live lifecycle changes on the
  owning host require `confirm=true` plus `dry_run=false`.
  Profile selection is topology selection: for reference OpenClaw Talk and
  candidate benchmarks, keep Mini model-free and select Dark-host audio or a
  Mini-side proxy to Dark. Use `mini-audio` only when explicitly testing the
  optional same-host/local-audio mode.
- Harness sync: preview OpenClaw provider, skill, and agent config with
  `openclaw_sync`; apply only to an explicit `out` or `gateway_host` target.
- Router operations: use bounded status/log/decision tools and
  `router_transition`; mutations remain preview-first.
- Evaluation: use `preflight_probe` with explicit thinking mode, reasoning
  effort/evidence, visible-answer budget, reasoning headroom, and allowed
  finish reasons. Use the MCP benchmark tools for capacity only. Quality runs
  must use repeated `eval benchmark quality` attempts and preserve visible
  output, reasoning-channel evidence, finish reasons, separate budgets,
  provenance, and per-attempt failure classification.
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
