# Operator skills and sub-agent workflows

Use small models for bounded inspection, manifest parsing, command previews, and report drafting.
Use a stronger independent model for architecture changes, benchmark synthesis, and adversarial
review. Neither class may change a serve, direct alias, or host state without the required human
authorization.

| Role | Inputs | Output | Boundary |
| --- | --- | --- | --- |
| Inventory scout | Router config, serves manifest, status | Current aliases, tiers, and blockers | Read-only. |
| Serve operator | Exact serve and manifest | Preview or confirmed lifecycle result | Requires explicit target and confirmation. |
| Preflight runner | Local endpoint and served model | Functional pass/fail evidence | Benchmark is blocked on failure. |
| Benchmark runner | Preflight proof and measurement shape | Durable artifact and summary | Capacity is not quality proof. |
| Evidence reporter | Artifacts and config identity | Dated finding with caveats | Does not promote. |
| Adversarial reviewer | Diff, tests, docs, evidence | Severity-ordered findings | Does not implement in the review pass. |
| Human approver | Evidence and rollback plan | Approve or reject promotion | Required for `serves promote` and destructive changes. |

The useful MCP surface is `router_status`, `decision_summary`, `serves_status`,
`serves_manage`, `serves_promote`, `preflight_probe`, `benchmark_probe`,
`benchmark_artifact`, `voice_manage`, and `openclaw_sync`. The request path is intentionally
separate: callers choose a configured direct alias and the gateway proxies it to one local tier.

The canonical voice operations procedure is
`skills/anvil-serving-voice-ops/SKILL.md`. Voice benchmark output is
voice-pipeline evidence; it is not LLM serve qualification evidence,
`promotion_quality_evidence` remains `false`, and the result remains
`promoted=false` until a human-approved serve or direct-alias change.
