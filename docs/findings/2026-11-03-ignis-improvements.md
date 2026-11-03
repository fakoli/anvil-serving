# Using Ignis 0.8.0 on an RTX 5090: what worked and what to improve

**Observed:** 2026-11-03. **Scope:** one private candidate on Ignis 0.8.0,
one RTX 5090, Windows/Docker Linux VM, direct online requests.
**Decision:** bounded research evidence; no promotion.

Ignis is the serving runtime, not the model. Our earlier conversational shorthand
called the candidate “Ignis”; this note separates the runtime, the loaded model,
and our evaluation harness. Exact model/variant and asset fingerprints remain
private. The [configuration description](2026-11-03-rtx5090-campaign-evidence/configuration.md)
documents the public controls and reproduction limits.

The useful result was not a universal speed win. Ignis ran the compact candidate
with long-context retrieval, tools and direct images, and a larger reasoning
budget recovered coding correctness. The main improvement opportunity is making
that quality/latency tradeoff and the loaded capability/memory contract easier
for callers to observe.

## How we used it

We used managed Anvil recipes with an explicit context, four decode lanes,
DFlash2 with seven drafts, a 24 GiB VRAM budget and 16 GiB host-memory cap.
The matched 32K budget pair disabled the loop guard and implicit model switching.
The later profile increased context to 262,144, shared KV slots to 524,288 and
enabled vision together. Primary benchmark requests were sequential C1.

Coding and synthetic tool-use enabled reasoning. Retrieval, images and the
short-output capacity diagnostic disabled it. We tested these policies separately;
successful retrieval is not evidence of long-context reasoning.

