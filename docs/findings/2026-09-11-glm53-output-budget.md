# GLM-5.3-Flash: 4,096 versus 16,384 completion tokens

**Date:** 2026-09-11. **Decision:** `no-promotion`; retain 4,096. **Evidence:** bounded `functional`, with eight `historical-invalid` coding runs.

<!-- benchmark-result-card/v1 -->
## Result card

No improvement established under the predeclared acceptance rule: the larger budget rescued a capped reasoning answer once but failed its repeat. The running backend remained unchanged.

| Valid tasks only | A: 4,096 | B: 16,384 |
|---|---:|---:|
| Successful tasks | 6/8 | 7/8 |
| Truncations / empty finals | 2 / 2 | 1 / 1 |
| Unexpected tool failures | 0 | 0 |
| Expected transient errors, recovered | 2 | 2 |
| Total task time, diagnostic | 77.95 s | 143.67 s |

**Setup:** real Pi → authenticated Anvil test route → same running GLM backend; native Linux, two RTX PRO 6000 Blackwell Max-Q 96 GB cards, TP2 over PCIe without NVLink, SGLang rc14, W4A16/NVFP4, FP8 KV, BF16 recurrent state, 393,216 context, C1, adaptive EAGLE [3,5], reasoning `max`. Warm/cache-uncontrolled: the time totals are not a matched speed claim. Managed baseline semantics are documented in the [393K recipe](https://github.com/fakoli/anvil-serving/blob/730cd4ddbe598715b89b08a1e88334e46c0666ad/configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml); live native transport settings were preserved instead of restoring historical WSL overrides.

**Main limitation:** 24 tasks executed, only 16 valid. All eight coding runs were excluded because source fixtures changed between runs. No retry beyond the user ceiling. This blocks a full coding acceptance claim independently of the failed repeated rescue.

[Evidence manifest](2026-09-11-glm53-output-budget-evidence/artifact-manifest.json) · [Evidence index](2026-09-11-glm53-output-budget-evidence/README.md) · [Publication summary](2026-09-11-glm53-output-budget-evidence/publication-summary.md).

## Contract and identity

Model `ormandj/GLM-5.3-Flash-W4A16-NVFP4-K32-Experts-FP8-WO` at `c3cbb9891b67c741bcbf6b176dd7af9265b069db`. Image `sha256:0c0637959c3931829f05154087bbefd2c50003fb9b2010200ce0ec82f4d71a53`. Engine `SGLang v0.1.1-rc.14+a547c90c74f1363920287eb80adc88a16d1e7005`. Publication/test source `730cd4ddbe598715b89b08a1e88334e46c0666ad`; temporary routers used deployed revision `81dfc4fa12f79e8ecc169a1bb4b443d50d15ccf3`, with all 286 Python package files hash-matched to the running image. Exact safe metadata is in [identity](2026-09-11-glm53-output-budget-evidence/identity.json).

Production discovery advertised 4,096 and a real Pi tool round trip requested `max_tokens=4096`. Test B advertised and forwarded 16,384. Both retained `reasoning_effort=max`, no explicit chat-template override, no sampling override and 1,200-second upstream timeout. Reported completion usage includes reasoning; reported token splits are retained without treating them as separate capacity pools. One-over-cap probes clamped 4,097→4,096 and 16,385→16,384 with `max_tokens_clamped`; unauthenticated discovery returned 401. Existing timeout/admission/protocol regression gates passed 97 tests.

## Method and results

Six frozen fixtures, two repetitions per arm, serialized C1; A/B in repetition one, B/A in repetition two. Eight model requests and 900 seconds maximum per task. Independent executable checks or exact objective answers; no model self-grading. Initial outbound message hashes match between arms for all eight valid case/repetition pairs. [Frozen workload and plan](2026-09-11-glm53-output-budget-evidence/panel.json), [native records](2026-09-11-glm53-output-budget-evidence/panel-results.json), [validity ledger](2026-09-11-glm53-output-budget-evidence/validity.json).

Both arms passed both weather continuations, both injected-error ticket recovery tasks, and both context retrieval tasks (47,777 actual input tokens). The retained RISC/MMLU-Pro reasoning fixture had A length-terminate without a final answer twice at 4,096 tokens. B answered correctly using 2,879 tokens on the first repetition, then length-terminated without a final answer at 16,384 tokens on the second. The predeclared rule requires rescue in both repetitions; B fails it. There were no unexpected tool/protocol failures in valid runs and no observed backend restart or OOM.

The two executable code fixtures suffered harness contamination through absolute source prompt paths. Apparent code passes remain in raw records but are not model-quality evidence. The harness was repaired to use stdin prompts and hash-checked declared-file copies; original failing fixtures were restored. Static checks passed, but no live repair validation is claimed. See [friction and durable invariant](2026-09-11-glm53-output-budget-evidence/friction-log.md).

## Decision and restoration

Budget exhaustion is a demonstrated failure mechanism in these reasoning runs; a repeatable remedy and broader cause remain unproven. Keep 4,096. B was configured and tested only on temporary routes; it is not ready for promotion. No runtime, quant, KV, speculation, TP, concurrency, prefill, power or transport change occurred. The original production router and normal Pi configuration remained untouched; temporary routers were stopped. Backend identity and restart counters were unchanged, discovery still advertised 4,096, and a fresh real Pi tool call plus executable assertion passed through production. [Restoration](2026-09-11-glm53-output-budget-evidence/restoration.json).

No full 393K input envelope, new concurrency level, model-wide ranking, or v0.4.3 qualification is established. The recommendation and historical benchmark archive remain unchanged. Full raw Pi output stays on private evidence storage; public redactions are enumerated in the evidence index.

## Publication verification

19 documentation tests, 97 focused router tests and the full repository suite (8,179 passed, 35 skipped) passed. Ruff, tracked Markdown links, strict MkDocs, CLI reference audit, skill validation, independent arithmetic/request checks and repeated byte-identical artifact finalization passed. The test environment needed owner-only worktree permissions and its existing Python executable on PATH; these were environment repairs, not model changes. [Verification record](2026-09-11-glm53-output-budget-evidence/validation.json).
