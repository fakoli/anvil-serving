# TensorFold reasoning-budget investigation

**Date:** 2026-11-09

**Scope:** A focused investigation of the selected Go `alphametics` task on
the pinned TensorFold GLM EXL3 configuration. The initial plan changes only
request-local reasoning effort within the same 16,384-token response cap.

**Decision:** Focused `quality` evidence, `no-promotion`. Low passed both
selected-task repetitions; Max failed both after spending the shared response
allowance in reported reasoning. The numeric-control patch remains a source
proposal, separate from deployed behavior.

<!-- benchmark-result-card/v1 -->
## Result card

> On this pinned TensorFold GLM EXL3 setup, Low effort passed the selected Go
> task twice within the unchanged 16K response cap; both Max repetitions
> exhausted that cap before producing an editable answer. Effort is a prompt
> instruction, and no positive hard thinking budget was configured.

| Setup | Recorded value |
|---|---|
| Model | `Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold@76c0b5173166d2795dd48860f45d8224817f894c` |
| Runtime | TensorFold base `f350cd3ea253a042e205746928271f9d8f71a82d`, unchanged 101 author patches and five Anvil telemetry patches; image `sha256:8f5604d13d319adac8dc708b2e8bf8ce54096e181313a2a8de83cbfdd2902b06` |
| Hardware and recipe | Two RTX PRO 6000 Blackwell Max-Q 96 GB GPUs; native Linux, TP2, EXL3 4-bpw, FP8 paged KV, DFlash2; current 48-GiB serving limit and conversation-disk cache preserved |
| Measurement path | Routed online Aider/Polyglot with contained Go tests; co-resident harness, uncontrolled warm cache and user traffic permitted |
| Contract | Configured 327,680 context/C4; one owned C1 task at a time, fixed 16,384 total response tokens, temperature 0.6/top-p 0.95; Max/Low/Low/Max order |
| Reproduction | [Pinned controls, source and recipe boundary](2026-11-09-tensorfold-reasoning-budget-evidence/configuration.json); [reviewed frozen plan](2026-11-09-tensorfold-reasoning-budget-evidence/inputs-v2/plan.json) |
| Decision | No model, route, cache policy or serving-default change; numeric-budget proposal not installed or tested live |

| Headline observation | Local result | Conditions |
|---|---:|---|
| Low task passes | 2 / 2 | First contained test in each repetition; one unique Go task |
| Max task passes | 0 / 2 | Both coding attempts failed in each repetition |
| Low first-response reasoning | 25 and 6 tokens | Same 16K response cap and sampling as Max |
| Max first-response reasoning | 16,384 tokens each | Entire reported completion, ending with `length` |
| Accounted responses | 8 / 8 | HTTP 200, zero pending or transport errors; four length finishes |

**Why it matters:** The local effort controls demonstrate a useful answer
within the existing cap, while the audit identifies a missing Anvil forwarding
seam for TensorFold's already-supported numerical thinking limit.

**Important caveat:** Two repetitions per effort on one selected task do not
establish a global reasoning policy or broad Go quality. A forced-budget
coding pass and a performance comparison were not measured.

[Artifact manifest](2026-11-09-tensorfold-reasoning-budget-evidence/artifact-manifest.json)
· [Evidence index](2026-11-09-tensorfold-reasoning-budget-evidence/README.md)
· [Native observations](2026-11-09-tensorfold-reasoning-budget-evidence/case-diagnostics.json)

## Starting question

The [November 8 validation](2026-11-08-tensorfold-go-validation.md) retained
two length-finished 16K responses classified entirely as reasoning, followed
by a separate 32K profile check that passed its first independent Go test.
Those observations do not establish why reasoning lasted so long, whether
lower effort would help, or whether a hard reasoning boundary would preserve
correctness.

This investigation keeps three questions separate: what the actual request
path and loaded template do, what happens in the bounded task executions,
and whether a source change is needed to expose an already-supported control.

## Frozen diagnostic controls

The initial order is `max-1`, `low-1`, `low-2`, `max-2`. Each job selects the
same pinned Aider/Polyglot Go task and 16K profile, requests C1, retains at most
two coding attempts and twelve transport calls, disables retries and keeps a
600-second suite deadline. Temperature remains 0.6 and top-p 0.95. The total
response cap includes reasoning and answer tokens; no numeric thinking budget
is requested in this initial matrix.

The paired order reduces order confounding but does not provide seeded or
cache-equivalent repetitions. Current cache policy is preserved, its warm
state is uncontrolled, and ordinary user traffic is allowed. At most two
temperature follow-ups are conditional on the declared initial outcomes;
their exact specifications require a separate freeze and review before use.

The reviewed pre-request custody amendment permits one owned job at a time
alongside user traffic under the existing C4 admission policy. Submission
requires zero native waiting requests and at most two running requests.
Complete successful transport accounting and worker exit gate the next owned
job. Global idle, exclusive engine counters and managed-campaign activation
custody are not claimed; existing requests are never interrupted for the test.

## Identity and historical boundary

The checkpoint and runtime image match the earlier TensorFold record, but
the current process generation, disk-cache policy and 48-GiB serving memory
limit form a new baseline. This is not a same-process replay or a matched
historical performance comparison. The current route, model, context,
output/reasoning defaults and cache policy are preserved during this work.