The [official 0.8.0 release](https://github.com/gpillon/ignis/releases/tag/v0.8.0)
documents runtime profile and adaptation controls. That is
external context, not local performance evidence. This note concerns the tested
release and does not claim these observations apply to newer versions.

## What worked

- **Coding with a 4,096-token reasoning budget:** 123/123 behavioral checks
  and 21/21 whole solutions in the seven-task fixture. Both the 32K profile
  and later 256K vision profile passed; all 21 visible sources matched across
  those two profiles.
- **Sequential retrieval:** the larger profile passed 45/45 synthetic checks
  across three requested input buckets and three positions, reaching
  245,646 actual input tokens. The full 262,144 boundary was not tested.
- **Tool-use:** 30/30 deterministic short-message scenarios passed with
  reasoning enabled. This was not repository repair or a long-context agent.
- **Direct images:** 18/18 required-fact assertions passed across seven
  synthetic images and six case types. Independent review found incidental
  unsupported statements, so this is a required-fact pass rather than complete
  caption fidelity.

See the [campaign finding](2026-11-03-rtx5090-campaign.md),
[all coding records](2026-11-03-rtx5090-campaign-evidence/coding-results.md),
[retrieval](2026-11-03-rtx5090-campaign-evidence/raw/ignis-256k-vision-context-raw.json),
[tool-use](2026-11-03-rtx5090-campaign-evidence/raw/ignis-256k-vision-agentic-raw.json)
and [image artifact](2026-11-03-rtx5090-campaign-evidence/raw/ignis-256k-vision-multimodal.json).

## Priority 1: make reasoning-budget effects observable

| Matched 32K setting | Behavioral checks | Whole solutions | Sum of 21 task latencies |
|---|---:|---:|---:|
| Thinking budget 1,024 | 111/123 | 15/21 | 99.145 s |
| Thinking budget 4,096 | 123/123 | 21/21 | 305.500 s |

The larger budget recovered stable-topological-order and zero-capacity-cache
failures at **3.08× summed task latency**. The two recipes differed functionally
only in that budget. They were separate loads, and the larger setting was a
post-hoc response to the failures. We did not prove that 4,096 is optimal or that
every task needs it.

All attempts in both runs returned visible code with finish reason “stop”.
Retained runtime observations nevertheless reported forced reasoning closure
for 21/21 requests at 1,024 and 18/21 at 4,096. Those observations were associated
by sequential request order, not an explicit client-returned request-ID join.
They are not exact HTTP reasoning-token accounting.

**Proposed runtime improvement:** expose the effective thinking budget, whether
closure was forced, its reason, reasoning usage and a correlatable request ID
in structured request telemetry. If already available elsewhere, document and
test that contract rather than adding a duplicate interface.

**Proposed evaluation improvement:** sweep intermediate budgets on held-out
tasks, score state-invariant failures, and choose an explicit caller policy
from the quality/latency curve. Any adaptive budget policy needs its own tests;
this campaign did not implement or qualify one.

Evidence: [1,024 grade](2026-11-03-rtx5090-campaign-evidence/raw/ignis-budget1024-coding-grade-final.json),
[4,096 grade](2026-11-03-rtx5090-campaign-evidence/raw/ignis-budget4096-coding-grade-final.json),
[1,024 runtime observations](2026-11-03-rtx5090-campaign-evidence/raw/ignis-budget1024-coding-reasoning-observation.json),
[4,096 runtime observations](2026-11-03-rtx5090-campaign-evidence/raw/ignis-budget4096-coding-reasoning-observation.json).

The earlier enabled scout also passed but took 668.108 summed seconds under
different loop-guard/switch and budget controls. Comparing that with 305.500
seconds does **not** isolate a budget or kernel optimization.

## Priority 2: distinguish visible facts from interpretation

The chart answer added “units sold” although that interpretation was unsupported.
A separate expanded preflight caption invented industrial/forging/CNC context
and meaning for a temperature display. These are model-output observations,
not proof of an Ignis kernel or serving bug.

**Proposed model/prompt improvement:** ask for transcription and visible facts
separately from interpretation, avoid supplying unstated units or purpose, and
mark uncertainty. Test the effect with independent factual checks.

**Our harness improvement:** grade contradictions and unsupported additions,
not only required markers. The initial preflight hit a 256-token output cap and
failed globally despite positive markers. A separate 1,024-token rerun passed,
but the longer answer still invented details. Raising the cap alone is not a
faithfulness fix.

Evidence: [failed preflight](2026-11-03-rtx5090-campaign-evidence/raw/ignis-256k-vision-preflight-disabled.json),
[separate rerun](2026-11-03-rtx5090-campaign-evidence/raw/ignis-256k-vision-preflight-disabled-v2.json).
Output text is withheld in this public derivative; these qualitative observations
come from independent private image/output review, not a publicly replayable grade.

## Priority 3: align discoverable capabilities with the loaded service

The vision-enabled service accepted images directly while its
[model-discovery response](2026-11-03-rtx5090-campaign-evidence/raw/ignis-256k-vision-models.json)
reported kind “text”. That label alone does not establish the entire advertised
capability contract, but it did not communicate the demonstrated image path.

**Proposed runtime/integration improvement:** expose unambiguous, documented
loaded input modalities alongside context, output and lane limits. Anvil should
consume a declared protocol/capability contract; it should not guess vision
support from the engine name. Qualify the real routed-client image path afterward.

## Priority 4: expose memory pressure and shared-KV admission separately

The large profile reached its 16 GiB host cap: peak equaled the limit and
the max-event counter was 932, without an observed OOM or kill. Current usage
later fell to roughly 5.9 GiB. This is evidence of transient pressure, not a
demonstrated leak or comfortable host headroom. GPU-free snapshots around
14,044–14,046 MiB were [point observations](2026-11-03-rtx5090-campaign-evidence/resource-observations.json), not sustained minima.

Four configured decode lanes did not prove four concurrent full contexts.
524,288 shared slots / 262,144 configured context gives two nominal sequence
equivalents before other reserves. Long retrieval was sequential.

**Proposed runtime/operations improvement:** document load-time host-memory
requirements, retain peak/event counters, and show shared-KV admission capacity
separately from decode lanes. Next test a bounded concurrent near-boundary
admission workload, with explicit rejection behavior and memory observations.

Evidence: [start status](2026-11-03-rtx5090-campaign-evidence/raw/ignis-256k-vision-start-status.json),
[final status](2026-11-03-rtx5090-campaign-evidence/raw/ignis-256k-vision-final-status.json).

## Priority 5: measure end-to-end latency, not only short decode rate

The separate 32K, reasoning-disabled, 16-word C1 cell passed 120/120 eligible
requests. Its median first-token latency was about 1.114 s and median E2E
about 1.169 s. A high short-decode rate did not make this the fastest response
path among the three different tested configurations.

**Proposed performance work:** measure cold/warm first output, sustained output,
prefill and decode separately under the same checkpoint and controls before
attributing a bottleneck to a kernel. The release's upstream prefill work is
a useful research lead, not proof of the cause of our local latency.

The [native cell](2026-11-03-rtx5090-campaign-evidence/raw/ignis-budget4096-capacity16-120.json)
reports 28 completion tokens. The [public chart](2026-11-03-rtx5090-campaign-evidence/capacity-comparison.svg)
corrects an earlier blanket 29-token annotation. Tokenization, runtime, model and
policies differ across its series; it does not isolate an Ignis speed effect.

## Improvements in our Anvil workflow

Ignis stability needed the explicit v1-tokenize counting protocol. We added
protocol-declared handling to the campaign harness, rather than engine-name
branches or inferred fallback. The 32K profile then passed
[serial](2026-11-03-rtx5090-campaign-evidence/raw/ignis-budget4096-stability-serial.json)
and [overlap](2026-11-03-rtx5090-campaign-evidence/raw/ignis-budget4096-stability-overlap.json)
functional diagnostics, three gates each. They remain performance-ineligible;
client overlap is not observed scheduler concurrency.

The campaign also needed clearer distinction between marker success and semantic
correctness, per-profile evidence binding, and raw-stage versus outer-job status.
Those are responsibilities of the evaluator, not defects to assign to Ignis.

No repository-task, broad vision, video, routed-client, simultaneous full-context
or long-running reliability claim follows. No upstream issue or message was sent
by this publication; these are documented improvement proposals, not fixes
claimed to have shipped.
