# GLM-5.3-Flash DCP1 active qualification evidence

**Campaign:** `2026-09-23-glm-dcp1-qualification`
**State:** active; live acceptance pending
**Decision:** `no-promotion`; r11 preliminary checks are retained, but promotion
has not been recorded.

This is a public-safe deployed-publication-pending evidence selection. Native
result schemas remain native. Local endpoints, paths, and runtime identifiers
were removed; reviewed synthetic benchmark output remains where it is needed to
audit a claim. The [artifact manifest](artifact-manifest.json) has ten retained
roles. Final solver review and publication receipt remain pending.

## Retained interim evidence

- [DCP1 201K unique-prefix 8K cohort](overlap-201k-8k-unique.json),
  [repeated-prefix cohort](overlap-201k-8k-repeat.json), and
  [310K/C2 overlap](overlap-310k-8k.json): retained native stability schemas.
- [Original 201K/4K unique-prefix cohort](overlap-201k-unique.json): retained
  failed third anchor with no visible answer. The later 8K cohort does not
  replace this result.
- [Short quality screen](quality.json) and [agentic gate](agentic-final-gate.json):
  12/12 and 18/18 respectively. These are separate gates, not a general model
  ranking.
- [Natural-output token count](natural-long-output-1-token-count.json): 18,016
  visible tokens with a complete marker. The [exact reviewed output](natural-long-output-1-visible.txt)
  and [independent coherence review](natural-long-output-1-independent-review.json)
  bind this separate, stop-finish natural-output gate; no integrated
  application-correctness claim follows.
- [64K native diagnostic](long-output-64k.json), [full synthetic visible
  output](long-output-64k-visible.txt), [summary](long-output-64k-summary.json),
  [execution record](long-output-64k-execution.json), [before/after
  identities](long-output-64k-before-identity.json) / [after](long-output-64k-after-identity.json),
  and [independent review](long-output-64k-independent-review.json): the
  65,536-token completion cap ended with `length` after 5,921 visible tokens
  (24,123 characters), three complete chapters, and a partial fourth; the
  completion marker is absent. The native failure remains
  `completion_budget_exhausted_after_visible_output`. Full reasoning is not
  retained; only 252,061 reasoning characters and null reasoning-token metadata
  remain. The review found coherent, non-degenerated visible text through its
  truncation, allowing the predeclared routed diagnostic to proceed without a
  waiver. It also records a material one-attempt visible-yield regression versus
  the matched incumbent (24,123 versus 70,772 characters; three versus eleven
  complete chapters), with no supported causal explanation or reasoning-loop claim.
- [Isolated routed preflight](isolated-routed-final-preflight.json) and its
  [same-identity execution receipt](isolated-routed-final-preflight-execution.json)
  retain 28 passed observations across eight functional families, including a
  20-request tool batch. This is a routed functional result, not a capacity
  ranking.
- [Routed admission native result](routed-admission-capacity.json), [execution
  receipt](routed-admission-execution.json), [active snapshots](routed-admission-active.jsonl),
  [terminal decisions](routed-admission-decisions.jsonl), and [router errors](routed-admission-router-errors.txt)
  bind an exclusive offered-eight, nominal-32K window to eight terminal rows
  with one configuration. Six served requests passed identity, own-canary, and
  strict-output checks. Two HTTP 503s are explicitly `admission_timeout`, so
  the native exit-1 performance result is ineligible and the optional 8/8
  completion objective failed. The retained [initial false gate](routed-admission-final-gate.json)
  documents the absent native active config field; [gate v2](routed-admission-final-gate-v2.json)
  corrects the join by binding the eight gateway IDs to the eight terminal rows
  with the same configuration, without a rerun. It closes the configured C4
  dispatch/admission boundary only: sampled dispatched plus streaming attained
  and did not exceed four across 75 samples. It does not qualify C8, sustained
  queueing, cancellation, or performance.
- [Final GPU window summary](gpu-final-quality-summary.json) reports 4,273
  samples per GPU, a 1,721 MiB sampled minimum free memory on each card, and
  maximum temperatures of 87/85 C. The source CSV is hash-bound by the summary;
  one-second sampling is not a continuous minimum. [Candidate status](qualification-final-candidate-status.json)
  records 52 GiB peak host memory at its limit, 13,361 `max` events, and zero
  OOM events; pressure remains a caveat, not an OOM or general reliability claim.
- The [final independent qualification review](final-qualification-independent-review.json)
  recommends controlled promotion of the exact DCP1 batch-2,048/C4/0.97 recipe
  under the retained user authorization. It remains a recommendation: DCP1 has
  825,268 KV tokens versus DCP2's 1,416,244 (a 41.73% reduction), so four full
  327K windows are unsupported. It is a mitigation, not a root-cause fix or
  crash-rate result, and it is not a speed winner.
