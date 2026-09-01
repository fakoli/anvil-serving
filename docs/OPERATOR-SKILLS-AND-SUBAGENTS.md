# Operator skills and sub-agent workflows

Use small models for bounded inspection, manifest parsing, command previews, and report drafting.
Use a stronger independent model for architecture changes, benchmark synthesis, and adversarial
review. Neither class may change a serve, capability alias, or host state without the required human
authorization.

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
