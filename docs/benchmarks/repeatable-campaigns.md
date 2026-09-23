# Repeatable benchmark campaigns

Use this workflow when a benchmark spans multiple checkpoints, runtimes,
contexts, concurrency levels, or delegated tasks. It complements the metric
definitions in [Benchmark methodology and evidence rules](methodology.md) and
the artifact contract in
`skills/anvil-serving-benchmark-docs/references/artifact-set-contract.md`.

## Start from requests, not commands

Copy `skills/anvil-serving-benchmark-docs/templates/artifact-set/` into the
dated evidence directory before the first live request. Translate each
requested outcome into `coverage-and-gaps.md`, including the evidence condition
that would satisfy it. Keep partial, rejected, deferred, and missing rows
visible. A configured limit, healthy endpoint, external claim, or planned run
is not measured evidence.

Use `campaign-state.json` as the compact resume ledger. Record the current
stage, verified launcher identity, active assignments, completed cells,
evidence paths, failures, and next actions. Do not copy logs, prompts,
responses, credentials, private paths, or reachable operator endpoints into
it.

## Verify the executing source

Before the first request, run these from the intended checkout:

```text
python -m anvil_serving.cli --version
python -c "import anvil_serving.cli; print(anvil_serving.cli.__file__)"
```

Verify that the imported module belongs to that checkout. Retain the version,
repository commit, and sanitized repository-relative module identity. Do not
publish the absolute path. Use `python -m anvil_serving.cli` for campaign
commands so a stale executable on `PATH` cannot silently select another
checkout.

## Validate the harness before loading candidates

```text
python scripts/run_tests.py tests/test_agentic_benchmark.py tests/test_benchmark_suite_runner.py -q
```

The wrapper checks directory trust, interpreter/dependency availability and
source resolution first, and puts the selected interpreter on subprocess PATH.
`--preflight` performs only those cheap checks. It records a fresh run ID,
interpreter, source revision, tracked diff digest, command, completion state and
exit code in `.pytest_cache/run-tests-receipt.json`; use `--receipt PATH` for
private durable campaign evidence. `--timeout SECONDS` records a timeout and
stops the test process. A running, missing or interrupted receipt is never a
pass. The wrapper checks source identity again after pytest; changed or
unreadable source cannot produce a passing receipt. A dirty-tree receipt describes a development run, not final acceptance.
Keep receipts private because commands and paths can contain operator identity.

After the final change, freeze a clean source revision and run the full suite
once. Repeat only for changed code, failures or a named unresolved concern.
Keep strict tool protocol, deterministic fixture state, answer formatting and
independently executed coding tasks distinct. The agentic debug loop uses
synthetic tool results; a pass does not prove a repository patch was executed.
Bind claims to the recorded oracle revision and preserve older scores.
For final signature scanning, use
`python .agents/skills/anvil-serving-secret-hygiene/scripts/semantic_secret_scan.py --signature-snapshot`.
It scans a fresh export of committed HEAD and excludes generated test/build
output; semantic and intended-untracked-file gates remain separate.

## Research once, expand only a specific gap

The lead maintains one dated source registry for the initial pass. Record URL
or artifact identity, revision, applicability, claim and decision impact.
Delegate additional independent research only after naming the unresolved
material choice and distinguishing evidence needed. Repeated URLs quoting one
result are one source. Use local artifacts alone for local-only questions.

## Advance through explicit gates

| Stage | Purpose | Advance gate |
|---|---|---|
| Research | Collect dated official and community leads | Exact candidate identities, provenance, and decision impact recorded |
| Feasibility | Reject physically or contractually infeasible recipes | Fit assumptions, unknowns, and rejection class retained |
| Scout | Screen a broad matrix economically | Identity, completion, correctness, and resource gates pass |
| Finalist | Compare survivors under one controlled workload | Matched controls, adequate sample population, and request isolation pass |
| Quality | Test behavior separately from speed | Declared functional, quality, context, modality, and integration gates pass or remain explicitly missing |
| Restoration | Return to the captured starting state | Managed state, exact identity, route behavior, and post-run smoke reconcile |
| Publication | Reconcile claims with native artifacts | Coverage matrix, friction dispositions, graphs, summary, and manifest agree |

Scout results choose what to test next. They are not headline evidence when
their request count, completion distribution, cache state, or output contract
differs from the finalist workload. A performance winner is not a qualified
deployment until its independent quality and integration gates pass.

## Investigate before rejecting candidates

Use `skills/anvil-serving-llm-qualification/references/configuration-search.md`
whenever a gate fails: capture and diagnose, research the observed error, test
a supported versioned remedy, then freeze and fully qualify the selected
configuration. Keep all failed artifacts. A command or stage stops on failure;
the campaign owner continues investigation or dispatches another bounded trial
while supported options, authority, and budget remain. Budget-limited candidates are unresolved, not defective.

Before the run, record ranked objectives, investigation allocations, and a
reserve for final qualification and authorized promotion or restoration.
Give promising candidates fair configuration coverage before picking the first
pass. Assess coding with independently checked repository tasks; strict output
adherence and speed are separate evidence. Report native, configured, measured,
and simultaneous context capacity separately; do not call a tested default a
proven maximum.