- [r11 startup](r11-startup-gate.json), [scenario binding](production-r11-scenario-binding.json),
  [direct preflight](r11-direct-preflight.json), and [production-routed
  preflight](r11-production-routed-preflight.json) retain the reviewed r11
  identity and 28/28 passed direct plus production-routed functional checks.
  Live native-client acceptance remains pending; `promoted` stays false until
  that receipt closes the transaction.
- [Native client acceptance](client-acceptance-summary.json) records 13 passed
  semantic paths, not 13 strict-format passes. Five Pi paths and Pi Web returned
  fenced JSON, as did two secondary Hermes vision responses; Mac OpenClaw also
  prepended prose before correct JSON. These remain visible strict-format
  failures while their semantic/tool continuation paths pass. OpenClaw's actual
  reserve is 20K; retired upstream reserve knobs are not represented as a 65,536
  reserve. Pi Web's `messageCount=0` is a metadata quirk because its retained
  native transcript passed. [Controller mount verification](controller-mount-live-verification.json)
  records read-only mounts and unchanged siblings; all fleet repeat targets
  converged with `changed=0`. The public WebUI browser path passed its
  user-completed login, calculate-tool, and exact `1591` check. The [independent
  control-plane review](client-control-plane-independent-review.json) accepts
  semantic client and controller convergence with those format, correlation,
  Pi Web metadata, and OpenClaw reserve caveats; publication/readback remains
  required for campaign closure.
- The [publication receipt](promotion-publication-receipt.json) verifies nine
  imported dashboard runs against 99 source-matched metrics, nine authenticated
  Workbench evidence cards, byte-identical repeat imports, and model readiness
  of one. The public post remains pending PR merge and deployed readback. The
  observability repair did not restart inference or the router and made no new
  benchmark request.
- [Native SWE result](swe-native.json): all five fixed tasks were submitted and
  officially graded, with four resolved. The [absolute-gate disposition](swe-final-disposition-v2.json),
  [environment comparison](swe-environment-comparison.json), and [independent
  review](swe-environment-disposition-independent-review.json) retain the
  CPython/package mismatch: the absolute at-least-3/5 gate passes, but the
  4/5 versus 3/5 baseline comparison is ineligible. The earlier
  [false composite gate](swe-final-gate.json) remains diagnostic evidence.
- [Offered-C8 controls](concurrency-b2048-reload-offered8-seed801.json),
  [seed 1801](concurrency-b2048-reload-offered8-seed1801.json), and
  [failed seed 2801](concurrency-b2048-reload-offered8-seed2801.json): the
  last is performance-ineligible and closes the dependent C8 arm. The
  [independent review](c4-offered8-failure-independent-review.json) confirms
  that the failed direct diagnostic stops C8 without by itself disqualifying
  the unchanged production-envelope C4 recipe.
- The original selected batch-2,048/C4 performance cells are preserved for the
  dashboard importer at
  `performance-dcp1[-repeat1|-repeat2]-{c1-32k-unique,c4-128k-unique,c4-128k-shared}.json`.
  They remain separate cache/context groups with descriptive 16-request cell
  metrics; they do not rank batch 4,096, include primes, or incorporate C8.
- The native-derived [chart manifest](graph-manifest.json),
  [SVG](batch2048-capacity-matrix.svg), and
  [graph data](batch2048-capacity-graph-data.json) were rendered twice with
  identical bytes. They plot only those nine eligible cells and preserve their
  unique/shared cache separation.
- [First C4 reload window](gpu-c4-reload-control-summary.json): sampled
  minimum free memory was 1,755 MiB on each GPU, maximum temperatures were
  88/86 C, and the largest sample gap was 0.256 s. It covers warm-up,
  offered-C8 controls, and agentic work only; it is not a continuous minimum
  or the final SWE/64K window.
- [Search closure](search-closure.json) and
  [sanitization receipts](sanitization-receipts.json): scope and public-byte
  provenance for this stage.

## Gaps retained for campaign close

| Gate | State | Limit |
| --- | --- | --- |
| Frozen SWE | complete | Absolute 4/5 passes at-least-3/5; paired 4/5 versus 3/5 comparison is ineligible. |
| 64K diagnostic | complete, bounded | The native completion-budget failure remains; coherent truncation satisfies the separate diagnostic rule. One-attempt visible yield regressed versus the matched incumbent. |
| Routed dispatch-cap C4 and real client | C4 boundary complete | 28 routed preflight observations passed; the offered-eight diagnostic preserves two bounded admission timeouts and no C8 claim. |
| Restoration | pending | The active candidate has not reached campaign close. |
| Native-client acceptance | pending | r11 direct and production-routed preflights passed; live client receipt is still required. |
| Promotion review | recommendation retained | Promotion is authorized and recommended for the exact reviewed recipe; it has not been performed. |

The shareable sanitized managed recipe is
[candidate-dcp1.public.toml](candidate-dcp1.public.toml). It preserves the
managed schema, containment, environment, and flags while using the generic
cache path and port. Its materialization prerequisite is documented in the
[prior recipe-reconstruction section](../2026-09-23-glm-runtime-stability.md#recipe-reconstruction);
review a managed preview before any load.
