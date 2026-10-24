# Independent final current-upstream baseline results review

```yaml
schema_version: operator-workflow/v1
request: Independently verify the closed current-upstream baseline closure, counts, lane-local fixture binding, compact decision numerics, and restoration evidence.
gate_state: pass
targets:
  - harness/summarize-current-baseline.py@2b90b036a2e41abdba7a70ef7036b23c8301185b55bf3761ca5b858cd40d4077
  - harness/check-current-baseline-summary.py@bcb18ea433dd270ec3b76c6d4c4d4e2fd445f616451a775564af00069f397236
  - summary-current-baseline.json@a2b5da05f83833908d1fc5f23911b67eadc9a9ceeb00a36165b5eada38c23ee1
  - decision-summary.json@f5d4f885ff0d0892eb9c8e62b97c8de76b0ff0732210c2be09e9d971d7ca38de
  - formal/current-baseline-canonical/closeout.json@a4a1c8d3735eddf79f92da5c775d391411b1cd1fbaa0d11bf7e94c7b77bb5047
tools_used:
  - read-only artifact and source inspection
  - deterministic summary rebuild comparison
  - independent raw/summary arithmetic reconciliation
  - restoration preflight and final inventory review
artifacts:
  findings_summary:
    blocking: 0
    resolved_medium: 1
    accepted:
      - final reader rebuild equals the saved complete summary with no issues
      - lane-local proof binding is correct and the rejected pre-fix summary is retained
      - 391 adapter requests plus one smoke passed across six distinct incarnations
      - all 192 measured responses match prompt, body, and one-token output contracts
      - compact medians and native host-cache deltas reconcile
      - exact incumbent restoration and direct/routed preflights passed
advisory_priors:
  implementing_model: GPT-6-based Codex; exact variant unavailable
  reviewing_model: GPT-5.6 Sol, independent from the implementer and evaluated GLM
  evaluated_model: GLM-5.3 Flare package at canonical revision dec48abd33efa73c3bb7c95b74eee10cad34f9be
recommendation: accept
human_gate_required: false
promoted: false
```

## Finding

### Medium — `cached_reused_tokens` names a host-cache counter as though it were total/GPU cache reuse

Each `decision-summary.json` cell sets `cached_reused_tokens` from `flare_host_cache_reused_tokens_total`. The values are numerically correct for that source: 76,848 in each fixed cell and zero in both protected cells. But the raw measured boundaries show the distinct GPU/cache counter `flare_cache_reused_tokens_total` changed by 123,587 in each fixed cell, 184,926 in `04-small12`, and 212,432 in `05-pageheavy`.

The current field name can therefore be read as claiming that protected cells reused zero cache tokens, which is false for GPU prefix-cache reuse. The adjacent `no_match` value has the same scope issue: it is the **host-cache lookup outcome** and may coexist with a GPU hit; 42 `no_match` outcomes do not mean 42 requests recomputed their prefixes.

**Minimum correction:** rename the field to `host_cache_reused_tokens` and either rename `no_match` to `host_lookup_no_match` or attach an explicit scope note that it includes requests already satisfied by GPU cache. No rerun or numerical change is needed.

## Verified results

- Rebuilding the final reader from the closed root produced the same object as `summary-current-baseline.json`: `complete: true`, `issues: []`, exact image/model/boundary identity, six distinct container/generation pairs, valid closeout, and exact lane-local proof hashes. The original fixture-binding rejection remains preserved at `analysis/summary-reader-fixture-binding-failure.json`.
- The request accounting reconciles exactly: 4 fixtures + 15 warmups + 180 prime + 192 measured = **391 adapter requests**, plus one passing smoke request. All attempts and fixture responses passed; no request failure was omitted.
- Every measured request matched its corresponding prime body and prompt and produced exactly one token: fixed cells 52/52 each, `small12` 24/24, and `pageheavy` 12/12, totaling **192/192**. Full speculative work remains `unknown:not_observed_in_current_response_schema`.
- Fixed measured TTFT medians independently recompute to **449.9329205 ms**, **452.902749 ms**, and **460.2504025 ms**. Each fixed cell records 52 host lookups, 10 admitted/fetched restores, 42 host `no_match` outcomes, 76,848 host-cache reused tokens, and 199,464 prefill tokens.
- Protected measured medians independently recompute to **9.381987 ms** over 24 requests and **18.0653265 ms** over 12 requests. Both have zero prefill and zero host fetches. Those observations describe warm GPU-cache paths and do not establish a general performance advantage.
- Startup logs bind the 32.0 GiB host prefix cache to 7,314 pages and 52 rows. The result does not expose the retired row-pressure counter, run a 75% row-share comparison, clear the prior 128-token equality failure, establish full speculative-work equality, or qualify performance.
- Closure restored the exact incumbent container/image with restart count zero, no containment/OOM error, and a ready/admitting idle primary route. Direct and routed preflights each passed 7 checks and 27 observations: 20 tool probes plus one tool-result initial and one continuation observation. The final inventory contains the running incumbent and the previously exited retained EXL3 entry; no Flare candidate remains.

**Original recommendation, now resolved below:** accept the closed raw evidence and final reader; hold the compact `decision-summary.json` for the terminology correction above.

## Final GO supplement

Reviewed `decision-summary.json` at SHA-256 `f5d4f885ff0d0892eb9c8e62b97c8de76b0ff0732210c2be09e9d971d7ca38de`. The requested correction is exact:

- every cell now uses `host_cache_reused_tokens`; the old `cached_reused_tokens` key is absent;
- `metric_semantics.host_cache_reused_tokens` binds it to `flare_host_cache_reused_tokens_total` and explicitly excludes GPU `flare_cache_reused_tokens_total`;
- `metric_semantics.no_match` states that it is a native host-cache lookup outcome, may coexist with a GPU-cache hit, and is not a full-prompt recomputation count.

**GO** for the closed raw evidence, final reader, and corrected compact decision summary. The earlier hold is cleared. `performance_qualified: false`, `promoted: false`, and `upstream_submitted: false` remain intact.

Focused final prose review also covered:

- `report.md` at `2b360448845a73e534d283633c4db67a9db322868e5705dc3efc251d4ce4c4b6` — accepted;
- `publication/compatibility-pr-draft.md` at `ffbb7bbdb39e45382e892ac84e2f9eb0ddc29c1dd1ad6909b7b8f8eee2152918` — accepted;
- `publication/issue-draft.md` at `b9f265142ea5d757ec06a9adaec3f4b96ffdf76cc61d47e54fc8483ed0c8404f` — accepted;
- `upstream-review.md` at `8b55d0ebc2776a81e1c3961bab5be97716b9a1a336030116cb29a09025612256` — one wording correction remains at line 14.

`upstream-review.md:14` says the protected windows had “zero ... MTP work,” while lines 38 and the final decision correctly keep full/per-request speculative work unknown. The raw protected phase boundaries show no change in the observed aggregate speculative/draft/proposal counters, but that is narrower than proving absence of all MTP work. Replace the phrase with “zero prefill and host transfer; observed aggregate speculative counters did not change, while per-request speculative work remains unknown.” This prose correction does not affect any number, gate, closed artifact, or the GO verdict above.
