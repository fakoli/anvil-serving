# Flare source findings — 2026-10-21

Pinned source: NVIDIA/flare-local-inference 47581ccac82660953a5b59cb04b2750d64edc97d.

1. **Concrete C4 hypothesis:** `ignition/components/capacity.py:48-65` defaults the speculative row envelope to max(padded concurrency, maximum width). Our C4/MTP3 package therefore captured seven shapes: batch1 widths1–4, batch2 widths1–2, batch4 width1. Old preparation logs corroborate this. Capacity16 enables all12 batch1/2/4 × width1/2/3/4 shapes. This is a candidate explanation, not a proven cause. Independent reviewer flare_trial_review confirmed the mechanism and supported the experiment.
2. The GLM TP2 page size512 is expected: recipe costs and context-parallel layout use256×ranks logical tokens/page. No metric/document contradiction remains.
3. `ignition run` does not forward native scheduler-policy. Use managed native `flare run` with the prepared package for balanced/throughput and same-package MTP0 controls. Native launch avoids re-export when changing those controls.
4. Increasing the speculative envelope may consume more graph memory and reduce KV. The recipe graph estimate is fixed, so measure actual remaining memory; do not infer fit from the estimate.
5. GLM preparation is text only. Speed improvements cannot qualify the incumbent vision contract. No automatic promotion.

Sources: pinned `ignition/recipes/glm53/recipe.py`, `bootstrap.py`, `ignition/recipes/declaration.py`, `ignition/cli/serve.py`, `docs/performance-work.md`, `docs/scheduling-runtime-design.md`.

Initial local gates: 68 agentic/benchmark-runner offline tests passed. Installed and published Anvil Serving are1.5.0; the prior isolated source checkout additionally carries reviewed, unreleased Flare recipe/preflight support. Structured model lifecycle tools are not exposed in this session; documented CLI is used.

## Independent follow-up: MTP0 and unsupported packed verification

Reviewer flare_trial_review traced GLM bootstrap384–445 and native selection107–158,311–335,376–400,468–475. The MTP3 package includes ordinary graph families. Runtime MTP0 disables proposal/speculative execution and draft-state maintenance; it retains package weights and draft allocation mappings. It is an execution control, not a clean memory baseline.

GLM does not implement packed verification. Its recipe drops the flag. Direct `ignition serve` rejects absent packed graphs after preparation, while export/run do not apply that check and can record misleading packed=true metadata. Do not test this flag as an optimization; record it as an upstream validation gap. No source patch or external issue was sent.

## Independent follow-up: GPU cache churn and host-cache sizing
Base C1 cold/warm12-request cells each published and evicted36snapshots with only108reused tokens, consistent with three snapshots/request exceeding32GPU slots. Exact eviction ordering was not traced, so source plus counters support a churn hypothesis, not proof of each victim.

Reviewer flare_trial_review conditionally accepted a32GiB memory-only store. GLM TP2 MTP row81,464,576B/rank gives a combined4096-padded162,930,688B record. One quarter of32GiB gives52records; the remaining page budget gives about7314combined3,538,944B page records. Source: recipe.py63-89,pool.py15-42,helios offload slab.rs235-315/334-355 and offload mod.rs320-460. Expected GPU mapping cost~64MiB/device. Require startup pool counts, unchanged snapshot/KV geometry, memory.current/peak, zero cgroup max/OOM, and >=12GiB host reserve. Disk0 retains bounded rank-split registered memory and skips copies if full.

For output control, reuse the native capacity runner with response-words16 and strict policy as a separate population. Prior GLM evidence supports this as a useful probe; sixteen visible words still do not fix reasoning/total generated tokens. Preserve failed strict requests and never call the result exact-token parity.

## GLM speculative correctness prior

Pinned upstream docs/glm-batched-speculation-defect.md records an export-scratch/indexer defect fixed by22887c1, which is an ancestor of our47581cca source (verified git merge-base). The current GLM path uses page-table extent without the old scratch width tensor. Remaining documented rare failures are TP4/C16 on Spark, not evidence of a TP2/C4 failure here. Capacity16 stays at<=16 target rows; depth5/cap24 would cross that shape boundary and requires additional exact retrieval checks. Reuse upstream scripts/repro_glm_batched_speculation.py as a bounded diagnostic, preserving full requests, replies, seeds and batch-width counters. Its correct/neighbour/corrupt classifier is a heuristic diagnostic, not proof that every wrong reply is engine corruption. No upstream GPU replay or kernel trace has been collected.

## Adaptive acceptance scope

helios/src/scheduler/speculation.rs initializes each logical request and proposal source with conditional acceptance0.5,weight2, bounded memory128. Warming scheduler graph costs does not warm per-request acceptance. This makes same-package fixed3 versus adaptive3 especially informative for our short-response screen, but a short-response improvement must be checked on longer coding outputs before recommending it. No prior or mathematical change has been made.

## Measured capacity16 and fixed3 screening

Capacity16 native adaptive passed7 protocol checks and18/18 synthetic agentic observations;18/18 context observations passed at actual~31.9k,129.9k,259.8k input tokens. Upstream exact retrieval10/11 includes one neighboring-record reply. Its natural capacity matrix95/96 includes one not-at-start canary failure, retained and excluded from performance ranking. Capacity4 original was also95/96.

Same-package fixed3 passed7 protocol checks,12/12 strict16-word requests and96/96 natural capacity requests. C1 decode medians191.7-196.8tokens/s versus adaptive166.1-178.9, but natural output lengths differ, there is no API seed, and these single12-request cells are screening evidence. C4 outcomes are mixed across context/cache cells. Cache reuse remains~0 for sequential12-request C1 attempted warm repeats; fixed speculation does not solve cache churn. No promotion conclusion.

