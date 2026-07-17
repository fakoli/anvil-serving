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
2. List or inspect available MCP tools first. Use `operation_contracts` before
   topology-aware or controller-backed work so the selected CLI operation,
   target context, transport, and MCP wrapper agree. Use the grouped catalog
   below rather than relying on a memorized subset. If the repo-scoped MCP
   server is unavailable, report that loss of controller coverage explicitly.
3. Use documented `anvil-serving` CLI verbs only when an MCP wrapper is missing.
   Safe fallbacks are read-only or preview-first verbs such as `eval usage`,
   `models sync`, `models recipes`, `models score`,
   `harness sync openclaw --out -`,
   and other render/inspect commands. Return the command preview and mark the
   missing wrapper as a product gap. Before using a CLI fallback, verify that
   it resolves to this checkout or the intended installed version; do not imply
   that an unverified fallback is controller-backed.
4. Use `127.0.0.1` in local URLs. Do not introduce `localhost`.
   For resource-owned commands, pass the deployed topology and declare the
   actual command host/runtime; do not infer command identity from the target.
5. For OpenClaw Talk / Anvil Voice work, record the command host before
   interpreting loopback. Reference deployment: Fakoli Mini runs OpenClaw
   Gateway plus Anvil Voice Realtime/proxy and reserves its 16 GB RAM for
   OpenClaw, Claude Code, and Codex. Do not run STT/TTS/LLM model serves on
   Mini for reference testing. Fakoli Dark owns the router, candidate serves,
   and STT/TTS model endpoints. Prefer `dark-audio` or
   `mini-dark-audio-proxy` for OpenClaw Talk; `mini-audio` is an explicit
   optional same-host/local-audio mode only. A non-gateway checkout failing to
   reach Mini proxy loopback is topology evidence, not proof the live
   Mini/Dark path is down.
6. Pass credentials by environment variable name only. Never place literal keys
   in configs, fixtures, packets, logs, or prompts.

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
quality graders. Repeated quality evaluation remains the explicit
`anvil-serving eval benchmark quality` workflow until a dedicated MCP wrapper
exists.

## Gates

Stop for a human gate before profile promotion, router policy changes, metered
cloud enablement, destructive cache pruning, host repair, Docker/WSL restart,
public or non-loopback bind, or any operation that would persist a new routing
trust decision.

`router_promote` may validate with `validate_only=true`, `dry_run=false`, and
`confirm=true`, or preview with the default `dry_run=true`. Validation executes
an already-local selected container image with network and resource isolation,
so keep its timeout and output bounds explicit.
Live apply requires all three fields:
`confirm=true`, `dry_run=false`, and `human_approved=true`. Skill packets must
keep `promoted=false` unless that human-approved promotion result is present.

`serves_promote` has the same three-part live gate: `confirm=true`,
`dry_run=false`, and `human_approved=true`. `host_manage` and mutating
`router_transition` actions remain human-gated even though their MCP schemas
support preview and confirmation.

Never self-verify. The model that generated a candidate output cannot be the
critic or judge that validates it.

Do not write Anvil `state.db` directly. If Anvil task entry is requested, use
the supported Anvil CLI or MCP path; if it is unavailable, report the blocker.

## Playbook Selection

- Readiness: inspect `operation_contracts`, `router_status`, `serves_status`,
  `reservation_status`, `doctor_summary`, `host_summary`, `gpu_inventory`,
  `models_inventory`, and configured endpoint status. Use
  `observability_collect` only for bounded declared capabilities.
- Model catalog: read or sync model inventory and use `external_bench_sources`,
  `external_bench_list`, `external_bench_report`, or `external_bench_compare`
  for benchmark priors. Apply the model benchmark source-freshness rules below.
  Keep those priors advisory-only. Use `models recipes list/show` to inspect
  recorded configurations. Create or revise candidates through
  `models recipes create/update`, review the rendered recipe, and use
  `models recipes load`
  only with an exact container plus its documented confirmation gate. Use
  `models recipes delete` only for an exact reviewed registry entry. Run
  `models pull` only after an explicit network, disk, and target-volume gate.
