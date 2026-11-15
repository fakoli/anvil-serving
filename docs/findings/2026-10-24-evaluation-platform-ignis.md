# Evaluation platform pilot: partial incumbent results and blocked Ignis admission

**Date:** 2026-10-24, with observations through 2026-10-25 UTC

**Scope:** Local routed coding/tool diagnostics on NVIDIA RTX PRO 6000 Blackwell Max-Q hardware

**Decision:** `no-promotion`; diagnostic closeout, comparison acceptance incomplete

**Outcome, updated 2026-10-25:** R12 submitted 48 incumbent jobs: 44 completed with complete measurements and grades (38 pass, six fail); four were cancelled with unknown grades. Its 48 planned candidate cells did not run. The separate R13 candidate-only attempt was refused before container creation, with zero of 48 jobs submitted. Exact incumbent restoration, routed preflight and an explicitly minimal-thinking Pi read/final probe passed. The historical thinking-off strict-output failure remains open. No candidate comparison or promotion follows.

**Comparison acceptance remains incomplete.** The candidate has no measured result. Minimal-thinking Pi acceptance is a scoped restoration check, not acceptance of thinking-off or default behavior.

<!-- benchmark-result-card/v1 -->
## Result card

The pilot exposed harness startup, import and mixed-concurrency closure defects, followed by candidate admission and identity-integration failures. The earlier mixed-C4 canary established a narrower result than a model comparison: the repaired platform stopped four mixed clients, performed one shared fresh-zero drain, retained complete four-plane telemetry and closed an independently verified immutable evidence chain. Task quality remains a separate result, including the Go failure.

| Setup | Retained scope |
|---|---|
| Incumbent | NVIDIA GLM-5.3-Flash FP4, vLLM, TP2 on two RTX PRO 6000 Blackwell Max-Q GPUs |
| Challenger | Qwen3.8-Flash-Next Trellis artifact, Ignis 0.8.0, configured TP1 on one GPU; unmeasured |
| Measurement path | Authenticated routed online requests; C1/C4 task workloads, fixed order, uncontrolled cache state |
| Configured context / admission | Incumbent 327,680 tokens / C4; candidate 131,072 tokens / eight profile lanes, unqualified |
| Evidence class | Bounded functional/quality observations, source validation, failure and restoration diagnostics; no comparative performance |
| Planned comparison | 12 tasks × C1/C4 × two repeats × two arms: 96 cells in 60 batches per attempt |
| Current result | R12: 44 complete incumbent measurements, 38 passing / six failing grades, four cancelled unknowns; candidate unmeasured |
| Decision | Preserve incumbent assignment; exact restoration and explicit minimal-thinking Pi acceptance verified; thinking-off failure open; no promotion |

| Population | Submitted / planned | Retained terminal boundary |
|---|---:|---|
| R5 | 7 / 96 | Six native completed, one Aider child cancelled, 89 unrun; incomplete cancellation interval |
| R6 | 9 / 96 | Six native completed, two Aider harness failures, one cancelled, 87 unrun; one interval unknown |
| R7 | 20 / 96 | 16 completed jobs, one failed, three cancelled, 76 unrun; 17 known grades include one failed measurement |
| Four separate C++ canaries | Four single-task runs | Two ungraded failures; one resolved with false lint feedback; one resolved after corrected preparation |
| Separate mixed-C4 canary | 4 / 4 | Four complete measurements, three task passes, one valid Go task failure; accepted closure |
| R8 | 48 / 96 | 42 complete grades: 32 passes / ten failures; two maximum-call failures with unknown grades, two cancelled, two cancelling; 48 candidate cells unrun |
| R9 candidate-only | 0 / 48 | Host-reserve admission refused before creation; all rows remain planned |
| R10 candidate-only | 0 / 48 | Candidate loaded; monitoring identity gate blocked admission; all rows remain planned |
| R12 | 48 / 96 | Incumbent: 44 completed/graded, 38 pass / six fail; four cancelled with unknown grades. All released batch closures valid; 48 planned candidate cells unrun |
| R13 candidate-only | 0 / 48 | Separate pre-create host-reserve refusal; no candidate container, traffic or router alias transition |
| Candidate across these populations | 0 | No measured functional, quality, latency, capacity or energy result |