RuntimeMTP0 passed ordinary smoke/JSON/needle but crashed in C4 tools after1/4; root sampler completion error1 has several possible predicates, with TP completion disagreement secondary. No MTP0 performance evidence is valid. See cap16-mtp0-failure-assessment.json and independent-review.

Fixed3 follow-up: upstream retrieval11/11 exact, but agentic17/18. Debug-loop repetition0 called run_tests(scope=all), outside the allowed enum, then recovered with unit, repaired the file and passed final tests. The protocol oracle remains failed. Generic harness pass threshold75% does not override this campaign all-oracles gate. This is a model-output protocol error, not evidence of a runtime crash or demonstrated algorithm defect.

The upstream scripts/benchmark_server.py delegates to qualify.performance, which hardcodes Qwen checkpoint/tokenizer/vocabulary and validates that exact corpus. It is not a supported GLM exact-token comparator without a separate reviewed extension; no bypass or benchmark claim from that tool.

Throughput scheduler passed7protocol,12/12strict16,96/96natural. Concurrent warm results did not consistently improve: aggregate228/149tok/s at10k/32k versus balanced247/222, with changed output lengths and cachehit75% versus83/92%. This screen favors retaining balanced for the next branch; it does not prove universal scheduler superiority.

Fixed3 source review confirms the same sampling correction/verifier at width4 as adaptive; forced width can change RNG advancement and complete trajectories. C1 counters occasionally name physicalbatch2/4 with one live lane. Do not infer userconcurrency from physicalgraphlabels. Discovery/Refresh is plausible but unproven without origin traces. See cap16-fixed3-independent-review.json.

Host32 same-package adaptive/balanced passed7protocol,12/12strict16,96/96natural and18/18agentic. Allfourwarmcells reached100%promptreuse; parentcache0C1remained~0. WarmC1TTFO fell from~0.82/2.45s to~0.04/0.06s; warmC4 aggregate~367tok/s atbothcontexts versusparent247/222. Naturaloutputs differ, so record this as a practical cache-treatment screen and repeat before causal speedup. Cgrouppeak33.724GiB,minimumhostavailable56.35GiB atcellboundaries,zeroobservedmax/OOMevents. Pool7314hostpages+52hostrows;GPU1532928nominaltokens+32sharedsnapshot slots. GPUslotsand snapshots sharestorage; see independentKVreview, notadditivefull-contextcapacity.

Native host-hit counters corroborate the cache treatment: each host32 C1 warm12-request cell restored3requests fromhost (23012/68738tokens for10k/32k), whileGPUreuse supplied theother9. Both were100%totalpromptreuse. This is direct evidence that host storage recovered otherwise evicted prefixes, not merely an attempted warm label. An8GiB store hasonlyabout10snapshotrows and may duplicate recentGPUentries rather than retain theworking set; a later24GiB/~39row control is a bounded resource-efficiency experiment, not yet measured.

## Host memory accounting and24GiB hypothesis
The32GiB setting is a cache budget, not a direct cgroup RSS claim. Across restarts managed cgroup peaks differ (about2GiB for fixed2 versusabout34GiB for adaptive), while host MemAvailable remainsabout56GiB. Keep cgroup attribution and whole-host availability separate; cause of attribution difference not established. Both10K/C1cold windows published/stored36snapshots. A24GiB host tier may fit roughly39rows and preserve this36-snapshot working set; this is a hypothesis until startup sizing and actual hit counters are measured. A16GiB tier would have fewer rows than the32GPU snapshot slots and is not the next useful experiment.

## Cache fixture correction at13:26Z
First host fixture filled66checkpoint rows into52host slots, evicting the target from both tiers. Corrected34checkpoint pressure verified a real host restoration and all6ninefact answers. ExactREADY had a punctuation violation. Originalbranch oracle summed overlapping GPUancestorlookup andhostlookup hits; rawmetrics retained for independent review. r1/r2 results remain failed rather than being overwritten.

## Profiling follow-up source review
`qualify/glm_performance.py` supplies a GLM-specific seven-request corpus (512input,256generated), native timestamps and CUPTIgraph mapping. It prepares its own TPgroup with16kcontext/defaultconfig and requires Nsight capture; `glm_gpu_analysis.py` expects765decode1graphs. Those defaults are not the cap16/MTP2nativepackage under test. Adapt the existing profiler to the exact prepared graph catalog and all-rank envelope before kernel attribution; do not run the generic Qwen replay or sum overlapping TP rank durations. No Nsight trace or kernel speedup was measured in this campaign. Native auxiliary embedding storage flags apply to PLE/Engram tables; they are not a justified GLM tuning experiment.

## Exact checkpoint reasoning policy at13:39Z
Pinnedchat_template.jinja2-3 usesexplicitlow/high, otherwiseMax;260alwaysopensreasoning. AllpriorunseteffortcellsarethereforeMaxunderthischeckpointtemplate. No-think flagisnotasupportedtoggleinthisexacttemplate; donotbenchmarkaneffectthatdoesnotexist. PlanLowasaseparaterequestpolicy, samepreparedpackage/T1/P.95/prompts/outputbudget, independentprotocol/tools/contextgates. Anyimprovementisapolicytradeoff, notengine-onlyspeedup. SourceURL/hashin glm53-upstream-chat-template.source.json.

## Sampler clarification at14:12Z
Live p2048 /v1/models advertises default seed0, T1, P.95, top_k=-1. The capacity/native suite omits the API seed, but Flare request.rs:381-391 applies the configured default (and276-277 adds request index). Therefore these are not independent random-seed replications; absence of an explicit API seed must not be described as random seeding. The three identical Low needle replays do not erase the original refusal. Record request seed absence separately from observed engine default.