- Serve swap: inspect `reservation_status`, then preview lifecycle work with
  `serves_manage` or a named transaction with `serves_promote`. The newer
  role-based recipe flow is `anvil-serving serves switch ROLE [MODEL]`; it is
  CLI-only, so return its preview and name the missing MCP wrapper. Require an
  exact target and the documented confirmation gate before apply, then run
  preflight before either capacity or quality benchmarks.
- Voice lifecycle: preview audio with `voice_manage` and the persistent
  Realtime proxy with `voice_proxy_manage`; live lifecycle changes on the
  owning host require `confirm=true` plus `dry_run=false`.
  Profile selection is topology selection: for reference OpenClaw Talk and
  candidate benchmarks, keep Mini model-free and select Dark-host audio or a
  Mini-side proxy to Dark. Use `mini-audio` only when explicitly testing the
  optional same-host/local-audio mode.
- Harness sync: preview OpenClaw provider, skill, and agent config with
  `openclaw_sync`; apply only to an explicit `out`/`gateway_host` target and
  preserve operator-owned keys.
- Router operations: use `router_status`, bounded `router_logs`,
  `router_manage`, `router_transition`, and `decision_summary`; lifecycle and
  tier-transition mutation is preview-first and live only with `confirm=true`
  plus `dry_run=false`.
- Evaluation: use `preflight_probe` with the model family's declared thinking
  mode, reasoning effort/evidence expectation, visible-answer budget,
  reasoning headroom, and allowed finish reasons. Use `benchmark_probe` or
  `benchmark_artifact` for capacity only. For quality, run
  `anvil-serving eval benchmark quality` with repeated attempts and preserve
  visible output, reasoning-channel evidence, finish reasons, separate visible
  and reasoning budgets, provenance, and per-attempt failure classification.
  Do not rank or promote from an older one-shot score that lacks that evidence.
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
- Host/cache work: use `host_summary`, `gpu_inventory`, and `cache_prune_plan`
  for read-only checks and plans. Use `host_manage` only for an exact reviewed
  repair after its human gate. Report Docker/WSL restart, WSL config edits, and
  cache deletion as `blocked` or `human_required` unless the human approves the
  existing gate; MCP cache pruning is plan-only.

## Model Benchmark Source Freshness

When researching Fast or Heavy tier candidates, include dates in the evidence.
Treat model-serving posts, Reddit recipes, benchmark tables, and issue threads
as time-sensitive.

- Classify each external source by age using the current date:
  - `current`: 0-60 days old.
  - `aging`: 61-120 days old.
  - `stale`: older than 120 days, undated, or about a materially older engine,
    driver, CUDA, quantization, or checkpoint generation.
- Prefer current official sources first: Hugging Face model cards and
  discussions, vendor release notes/blogs, vLLM recipes/docs/issues, SGLang
  recipes/docs/issues, llama.cpp releases/PRs, NVIDIA ModelOpt/TensorRT-LLM/NIM
  notes, and model-family docs from OpenAI, Qwen, Mistral, NVIDIA, Z.ai,
  MiniMax, DeepSeek, Google/Gemma, Meta/Llama, or the model owner.
- Use community sources as recipe discovery, not promotion evidence: Reddit
  (`r/LocalLLaMA` first), Hugging Face discussions, GitHub issues/PRs,
  Millstone inference benchmarks, local-inference-lab/rtx6kpro,
  0xsero/blackwell-gpu-wiki, community LLM inference benchmark snapshots, and
  other hardware-matched writeups.
- Search recent community sources before broad web results. If the best
  hardware-matched post is aging or stale, label it as a historical prior and
  require either an official current source or a local benchmark before it can
  influence a recommendation.
- Record source quality in the report or packet: candidate, URL, published or
  observed date, age class, evidence type (`official`, `benchmark`,
  `community-recipe`, `issue`, `local-result`), hardware/engine relevance, and
  how it affected the decision.
- Do not let stale Reddit/forum evidence justify a shortlist, serve swap, or
  promotion by itself. Local preflight, local benchmarks, and independent
  quality evals remain the decision gates.

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
`workflow_packet_validate` when the MCP/control-plane tool is available. That
validator checks packet shape, gate consistency, evidence scope, and bounded
paths; it does not prove evidence sufficiency or reviewer independence. An
independent critic must still reject promotion when the underlying evidence is
missing or self-generated.