R5/R6 reached no graded Aider result. R7 has 17 known grades—15 passing and two failing—but only 16 complete measurements. Neither those populations nor the separate canaries may be pooled into R8. Process success, HTTP status, task grade and measurement closure are different facts.

Packet navigation: [R8 evidence index](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/README.md), [manifest](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/artifact-manifest.json), [publication summary](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/publication-summary.md); [R10 restoration index](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/README.md).

Authoritative summaries: [R5](2026-10-24-evaluation-platform-ignis-evidence/r5-cancelled/summary.json), [R6](2026-10-24-evaluation-platform-ignis-evidence/r6-cancelled/summary.json), [R7 diagnostic](2026-10-24-evaluation-platform-ignis-evidence/r7-diagnostic/summary.json), [four C++ canaries](2026-10-24-evaluation-platform-ignis-evidence/aider-canaries/summary.json), [mixed-C4 canary](2026-10-24-evaluation-platform-ignis-evidence/mixed-c4-canary/summary.json), [R8 diagnostic](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/summary.json).

## 2026-10-25 successor: R12 baseline and R13 admission refusal

The [authoritative R12/R13 summary](2026-10-25-evaluation-platform-r12-r13-evidence/summary.json) separates these fresh attempts from R5–R10 and the standalone canaries. The [evidence index](2026-10-25-evaluation-platform-r12-r13-evidence/README.md) and [artifact manifest](2026-10-25-evaluation-platform-r12-r13-evidence/artifact-manifest.json) map the retained records. Both attempts froze source `c0ce1d0633939eaf686bbd1e9e59241f6a0534aa`; they do not reuse earlier attempt outcomes.

R12 closed 44 incumbent measurements and grades: 38 pass and six fail. Four further submitted jobs were cancelled at the traffic bound with grades unknown. The six grade failures are completed task outcomes, not failed job execution; the four unknowns are not scored failures. All 30 released batch closures verified client stop, fresh zero drain and bound four-plane telemetry. The active interval was 3,529.878 seconds, including closure, within the 3,600-second arm budget and its 120-second reserve. [Native report](2026-10-25-evaluation-platform-r12-r13-evidence/campaign-report.json) · [Closure and cancellation](2026-10-25-evaluation-platform-r12-r13-evidence/r12-closure-and-cancellation.json).

Complete interval telemetry does not establish complete per-request accounting: request overlap remains unknown. One cancelled child's process, adapter-result and native grader records are missing. The fixed task order, uncontrolled cache and small mixed native/Aider sample remain diagnostic limits; these are neither full-benchmark scores nor simultaneous maximum-context capacity measurements. The planned R12 candidate arm remained unrun.

R13 froze a separate 48-cell candidate population, with prospective comparison eligibility restricted to the 44 graded R12 keys, including all six failures; the four cancelled keys were excluded in advance. It submitted no jobs. The 49 GiB RAM/no-extra-swap candidate plus 16 GiB host reserve required 65 GiB available host memory. The managed guard refused before container creation; exact refusal-time available memory and shortfall were not retained. This is a host-policy admission refusal, not a measured OOM, model-quality failure or GPU-capacity result. [Refusal evidence](2026-10-25-evaluation-platform-r12-r13-evidence/r13/admission-refusal.json) · [Frozen population](2026-10-25-evaluation-platform-r12-r13-evidence/r13/frozen-population.json) · [Zero-job report](2026-10-25-evaluation-platform-r12-r13-evidence/r13/campaign-report.json).

The exact incumbent recipe, image, model revision and GPU order were restored in a new managed incarnation; the original router configuration never transitioned. Fresh routed coding, JSON, approximately 128K context and 20-tool checks passed. One explicitly **minimal-thinking** Pi invocation passed all 29 independent read/final checks with two complete HTTP 200 captures. A reviewer accessor false-negative was corrected offline against that same invocation; there was no live retry. This does not close the historical thinking-off failure or establish upstream/downstream byte equivalence. Remote-memory terminal state remains unknown; local zero counters do not prove fleet-wide drain. [Restoration](2026-10-25-evaluation-platform-r12-r13-evidence/restoration.json) · [Scoped acceptance evidence](2026-10-25-evaluation-platform-r12-r13-evidence/restoration-evidence.json).

