# FLARE-FIX-001 proof assembly map

This is a closure map for the existing offline normalizer, not a new verifier and not a claim that r1-A passes acceptance. Use `harness/normalize.py` and `harness/check-normalize.py`; each eventual lane `proofs.json` must hash-bind its referenced proof and every raw receipt as described in `harness/PROOFS.md`.

## Per lane and arm

For `formal/r{1,2,3}-{a,b}/{lane}`, retain every applicable `warmup`, `prime`, `measured`, `measured-1`, and `measured-2` phase. The frozen plan defines the expected counts and roles in `harness/prospective.json`; warmups are identity/start anchors only and stay outside the 2,550-target denominator.

| Proof | Raw receipts and fields | Minimum derived checks |
| --- | --- | --- |
| Identity | phase `plan.json` (`arm`, `registry`, `registry_sha256`, `expected_image`, `epoch_ns`); `status.jsonl`; `running-proof.json`; `http/identity.json` | Reuse normalizer identity validation: recipe digest and registry hash, selected running container, model alias, one incarnation across phases, no incarnation reuse across lanes/runs, and actual A/B order from warmup epochs. |
| Functional | phase `summary.json` (`ok`, `error`, `audit`, `child_returncode`); `http/plan.json`; `http/summary.json` (`expected`, `attempted`, `completed`, `missing`, `semantic_quality`); `http/attempts.json` per row (`ok`, `phase`, `case_id`, `body_sha256`, `finish_reasons`, `message`, `output_token_ids`, `usage`, `performance`, timing fields) | Successful native phases and complete, validated attempts at the frozen count are necessary. Scope a strict semantic/controlled-output audit explicitly: it must name expected output/finish/capture conditions and cite attempt-level records. Preserve failed/reconstructed attempts and uncertain slots. |
| Monitoring | lane `monitor-up-1/2.json`, `monitor-freshness-1/2.json`, `monitoring-samples.jsonl`, `query-ranges/manifest.json`, `query-ranges/{collection_freshness,identity,engine,gpu_and_host}.json`, `registration-generation.txt` | Require successful query receipts, fresh collection, stable lane generation, and no retained containment/queue/crash fault. Bind the query interval to `manifest.start_epoch/end_epoch` and its generation. A nonempty HTTP result is not monitoring proof. |
| Restoration | `formal/rN-{a,b}/restore*.json`, `final-readmit.json`, `final-monitor-{up,freshness,sync}*.json`, `final-engine-targets.json`, `final-direct-preflight.json`, `final-routed-preflight.json`, and closeout | Require managed restore/readmit success, incumbent generation/image alignment, fresh monitoring, and both preflights. The preflights are service checks only; pair them with restoration receipt fields. |

## r1-A closed evidence presently available

`formal/r1-a/closeout.json` records 425/425 targets and 27/27 warmups, nine fresh candidate incarnations, `audit:false`, `diagnostic_only:true`, `performance_eligible:false`, and `promoted:false`. Every lane has `lane-complete.json`; the sampled `measured/summary.json` files have `ok:true`, `error:null`, `audit:false`, and the native HTTP summaries have complete expected/attempted/completed counts. This supports denominator and native-attempt retention only.

The r1-A formal HTTP summaries state `performance_eligible:false` and `semantic_quality:"not_evaluated"`. That field says the formal workload itself was not independently rescored; it does not negate the separate strict pilot audit. `audit-a-correctness/http/attempts.json` and `audit-b-correctness/http/attempts.json`, reviewed in `review/audit-a-gate-review.md` and `review/preformal-gate-review.md`, retain three successful checks on the exact audited arm/image: arithmetic text, parsed JSON, and `get_weather` tool name with `{"city":"Oakland"}` arguments. The reviewers independently rechecked all 3/3 returned messages and finish reasons.

This proves the stated pilot scope, not formal-workload semantic coverage. Whether those exact-arm/image pilot checks plus each formal lane's native validated attempts are sufficient for a future `functional` proof is a closure review decision that must be recorded with its scope; it is neither automatically true nor automatically false from `semantic_quality` alone. Do not replace that review with HTTP success, token IDs, or transfer bytes.

## Explicit false gates

| Gate | Evidence-backed value | Reason |
| --- | --- | --- |
| `independent_oracles` | `false` | `audit-a-transfer-audit.json` and `audit-b-transfer-audit.json` show actual TP2 row/page byte transport with zero transfer failures, but their scope explicitly excludes numerical state equivalence. `pilot-a-restore-comparison.json` is `failed_exact_all_case_gate`; the teacher-forced/logit oracle is not implemented. |
| `restore_fidelity` | `false` | `harness/normalize.py` forces this false whenever `diagnostic-amendment.json` exists. The amendment authorizes bounded diagnostics only, and the retained exact-128 numerical comparison failed. |
| recommendation / performance eligibility | `false` | The frozen normalizer/analyzer contract and r1-A closeout mark diagnostic-only/non-eligible; no proof assembly may override them. |

## Causal proof remains pending

The only admissible chain is, for each matched A/B pair and lane: (1) frozen capacity/allocation identity, then (2) relevant row-pressure change, then (3) completed eligible reuse, then (4) complete matched latency population. Attempt deltas provide the candidate raw components: `flare_prefill_tokens_total`, `flare_generated_tokens_total`, `flare_cache_reused_tokens_total`, `flare_host_cache_reused_tokens_total`, `flare_host_cache_fetched_total`, `flare_host_cache_fetch_failed_total`, `flare_host_cache_stored_total`, and `flare_host_cache_store_failed_total`; `performance.reused_prompt_tokens` is request-local context only. Do not treat generic cache reuse as disjoint GPU and host reuse without the source semantics.

r1-A contains only arm A. It cannot establish changed A/B capacity, a matched pressure/reuse contrast, or pair latency, so it cannot receive a passing `causal_chain` proof. Wait for completed B and all three AB/BA/AB pairs; then use the normalizer’s actual arm order, matching case IDs/work, failure denominators, phase summaries, and native query-range evidence. Missing, failed, unmatched, or semantically unqualified rows keep the causal gate false rather than being filtered away.

## Closure order

1. Stop each window’s producers and retain raw receipts unchanged.
2. Assemble hash-bound lane proof references only from the listed receipts; use `missing`/false where the strict audit or causal inputs are absent.
3. Run the existing normalizer and its focused synthetic check, then the unchanged analyzer/descriptor on the completed normalized matrix.
4. Review the normalizer’s `normalization_issues`, `source_artifacts`, denominators, receipts, and actual order before any descriptive pair summary. Do not create a performance or promotion claim from this map.