## Make comparable capacity cells

- Pin model, runtime image, engine revision, recipe, hardware/topology, context,
  concurrency, KV/state format, batching, cache mode, parser, sampling,
  reasoning, seed, and output contract.
- Use a nonzero controlled-output target with enough completion headroom and
  `--controlled-output-policy strict` for finalists. The default `observe`
  policy records adherence without rejecting a non-exact response, so publish
  false or unobservable adherence rather than calling it exact controlled
  decode. Keep the 8,192-character validation-capture limit in the output plan.
- Set cache state explicitly to `unique` or `shared`. Unique concurrent cells
  require request canaries that begin every response, contain no foreign
  `ANVIL_REQ` marker, and have a complete validation capture. Shared cells
  record cold/warm state and available hit counters. Never pool the
  populations.
- Pair speculative decoding with an otherwise matched no-speculation control.
- Keep TP, DP, and independent-replica aggregate results distinct. Combine
  synchronized replica artifacts only through the retained deterministic
  combiner.

For a new exact independent-replica aggregate, pass the same public-safe
`--clock-domain-id` only to processes that share one physical host clock and
the same `--configuration-fingerprint sha256:<64-lowercase-hex>` for the
normalized replica configuration. Never infer a common clock across hosts.
The combiner uses nanosecond timestamps only when the clock-domain declarations
match, and treats matching fingerprints as declared configuration identity.

Legacy artifacts without those fields are retained, not rewritten. When their
whole-second start and finish buckets match, the combiner derives a bounded
union wall time: `metrics.throughput_tok_s` is the conservative lower bound and
`throughput_tok_s_upper_bound` preserves the other end of the range. Publish
the range, timing precision, and `legacy-unverified` identity/validation status
instead of calling the aggregate exactly synchronized.

`capacity-v3` reports TPOT and the legacy `mean_inter_token_latency_ms` key as
the same per-request proxy: generation duration divided by completion tokens
after the first token. Its percentiles describe a population of request-level
means, not raw token-arrival intervals. Prefer the TPOT name in new prose. Do
not publish token-level ITL percentiles unless a native artifact retains token
timestamps and defines transport-chunk handling.

Nearest-rank p99 needs at least 100 measured requests to move beyond a
maximum-like descriptive statistic. At exactly 100 samples it is still one
observed tail order statistic and is sensitive to one request. Always publish
the sample count and method; use repetitions or a larger population before
calling it a stable service-level tail.

## Delegate with bounded packets

Use `dispatch-packet.md` for one bounded assignment. Include the stage, exact
objective, owned outputs, authoritative source artifacts, authority boundary,
stop conditions, verification, and concise return contract. Prefer a bounded
or no-history handoff when the packet and retained state are sufficient.
Return facts and paths rather than copied command output or full session
history.

## Fail fast and fix forward

Run one independently checkable command per step. Stop immediately on a
nonzero native exit before dependent work; follow the investigation loop above
rather than abandoning the candidate. On PowerShell, do not assume `$ErrorActionPreference`
turns a failed external executable into a terminating error; either run the
external command alone or check `$LASTEXITCODE` before continuing.

Keep status and diff inspection scoped to owned paths. Routine output should be
limited to state, bounded error detail, artifact path, digest, and verification
result. Record every actionable failure in `friction-log.md` with its earliest
evidence, immediate disposition, durable ticket/code/recipe/skill follow-up,
independent verification, and open/closed status. A manual retry is recovery,
not the fix-forward result.

## Improve the campaign method

Use the qualification skill's `references/improvement-loop.md` at closure or
a material process failure. The [research review](skill-improvement-research.md)
explains the external evidence, Astra adaptations, and validation limits.
Keep instruction revisions distinct from model/recipe revisions and evaluate
actual behavior before treating a reflection as a reusable rule.

## Finalize and report

Generate graphs from retained numeric paths, combine synchronized replicas,
and finalize the artifact manifest as separate fail-fast steps. Run each
deterministic helper twice and require byte-identical output.

The recipe-results renderer uses the product CLI:

```text
python -m anvil_serving.cli eval benchmark report CATALOG --root . --output docs/benchmarks/recipe-results.md --confirm
python -m anvil_serving.cli eval benchmark report CATALOG --root . --output docs/benchmarks/recipe-results.md --check
```

The confirmed form writes the generated report. `--check` verifies that the
tracked file is byte-current without writing. Start from
`skills/anvil-serving-benchmark-docs/templates/recipe-catalog.json`. Record the
expected native identity and workload, immutable source URL, exact selector
for a multi-recipe file, and numeric metric paths. Missing fields or mismatches
fail validation. Use LF-stable Git attributes for byte-bound catalog, recipe,
and generated report files.

Durable multi-cell capacity jobs and cursor-based managed recipe logs remain a
separate product improvement tracked in the
[capacity campaign ticket](https://github.com/fakoli/anvil-serving/blob/main/.tickets/2026-09-05-capacity-campaign-jobs-and-cursor-logs.md).
Do not replace that work with an ad hoc repository script.