Public artifacts are sanitized derivatives. Their ledgers bind original private hashes to separately hashed public bytes; they cannot revalidate native private hash chains against redacted bytes. The R12 projection retains native record schemas; complete R13 prepared-graph validation is not claimed. Earlier 127 artifacts remain separate. No model/engine ranking or promotion is supported.

### R5–R10 closeout at its original cutoff

**Historical outcome:** This diagnostic attempt closed without a deployment comparison. R8 retained 42 complete incumbent measurements with 32 passing and ten failing grades, but cancellation custody and its final interval remain incomplete. R9 was refused before candidate creation. R10 loaded the candidate, then Anvil Serving withheld monitoring because its served-identity adapter misread the model argument; no candidate generation ran. The exact incumbent service was restored and routed preflight passed, but the real Pi client's strict final-answer oracle failed.

**Acceptance at that cutoff was incomplete.** A fresh candidate campaign after the source correction and a passing accepted Pi final-answer oracle were still required. The later scoped Pi pass does not supply a candidate measurement or authorize promotion.

## Exact configuration

This is a public-safe reconstruction of the pinned managed recipes. The table records configured controls. R10 reached startup/readiness observations, but none of its configured inference limits was qualified. Operator addresses, container identities, GPU UUIDs and private storage paths are intentionally absent. The [retained identity/control map](2026-10-24-evaluation-platform-ignis-evidence/r6-cancelled/identity-control-map.json) binds the public model, revision, image and GPU-count fields.

| Control | Incumbent | Candidate |
|---|---|---|
| Model repository | `nvidia/GLM-5.3-Flash-FP4-Local` | `gpillon/Qwen3.8-Flash-Next-trellis-a25-ignis` |
| Model revision | `76348ddb2be2e35e39ecfc00e75dc07703f4f3d5` | `8d94db83cd8e58d6be31e796723e5e35b960638c` |
| Runtime | `vllm-b12x-release-10152026` | Ignis 0.8.0, source `950ee0d1c2cf6178cd7fec29df3f17c3e127f987` |
| Image SHA-256 | `745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29` | `2b8d56c75bbba758b504bc0ef7edea59bf45df398dd9a4cde1514edc81b63029` |
| GPU allocation | Two 96 GB GPUs, TP2 and DCP2 | One 96 GB GPU, TP1 |
| Weights / KV | NVIDIA QAD NVFP4/MXFP8 compressed scales; FP8 KV | Trellis mixed 2–4 bits per weight experts, 2.5 mean; resident experts. startup reports `hq-e8-2b` KV; no inference measurement |
| Context / runtime concurrency | 327,680 tokens / four sequences | 131,072 tokens / eight configured profile lanes |
| Speculation | MTP3 | MTP3 profile |
| Selected runtime controls | Prefix caching enabled; 7,650,410,496-byte KV setting; memory utilization 0.985 | `rtxpro6000` profile; default loop guard; host KV reuse pool disabled; vision disabled |
| Host-memory containment | Exact incumbent recipe retained | R9: 52 GiB RAM/no extra swap plus 16 GiB reserve; R10/R13: 49 GiB/no extra swap plus the same reserve |
| Observability | Identity-bound engine metrics | Readiness `/ready`; metrics `/ui/metrics` on the same API listener, not a second metrics port |

The candidate image is a locally retained immutable image of the pinned upstream build. Its identity is not an assertion that a mutable tag remains unchanged. GPU allocation and reserved memory do not prove active compute, utilization or simultaneous full-context capacity. Only the incumbent ran model work in R5–R8, R12 and the separate canaries; R13 never created a candidate container.

## Method: independent tasks inside a durable campaign

