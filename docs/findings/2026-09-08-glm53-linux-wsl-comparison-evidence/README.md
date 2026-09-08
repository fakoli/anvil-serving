# GLM-5.3-Flash native Linux migration evidence

Campaign `2026-09-08-glm53-linux-wsl-comparison` measures the existing model on
two RTX PRO 6000 Blackwell Max-Q GPUs against retained Windows/WSL evidence.
Launcher revision: `8abcc5dc75a1b2389210b553120abb76e5864d3a`, initially clean
tracked worktree; campaign changes are documentation and evidence only.
The [dated finding](../2026-09-08-glm53-linux-wsl-comparison.md) explains the
bounded `functional`, `capacity` and `quality` results and `no-promotion` decision.

## Campaign controls

- [Artifact manifest](artifact-manifest.json): native schemas, ten roles, exact hashes and bytes.
- [Decision summary](summary.json), [coverage and gaps](coverage-and-gaps.md).
- [Source registry](source-registry.json), [workload manifest](workload-manifest.json), [plan](run-plan.json), [reproduction](reproduction.md).
- [Exact configuration](configuration-and-identity.json), [native contract](native-server-contract.json), [hardware](hardware-observation.json), [launcher](launcher-identity.json).
- [Failures and dispositions](friction-log.md), [restoration](restoration.json), [sanitization](redaction-provenance.json).
- [Campaign state](campaign-state.json), [publication assignment](dispatch-packet.md), [publication summary](publication-summary.md).

## Native measurements

| Population | Retained native evidence | Interpretation |
|---|---|---|
| Direct disabled/off | [Full gate](direct-preflight-disabled.json), [smoke](direct-smoke.json) | General-image exact phrase failed; other nine checks passed |
| Direct enabled/on | [Functional gate](direct-preflight-thinking-enabled.json), [control index](thinking-control.json) | Reasoning evidence present with separate diagnostic budget |
| Historical-style C1 n3 | [4K](linux-legacy-capacity-4k-r3.json), [120K](linux-legacy-capacity-120k-r3.json), [262K](linux-legacy-capacity-262k-r3.json), [380K](linux-legacy-capacity-380k-r3.json) | Warm online direct, shared prefixes, variable short output; descriptive medians |
| Retained WSL C1 n3 | [4K](windows-capacity-4k-r3.json), [120K](windows-capacity-120k-r3.json), [262K](windows-capacity-262k-r3.json), [380K](windows-capacity-380k-r3.json) | Matching original native bytes; no new Windows arm |
| Strict output | [Scout](controlled-scout-4k-r3.json) | 0/3 exact128-word compliance; all performance withheld |
| Unique natural answer | [4K diagnostic](linux-unique-natural-4k-r10.json) | 1/10 first-position canary compliance; dependent depths not run |
| Coding | [Quality](coding-quality.json) | Five deterministic cases x3, 15/15 |
| Images | [Corpus](image-corpus.json) | Six cases x2, 12/12; native multimodal schema retained |
| Endurance | [Linux](endurance-4k-r60.json), [WSL](windows-endurance-4k-r60.json) | 60/60 each; same variable-output/shared-prefix limitations |
| Routed acceptance | [Disabled](routed-preflight-disabled.json), [260K retrieval](routed-needle-260k.json), [enabled](routed-thinking-enabled.json) | 380K estimate rejects413; reasoning projection evidence fails; visible answers otherwise correct |
| Deep agentic | [Native job](agentic/artifact.json), [30 observations](agentic/evidence/0-agentic.json) | 30/30 endpoint-only co-resident cases |
| Extended context | [Native job](context/artifact.json), [summary](context-summary.json) | 128/150; 9 empty and 13 incorrect; default thinking, no Windows control |
| SWE smoke | [Native job](swe/artifact.json), [official grader stage](swe/evidence/2-swe.json), [comparison](swe-comparison.json) | Same one instance resolves1/1; new trajectory is slower and longer |

## Derived views and boundaries

[Comparison data](historical-comparison.json), [prompt equivalence](prompt-equivalence.json),
[graph manifest](graph-manifest.json), [graph data](benchmark-graph-data.json),
and [context comparison chart](benchmark-matrix.svg) trace the historical comparison to native
request artifacts. The separate [endurance chart](endurance-matrix.svg),
[manifest](endurance-graph-manifest.json) and [data](endurance-graph-data.json)
compare the n=60 populations. Both chart packs are embedded in the finding;
repeated graph renders are byte-identical.

Capacity artifacts retain `anvil-serving.benchmark/v1`; durable jobs retain
`anvil-serving.benchmark-evidence/v1`; image evidence retains
`multimodal-benchmark-evidence/v1`. Preflight and coding retain their native
contracts. Failed and partial populations remain visible. Console logs in this
bundle record CLI failures and summaries; native JSON is authoritative.

The migration also changes the driver, transport and cache history. Decode
improvements are whole-stack descriptive results, not an OS-only causal result
or a strict controlled-output qualification. p99 at n3/n60 is descriptive only.
No speculation A/B, video qualification, higher engine concurrency, full SWE
score, model promotion or client-catalog change is claimed.