The model is
`Mia-AiLab/GLM-5.3-Flash-EXL3-4bpw-TensorFold@76c0b5173166d2795dd48860f45d8224817f894c`.
The runtime image is
`sha256:8f5604d13d319adac8dc708b2e8bf8ce54096e181313a2a8de83cbfdd2902b06`:
TensorFold base `f350cd3ea253a042e205746928271f9d8f71a82d`, the author's
unchanged 101-patch series and five ordered Anvil telemetry patches.

## Request-path audit and proposed control

`max` is an effort instruction in the rendered prompt, not a numerical
thinking-token allocation. Metadata-only tokenization through the actual
loaded template produced the distinct `Reasoning Effort: Low`, `High` and
`Max` headers. Each dummy verification prompt contained 17 tokens. The active
request path preserves the explicit effort: the router's effort default is
soft, with no conflicting hard override, and the native request value takes
precedence over the recipe's default `high`.

The total response cap includes both thinking and answer tokens. This
configuration has no positive separate thinking budget: the launch omits the
native budget flag, whose default is zero. If the model keeps thinking until
the total cap, no answer allowance remains. The engine counts through the
first closing-think token, or counts all generated tokens as reasoning if
that token never appears. This supports response-cap starvation as the
observed mechanism; it does not explain every reason the model chose a long
thinking path or prove a semantic loop.

The pinned engine already accepts a request-local `thinking_budget` and can
force the transition out of thinking. The installed Anvil Aider controls did
not expose that field, and the router rebuilt the request without forwarding
it. The separate [source proposal](https://github.com/fakoli/anvil-serving/pull/751)
adds typed `thinking_budget_tokens` in the benchmark controls, maps it to the
existing wire field and preserves that field through the relay. It does not
require an engine patch or change route selection.

That proposal is not installed, deployed or an arm in this effort matrix.
A forced boundary still needs its own executable quality check: it does not
guarantee a useful answer, and the counted closing sequence means reported
reasoning tokens need not equal the requested budget exactly. The separate
native `thinking_reserve` setting expands the total allowance and is outside
this fixed-cap investigation.

The source audit also traced RAM and conversation-disk prefix matching to the
full rendered token sequence, including the effort header. Low/Max divergence
prevents reuse past that differing token; compatible identical prefixes can
still be reused. This rules out the specific user-text-only-cache-key
explanation at the source-contract level. It establishes neither cold-cache
equivalence nor identical stochastic generation.

## Results

| Frozen job | First response: reasoning / completion tokens | Independent task outcome |
|---|---:|---|
| Max 1 | 16,384 / 16,384 | Both contained test attempts failed; both responses ended with `length` |
| Low 1 | 25 / 1,045 | First contained test passed; both transport responses ended with `stop` |
| Low 2 | 6 / 620 | First contained test passed; both transport responses ended with `stop` |
| Max 2 | 16,384 / 16,384 | Both contained test attempts failed; both responses ended with `length` |

Low removed response-cap starvation in these two selected task repetitions.
It is not a global model-policy recommendation or a broad Go-quality score.
Both Max repetitions retained the unchanged starter and failed both contained
tests. Both Low repetitions submitted real, distinct implementations; test
and reference bytes were unchanged. These are independent deterministic task
grades, not model self-assessment.

Each job used two transport calls and left its whole-job budget
unexhausted. The Low auxiliary responses reported five reasoning tokens each,
with 232 and 254 completion tokens respectively. Transport calls remain
separate from coding attempts; an auxiliary call is not another task grade.
Missing visible-token counts remain unknown, with completion-minus-reasoning
labeled as a derived non-reasoning count. Retained budget durations are not
used as performance measurements. The eight captured responses total 10,456
prompt, 67,687 completion and 65,577 reported reasoning tokens. Observed
engine-counter deltas agreed with those totals; coexisting traffic was allowed,
so that agreement is not exclusive attribution.

The conditional temperature follow-ups were not run: the two Low controls
resolved the selected task without response-cap starvation, satisfying the
frozen stopping condition. Temperature effects remain unmeasured. Source and
sampled metadata do not establish an exact repetitive loop or reveal why every
Max reasoning token was generated; complete traces and raw token IDs were not
retained for that claim.

## Final custody and source status

Independent review accepted all four grades, complete transport accounting,
worker/process-group exit, unchanged executing source and strict Python/oracle
environment verification. The final managed readback matched the current
container generation, start, image, model, GPU selection, recipe, registry and
route. Observed cgroup OOM counters remained zero under the existing 48-GiB
limit. These are scoped equality and resource checks, not global traffic
isolation or a performance gate. Original frozen comparison files remained
unchanged.

The [proposed Anvil change](https://github.com/fakoli/anvil-serving/pull/751)
was independently reviewed at
`bba218350aff8ee4b59f446977192bebc5af8174` and passed offline validation.
The installed CLI and live router stayed unchanged. The code proposal,
measured effort-control outcomes and any later deployment/forced-budget
qualification are separate evidence states.

## Decision boundary

No broad Go score, model ranking, throughput claim, numeric-budget deployment
or serving change follows from this plan. Original evidence is preserved;
unsafe identity, resource, OOM, admission or infrastructure failures stop
submissions. Ordinary independent coding failures remain in the frozen sample.