The native half contains structured editing, tool sequencing, parallel tools, dependent results, recovery from an explicit tool error and a debugging loop. Its tool/debug cases include synthetic tool results; they do not prove that an actual repository patch executed. The contained Aider tests provide the executable coding oracle. The coding half uses six real tasks from pinned [Aider](https://github.com/Aider-AI/aider/tree/5dc9490bb35f9729ef2c95d00a19ccd30c26339c) and [Polyglot benchmark](https://github.com/paraemsi/polyglot-benchmark/tree/7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f): C++ `all-your-base`, Go `alphametics`, Java/JavaScript/Python `affine-cipher`, and Rust `accumulate`. This is a diagnostic sample, not the full benchmark.

Preparation verifies that each starter fails and its reference solution passes the independent language tests. A model does not grade its own patch. Generated edits execute in contained nonroot tests without host credential files or a host Docker socket. Passing the reference oracles establishes a usable test harness; it is not a model score.

The `pilot-v2` native profile allows 24 steps, an 8,192-token cap and 600 seconds; the Aider profile permits at most 12 transport attempts with zero transport retries, distinct from its two upstream edit/test attempts, and a 16,384-token completion cap. Both arms request temperature 0.6, top-p 0.95 and reasoning effort `max`, leaving the separate thinking-mode override at its default. Completion caps include reasoning and output. Engine-specific defaults and template semantics remain different: a shared request field does not equalize reasoning compute. Fixed task order is not deterministic sampling, and cache state was uncontrolled.

The campaign freezes its source/profile/workload identities before child submission. Durable child jobs, batch barriers and cancellation receipts preserve outcomes across coordinator interruptions. Each arm has a 3,600-second active budget, with 7,200 seconds total and a 120-second drain/finalization reserve. Active time includes release, verified drain and bounded telemetry finalization; it is not merely the sum of model-call latency.

Telemetry observes engine, GPU, host and cgroup planes against the declared model/image/recipe, GPU allocation, serving incarnation and collection generation. Closure requires clients to stop and a fresh identity-bound engine-zero observation after that stop. A cached zero, missing counter, reset segment or failed scrape cannot establish drain or complete coverage. Four-plane coverage is an observability property, not proof of correct model output.

## Closed outcomes and failures

### R5: startup watchdog and incomplete cancellation interval

Source `6b26ed55285480cd731ae7771f41eea917382f02` failed to establish the first Aider child's watchdog before preflight/model requests. Retained offline verification exceeded the two-second startup guard; the active-receipt/telemetry dependency also blocked failure finalization. Native cancellation terminated the child. The coordinator's zero exit did not change its cancelled state.

The cancellation process lacked its declared endpoint credential, and that batch's activation/drain was unverified. These were co-observed; credential absence is not established as the cause because the underlying activation-recheck exception was not retained. A later independent engine-zero observation cannot repair the frozen interval or prove no compute occurred. The [R5 diagnostic](2026-10-24-evaluation-platform-ignis-evidence/r5-cancelled/failure-diagnostic.json) retains this uncertainty.

### R6: adapter import failure after the watchdog repair

Source `890d51987f40cf02cfa8822fcd9f456c419baa7c` closed two failed Aider children with fresh drain and all four telemetry planes, demonstrating the earlier watchdog repair on those error paths. Both failed before grading because a colon-bearing checkout path was split by POSIX `PYTHONPATH`, leaving isolated Python unable to import the adapter. The next child was cancelled before activation. These are harness failures, not failed model solutions.

One interval and total active wall time remain unknown. The [R6 summary](2026-10-24-evaluation-platform-ignis-evidence/r6-cancelled/summary.json) separately retains job/sample denominators and the six completed native cells' call, token and harness-time totals. Those subset totals are not TTFT, decode throughput or a matched-arm comparison. The [diagnostic](2026-10-24-evaluation-platform-ignis-evidence/r6-cancelled/failure-diagnostic.json) binds the failed-child closures.

### R7: mixed-C4 closure failure and refused native projection

The fully validated `60c80fa2b10e70939912c7ce184849790c041fd5` attempt was cancelled after a native child sought global drain while mixed-C4 siblings were still active. The coordinator exited 2; cancellation exited 0. Its first C1 repeat graded 12 tasks with ten passes and two failures; the first four native C4 tasks passed. A further passing native task failed measurement closure. Thus 17 known grades do not establish 17 complete measurements. One batch interval and four child intervals remain unknown; aggregate active time and overlap are not accepted.

The C1 coding failures remain distinct. Go `alphametics` retained the unimplemented starter through both tests with token-limit warnings; response-length termination before an applicable answer is supported, but raw wire finish reason and exact token consumption were not retained. JavaScript `affine-cipher` changed the required object-key interface first to array destructuring, then separate arguments; tests failed with a non-iterable-key error, then recursive stack exhaustion. Its event stream also has one unpaired transport start. These diagnoses do not establish complete request/usage accounting.

The full native publication projection was refused. The retained [reference audit](2026-10-24-evaluation-platform-ignis-evidence/r7-diagnostic/reference-audit.json) records 324 reference occurrences: 320 matched and four mismatched across two submitted children. Expected and observed hashes remain separate. The [diagnostic packet](2026-10-24-evaluation-platform-ignis-evidence/r7-diagnostic/README.md) preserves all 96 report rows and the refusal; it does not rehabilitate the damaged chain or claim a complete raw projection.

A later independent absence audit found all 20 owned worker groups stopped, both old watchdogs and the coordinator absent, and all 113 recorded containers absent. Its 134 inputs were unchanged; a separate expiry check retained all 212 bound R7 source files unchanged. This later cleanup observation does not repair R7 intervals, hash mismatches or scoring uncertainty. The original incumbent and router configuration remained unchanged; no candidate or temporary alias was activated.

## Source and preparation validation, separate from model results

Repairs were validated against their own source identities. The import fix passes a checkout root as one isolated-Python argument instead of a path list. A separate preparation fix normalizes read/traverse permissions for the nonroot contained user. Fresh preparation verified six failing starters and six passing references; all 14 preparation containers were nonroot and removed. The original permission probe's stderr was not retained, leaving its exact first denied pathname unknown.

The four single-task C++ canaries are retained in the [reviewed canary packet](2026-10-24-evaluation-platform-ignis-evidence/aider-canaries/README.md):

| Canary | Outcome | Interpretation |
|---|---|---|
| 1 | One selected, zero graded | Exact live cause unknown because the original typed process error was discarded; three HTTP-200 responses do not establish completed logical requests or quality |
| 2 | Ungraded failure with `swe_process_output_overflow` | Diagnostic code retention established this run's overflow branch, not the first run's cause |
| 3 | One graded/resolved, native `[false, true]` | Finite 8 MiB Aider output capture allowed completion, but upstream basic lint misread a C++ `.h` file as C and injected false feedback; not a clean comparison |
| 4 | One graded/resolved, native `[false, true]` | Fresh preparation after the scoped header-lint correction removed that feedback; accepted single-task confirmation |

The output repair preserves finite capture, request/time/process-group bounds and the shared SWE/performance limits. The header correction changes only basic-lint grammar for admitted C++ solution headers; compiler tests, filenames, budgets and custom lint remain unchanged. It is a disclosed local correction, not official leaderboard equivalence. The first native compiler-test failure in the resolved canaries remains unassigned. Their different call counts and durations do not establish a speedup between stochastic trajectories.

The later shared-batch repair is committed at `d8f86a2208584b21f39c3cf600762142d7ad0057`, executable-source digest `4a87fc4fcd3d7f21b1a5f51197392f057f27ae103c09cb2792bb692656a17430`. Each child records local stop; one serialized owner waits for all siblings before global drain. Normal completion and cancellation share receipt locks, cancellation stops all children before closure, and watchdogs preserve terminally anchored bytes. An orphan telemetry terminal without its shared closure stays blocked without redrain, rewrite, backdating or reclaimed time. Independent failure injection accepted these guards; it did not repair historical R7 bytes.

All three R15 full suites and packaging R8 passed on that exact clean source. The [source-validation artifact](2026-10-24-evaluation-platform-ignis-evidence/mixed-c4-canary/source-validation.json) retains the exact Python totals and package/source bindings. These software checks are prerequisites, not model scores or future campaign results.

## Completed mixed-C4 closure diagnostic

One fresh incumbent-only batch used native tool recovery, native debugging, C++ `all-your-base` and Go `alphametics`, with unchanged task controls and a 900-second outer active bound including 120 seconds reserved for drain/finalization. It was a separate diagnostic, excluded from full comparison populations.

All four jobs completed with complete measurements and measured engine/GPU/host/cgroup planes. The two native cases passed; C++ retained `[false, true]`; Go retained `[false, false]`. Go is a valid scored task failure with complete evidence, not a harness failure or missing grade. It remains in the denominator.

The [shared closure](2026-10-24-evaluation-platform-ignis-evidence/mixed-c4-canary/batch-closure.json) binds four local stops before one 35-second fresh-zero drain. The [telemetry terminal](2026-10-24-evaluation-platform-ignis-evidence/mixed-c4-canary/telemetry-terminal.json) binds all 60 retained rows; finalization completed within the declared bound. The [independent chain audit](2026-10-24-evaluation-platform-ignis-evidence/mixed-c4-canary/full-chain-audit.json) checked 41 references twice with unchanged bytes. The postterminal audit found no owned worker, watchdog, client or recorded container remaining, with incumbent identity and observer intact.

Six Aider request starts yielded five observed HTTP-response events. The missing C++ response record does not invalidate its deterministic grade sequence or shared closure, but complete response or usage totals are not claimed. Request overlap remains unknown. The [summary](2026-10-24-evaluation-platform-ignis-evidence/mixed-c4-canary/summary.json) is authoritative for timing and sample counts; none is a throughput, speedup, engine-superiority or capacity result.

Independent review accepted the bounded native closure and the exact 25-file public-safe derivative packet. That acceptance is scoped to this canary, not a completed 96-cell comparison or a publication-size exception.

## R8: stopped at the traffic bound, native cancellation incomplete

R8 retained the validated `d8f86a2` source, controls and 96-cell matrix. Its canonical manifest is `cdb787ab871e0f19655d34c5add6261a5855478975c0435fbc1663696c84f930`, raw SHA-256 `42f51f16df195100cb4e6ebb09c85eb1991ebb1f0c1445dacd0e29e72a48b0d5`. The incumbent arm began at 2026-10-25T06:18:55Z; its coordinator ended at 07:19:17Z with cancelled state and exit 0 after the frozen traffic deadline. That exit is not complete cancellation custody.

The [exact 96-row report](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/campaign-report.json) retains 48 submitted incumbent cells and 48 unrun candidate cells. Of the incumbent population, 42 have complete measurements and grades—32 passing and ten failing. Two jobs exhausted `max_model_calls` with unknown grades; those are not ordinary scored task failures. Two jobs are cancelled and two remain `cancelling`. Native unfinished 50 includes 48 planned candidate cells plus the two cancelling incumbent cells. These categories are not combined into a success rate.

The [cancellation diagnosis](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/cancellation-diagnosis.json) retains phase `stopping_unverified` and a final immutable blocked closure with `batch_clients_not_stopped`. That interval and total active time remain unknown. No budget extension, blind cancellation retry or native-state rewrite was used to turn it into a successful closure. The [first-repeat chain audit](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/first-repeat-chain-audit.json) covers 24 cells, not the whole submitted population: 23 completed, one maximum-call failure, 17 passes, six failures and one unknown grade.

A later [physical recovery audit](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/physical-recovery-context.json) found all 48 exact worker groups, the coordinator and matching clients/watchdogs absent, with all 99 retained containment IDs absent. The exact incumbent generation remained unchanged, and a newer observer sample showed zero running/waiting requests after the absence observation. Those findings support later physical recovery only. They do not change the two native cancelling states, repair the blocked interval, validate a complete native graph or prove that no earlier compute occurred.

The reviewed R3 projector refused this report before graph traversal because its native-state gate does not accept `cancelling` rows. [Exact refusal](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/projection-refusal.json) is retained. The compact [R8 diagnostic packet](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/README.md) contains the unchanged report and bounded supporting extracts; full native projection and chain acceptance are not claimed. This is a diagnostic derivative, not a complete accepted native graph.

## R9 and R10: no candidate measurement

R9 froze a separate candidate-only 48-cell, 30-batch population with the original tasks/order/controls, fresh identities and a 3,600-second active bound including a 120-second reserve. Its 52 GiB RAM limit plus 16 GiB host reserve required 68 GiB available, so managed admission refused before creation. All 48 rows remain planned; this is a host-envelope refusal, not 48 failed tasks or an engine-quality failure. A later memory snapshot is not the precise admission-time sample. Exact interim incumbent reload and six bounded routed smoke/JSON/tool checks passed. The [R9 report](2026-10-24-evaluation-platform-ignis-evidence/r9-refusal/campaign-report.json) and [summary](2026-10-24-evaluation-platform-ignis-evidence/r9-refusal/summary.json) retain that unexecuted population and limited restoration scope.

R10 was a separately frozen 49 GiB/no-extra-swap variant, preserving the 16 GiB reserve and all other recipe fields. Its operational memory and memory-plus-swap limits were both 50,176 MiB. An inherited prose note still said 52 GiB; that hash-bound note is an explicit erratum, not the executed limit. The candidate was created and startup logs reached loaded/ready. The coordinator observed `/ready` HTTP 200 and `/v1/models` advertising `qwen3.8-flash-next`; the raw HTTP bodies and inner registration exception were not durably retained, so those details carry coordinator/reviewer provenance rather than a raw-response claim.

Managed load exited 1 because Anvil Serving's served-identity parser recognized `--served-model-name` but ignored Ignis's `--model-id`. It fell back to the repository name, and the monitoring guard correctly withheld metrics for the advertised-model mismatch. This is an Anvil Serving identity-adapter defect; it is not evidence that Ignis failed to load, failed readiness or produced an incorrect answer. A separate nonfatal disk-KV permission warning is retained without assigning it as the cause. See the [source-backed diagnosis](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/monitoring-failure.json) and [startup events](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/startup-events.json).

R10 made zero generation or campaign requests. Its [full 48-row report](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/campaign-report.json) remains entirely planned, with zero attempted, graded or complete measurements. No temporary alias transition occurred. The candidate was removed through managed lifecycle. Candidate functional quality, latency, throughput, energy and simultaneous context capacity remain unmeasured.

Before candidate outcomes, the [prospective eligibility ledger](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/prospective-eligibility.json) fixed 42 baseline keys with complete measurements and retained six exclusions. Graded failures remain eligible; neither success nor latency selects the population. There are still no candidate measurements to intersect with those keys. Changing model, quantization, GPU count and runtime together would not isolate engine causality. Configured eight-lane concurrency is not accepted capacity, and a speculation benefit would require a matched no-speculation control. Fixed order, uncontrolled cache, separate attempts and the 12-task sample further limit interpretation. No speedup, parity, ranking or promotion follows.

## Service restored; strict real-client acceptance failed

This section records the R10 cutoff. The later R12/R13 restoration and scoped minimal-thinking acceptance are recorded [above](#2026-10-25-successor-r12-baseline-and-r13-admission-refusal); the thinking-off failure remains open.

The first R10 incumbent restoration command omitted the original explicit GPU selection/order and used the default `all` selection. The engine was healthy, but monitoring correctly refused the changed GPU identity. This was a coordinator launch omission, not a second engine or product defect. That incarnation was removed and the exact explicit-order reload passed. The original image, model, recipe, GPU order and endpoint were restored with fresh monitoring registration; the original router configuration remained unchanged and admitting. Its eight local counters were zero, while a separate remote-memory terminal remained unknown: local zeros do not prove global drain. The [failed attempt](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/restoration-failed-attempt.json) remains alongside the [final service evidence](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/final-restoration-evidence.json).

Fresh routed preflight passed coding, JSON, approximately 128K needle retrieval and 20/20 tool requests. Real Pi read/terminal operations and selected provider/model checks passed. However, the first Pi launch inherited its launcher script on stdin. A second attempt used `DEVNULL` and the exact 198-byte prompt; it still added prose and duplicated the required digest, failing the exact final-text oracle. Both operator mistakes—the GPU-selection omission and stdin contamination—are retained plainly. The validator's later recognition of `agent_settled` after `agent_end` corrected terminal-event interpretation without changing the strict output failure. No further retry is included.

The [two-attempt Pi validation](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/pi-validation.json) and [restoration summary](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/restoration.json) therefore record `service_restored=true`, `restoration_acceptance_complete=false`. Routed preflight does not override failed real-client acceptance. At that cutoff, a fresh post-fix candidate campaign and a passing accepted Pi final-answer oracle were still required; those diagnostic artifacts established neither implementation completion nor a successful replacement trial.

## Evidence and publication boundary

The eight earlier populations have separate artifact manifests: [R5](2026-10-24-evaluation-platform-ignis-evidence/r5-cancelled/artifact-manifest.json), [R6](2026-10-24-evaluation-platform-ignis-evidence/r6-cancelled/artifact-manifest.json), [R7](2026-10-24-evaluation-platform-ignis-evidence/r7-diagnostic/artifact-manifest.json), [four Aider canaries](2026-10-24-evaluation-platform-ignis-evidence/aider-canaries/artifact-manifest.json), [mixed-C4 canary](2026-10-24-evaluation-platform-ignis-evidence/mixed-c4-canary/artifact-manifest.json), [R8](2026-10-24-evaluation-platform-ignis-evidence/r8-diagnostic/artifact-manifest.json), [R9](2026-10-24-evaluation-platform-ignis-evidence/r9-refusal/artifact-manifest.json), and [R10](2026-10-24-evaluation-platform-ignis-evidence/r10-diagnostic/artifact-manifest.json). Their summaries are authoritative within their own population; packet-level publication summaries are derivative copies, not replacement measurements.

Private originals remain authoritative. Embedded native hashes bind original bytes; projection ledgers and artifact manifests bind sanitized derivative bytes. Projections cannot reproduce redacted inputs or revalidate private identity chains. Public wrappers were assembled after execution and do not claim to be before-request captures. R5/R6 preserve their reviewed derivative graphs; R7/R8 explicitly retain refused full projections and diagnostic substitutes. R9/R10 retain unexecuted reports and admission/restoration evidence, with no fabricated campaign closure. Missing or invalid intervals remain missing or invalid.

The whole-experiment evidence exceeds the repository's default size limits. Under [ADR-0027](../adr/0027-public-findings-are-durable-evidence.md), independent reviewer `evaluation_t005_correctness_r2` approved the exact **127-file, 38,388,806-byte** inventory on 2026-10-25. The inventory SHA-256 is `f216d6ca850dec19951fb217d973c9c0fca234cd3f265b9b3f1e26c7d78ff916`; the retained exception review SHA-256 is `662c0ee678592a1bd8a47a03dd39a8b900ed89981c2cd0eb9fdc88b8315f0637`. The eight linked manifests above enumerate the public packet files. All destination bytes matched the reviewed source inventory when copied.

The exception covers the aggregate above 5 MiB and these four files above 1 MiB, relative to `2026-10-24-evaluation-platform-ignis-evidence/`:

| File | Bytes | SHA-256 |
|---|---:|---|
| `r5-cancelled/projection-ledger.json` | 7,517,133 | `ff5e3dcf52df2b508fbd5cae1f921cba53449815d8b37fa2a90d3b2d14355a91` |
| `r5-cancelled/projections/child-evidence-records-0001.jsonl` | 9,942,136 | `705d11caa681757f48e72da3768036cc8d1ab62d6548375ded72c5c32bd97992` |
| `r6-cancelled/projection-ledger.json` | 7,788,783 | `d62ba2a17192664fac10f877a8899637129da0fbc710258db1b818d40177498e` |
| `r6-cancelled/projections/child-evidence-records-0001.jsonl` | 10,160,811 | `ceb72cd32feb4913f9c28671c907cd89aa8746c193740f933666333bad1051e7` |

These records retain complete failed/cancelled populations and original-to-derivative provenance. A compact subset would discard that evidence; no existing anonymous immutable external store with verified retention was available. The reviewer accepted the explicit Git/review cost for this inventory only. Any byte, path, count or total change requires a new exception review. Link, privacy and final-tree checks remain separate publication gates.

**R12/R13 addition, 2026-10-25:** independent reviewer `evaluation_t006_recipe_monitoring` approved the separate 22-file, 29,730,566-byte addition and verified that the earlier 127 files remain unchanged. The combined exact exception covers **149 files totaling 68,119,372 bytes**. The addition's inventory SHA-256 is `c3665559f0ed264ede595031cc4d195e07ba256470fa82e56331f4492f947148`; the final named size-review SHA-256 is `f216f775b1691a69fbed41a1288067a0bb101dab892add22d9b64f7d5f9c62e7`. The [new manifest](2026-10-25-evaluation-platform-r12-r13-evidence/artifact-manifest.json) and [evidence index](2026-10-25-evaluation-platform-r12-r13-evidence/README.md) identify the retained files. Its four files above 1 MiB are the projection ledger and the child-evidence, Aider/partial and telemetry JSONL streams. They preserve the complete failed/cancelled population, interval observations and redaction provenance; a summary-only subset would remove that audit trail. The PR records the exact paths, byte totals and approving reviewer. This exception does not make the candidate comparison complete.

Publication does not authorize promotion, hidden fallback, provider reconfiguration or a recommendation change. The retained incumbent selection is unchanged.
