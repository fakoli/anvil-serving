# Operator skills and sub-agent workflows

Use small models for bounded inspection, manifest parsing, command previews, and report drafting.
Use a stronger independent model for architecture changes, benchmark synthesis, and adversarial
review. Neither class may change a serve, capability alias, or host state without the required human
authorization.

## Codex development models

Trusted checkouts load the project defaults from `.codex/config.toml`: GPT-6
Astra with high reasoning for the lead agent, and GPT-5.6 Terra with medium
reasoning for general subagents. Explicit task selections override these
defaults. The existing MCP registration remains checkout-relative. These are
development settings; Claude Code keeps its separately selected provider.

Named Codex roles are standalone `.codex/agents/*.toml` files with `name`,
`description`, and `developer_instructions`. The former Markdown definitions
are replaced, not retained as a second source of model pins. Their operational
instructions and skill references remain in the role prompts.

| Role | Model | Reasoning |
| --- | --- | --- |
| Lead / orchestrator | `gpt-6-astra` | high |
| General implementation subagent | `gpt-5.6-terra` | medium; high for complex boundaries |
| Inventory scout / evidence reporter | `gpt-5.6-terra` | low |
| Serve operator / preflight runner / benchmark runner | `gpt-5.6-terra` | medium |
| Adversarial reviewer / quality critic | `gpt-5.6-sol` | high |

Review Terra implementation with Astra in a separate session. The named review
roles default to Sol so they can review Astra work. Before dispatch, identify
the implementing model, evidence author, and evaluated model; use a different
reviewer model or human review when any would otherwise grade its own output.
A second session alone does not establish model independence. Review and
inventory roles also request a read-only shell sandbox; their instructions
remain responsible for rejecting mutating MCP operations.

Use these explicit CLI selections when a task needs to override the defaults:

```bash
codex -m gpt-6-astra -c 'model_reasoning_effort="high"'
codex -m gpt-5.6-terra -c 'model_reasoning_effort="medium"'
codex -m gpt-5.6-sol -c 'model_reasoning_effort="high"'
```

In the desktop app, select the corresponding model and reasoning level for the
task. Check effective settings in a fresh session after configuration changes;
an existing task may retain its explicit selection. Project settings require a
trusted checkout. Do not change provider, authentication, permissions, or local
serving configuration merely to adopt a development model.

### Validation and rollback

Validate TOML parsing, custom-role discovery in the installed Codex client,
effective lead/subagent model selection, explicit overrides, and read-only MCP
access. Exercise representative documentation, parser/router, planning, and
review tasks against a recorded baseline. Record independent correctness
checks, unnecessary clarification pauses, test selection, completion time, and
usage; a small pilot establishes compatibility, not a general quality or cost
ranking. Run the repository gates selected by the changed paths.

For rollback, revert the development-model configuration change and reopen the
task, or explicitly select the previous model and reasoning level for that
task. Before this migration, the project had no lead-model pin; record the
effective inherited model before changing defaults. Restoring that state means
restoring inheritance, not assuming every user previously used Sol.

References checked 2026-09-09: [Astra migration and prompting](https://developers.openai.com/api/docs/guides/latest-model),
[Codex configuration](https://learn.chatgpt.com/docs/config-file/config-basic),
and [custom agent schema](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Operator roles

| Role | Inputs | Output | Boundary |
| --- | --- | --- | --- |
| Inventory scout | Router config, serves manifest, status | Current aliases, tiers, and blockers | Read-only. |
| Serve operator | Exact serve and manifest | Preview or confirmed lifecycle result | Requires explicit target and confirmation. |
| Preflight runner | Local endpoint and served model | Functional pass/fail evidence | Benchmark is blocked on failure. |
| Benchmark runner | Preflight proof and measurement shape | Durable artifact and summary | Capacity is not quality proof. |
| Feasibility analyst | Requirements, sourced intervals, hardware and artifact bounds | Pruned candidate matrix plus unresolved-variable ledger | Paper feasibility is permission to test, not qualification. |
| Kernel tuner | Exact runtime, GPU, model geometry, and untuned baseline | Pinned tune plus paired A/B decision | Generated microbenchmark config is not adoption proof. |
| Evidence reporter | Artifacts and config identity | Dated finding with caveats | Does not promote. |
| Adversarial reviewer | Diff, tests, docs, evidence | Severity-ordered findings | Does not implement in the review pass. |
| Human approver | Evidence and rollback plan | Approve or reject promotion | Required for `serves promote` and destructive changes. |

The useful MCP surface is `router_status`, `decision_summary`, `serves_status`,
`serves_manage`, `serves_promote`, `preflight_probe`, `benchmark_probe`,
`benchmark_artifact`, `voice_manage`, `openclaw_sync`, and
`client_catalog_sync`. The request path is intentionally
separate: callers choose a configured capability alias and the gateway proxies it to one local tier.

The canonical voice operations procedure is
`skills/anvil-serving-voice-ops/SKILL.md`. Voice benchmark output is
voice-pipeline evidence; it is not LLM serve qualification evidence,
`promotion_quality_evidence` remains `false`, and the result remains
`promoted=false` until a human-approved serve or capability-alias change.

Multi-sample STT corpus qualification uses
`skills/anvil-serving-stt-benchmark/SKILL.md`. It owns deterministic corpus
preparation, repeated/concurrent WER/CER and latency evidence, restoration
checks, and the dated finding; lifecycle mechanics remain in the CLI and
managed serve manifests.

Pre-benchmark model and runtime pruning uses
`skills/anvil-serving-recipe-feasibility/SKILL.md`. Its deterministic interval
calculator separates physical impossibility, safe-policy failure, measured
failure, unresolved bounds, and benchmark survivors. Unknown runtime, KV,
workspace, quality, and speed values remain named variables and are narrowed
from later managed qualification evidence; no mathematical result promotes a
serve or route.

Hardware-specific MoE/GEMM tuning uses
`skills/anvil-serving-kernel-tuning/SKILL.md`. Repository-owned configs live
under `configs/kernel-tunes/` with an exact compatibility manifest. A tune is
recommended only after identical untuned-versus-tuned functional and
end-to-end performance evidence; storage never activates it.

Coordinated package releases and Mini-to-Dark deployments use
`skills/anvil-serving-release-readiness/SKILL.md`. It joins merged-tree and
artifact gates with manifest-derived container file closure, exact endpoint
version parity, rollback, and real Pi/OpenClaw client smokes. A published
package or healthy single endpoint is not closure while an in-scope outage or
version skew remains.

All LLM, vision, Omni, STT, and TTS publication phases use
`skills/anvil-serving-benchmark-docs/SKILL.md`. The required matrix is:

| Always update | Conditional update |
|---|---|
| Finding + findings index | Archive when recommendation/reference/comparison changes |
| Run catalog | Methodology when workload/evidence contract changes |
| Model dossier | Portal when current/rollback/challenger changes |
| Measured hardware page | |

Classify other GPUs as measured, protected/co-resident, topology-only, or
unrelated. Publication preserves failures and `no-promotion`; it never grants
serve or alias authority.
