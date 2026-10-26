# GLM split-top-p patch: forced-token cases pass, ordinary parity stops startup

**Date:** 2026-10-25 local time; kernel attempt on 2026-10-26 UTC.

**Decision:** `functional`, `no-promotion`. The candidate is not qualified. Ignis remains held. The exact incumbent was restored and readmitted after identity and readiness checks; no generation smoke was sent.

<!-- benchmark-result-card/v1 -->
## Result card

> A pinned vLLM split-top-p backport completed six forced-logit regression cases on one RTX PRO 6000 Blackwell Max-Q GPU, then failed ordinary-logit exact mask parity at vocabulary 154,856 and batch 64. The startup gate stopped before model weights or candidate inference.

| Setup | Qualified value |
|---|---|
| Intended model | `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`; public served label `served-model-1` |
| Measured hardware | One RTX PRO 6000 Blackwell Max-Q GPU, selected as CUDA device 0, on Primary Node |
| Reserved topology | Two GPUs reserved for a planned TP2/DCP2 serve; the kernel gate did not exercise distributed model serving |
| Runtime | vLLM fork `8244d1c44348ae8a5bbfc9f1b607021652283ed4` plus upstream `0a1276e5e3d9000488e7590cc0957edd709f2152` and bounded diagnostic tracing |
| Recipe / reproduction | [Pinned build context and startup gate](https://github.com/fakoli/anvil-serving/blob/cf0b0db9efbd8d1213f3ff3fd4b765b0e2e3eb50/configs/runtime-patches/vllm/8244d1c4-split-topp/README.md); managed recipe lifecycle and exact [identity pins](2026-10-25-sampling-fix-evidence/identity.json) |
| Measurement path | Synthetic float32 kernel regression before loading weights; JIT/cache condition uncontrolled |
| Configured, unexercised model settings | FP4 weights, FP8 KV, TP2/DCP2, context 327,680, maximum sequences 4, MTP3, Model Runner V2 |
| Kernel contract | Seed 58,231; top-p 0.95, no top-k; batches 4/64, vocabulary/token pairs 131,072/13 and 154,856/154,842; forced values `1e9` and `1e9 / 0.6` |

| Headline measurement | Local result | Boundary |
|---|---:|---|
| Completed forced-logit checks | 6 / 8 planned, all six passed | Remaining two were not run after the ordinary control failed |
| Ordinary exact-parity controls | 3 passed / 4 planned; fourth failed | Failing shape: 64 × 154,856 |
| Candidate model inference requests | 0 | Startup wrapper withheld the original serving entrypoint |
| Candidate container exit | 1; OOM-killed false | Post-exit effective cgroup observations were unavailable |

**Why it matters:** The synthetic comparison reproduces the original all-masked forced-token defect and shows the patch correcting it in the six completed cases. The separate ordinary-input failure prevents accepting the patched runtime.

**Important caveat:** Counts are reconstructed from the six retained receipts, the exact runner order, and the terminating assertion. No final success aggregate was emitted. The ordinary mask difference was not retained, so this result does not establish which implementation is correct for that case. The previously observed model reasoning-boundary failure remains unresolved.

[Authoritative summary](2026-10-25-sampling-fix-evidence/summary.json) · [Evidence index](2026-10-25-sampling-fix-evidence/README.md) · [Artifact manifest](2026-10-25-sampling-fix-evidence/artifact-manifest.json)

## Prior observation and source path

The [earlier isolated model request](2026-10-25-glm-diagnostics.md#isolated-reasoning-boundary-follow-up) produced 64 ordinary tokens followed by 448 token IDs `0`, no closing reasoning marker, no answer, and `length` termination. That is measured end-to-end failure on the original runtime. It did not record the active sampling kernel.

The inspected Model Runner V2 source applies the thinking-budget state before temperature scaling. Its forced closing-token logit is `1e9`; temperature 0.6 motivates the second synthetic value. The speculative verifier uses the sampler's processed logits and top-k/top-p path. For the Triton implementation, CUDA batches of at most 64 enter `_apply_topp_split`, which dispatches statistics, five step rounds, and `_topp_sb_mask_kernel`. This is a source-backed path description, not proof that the earlier model request took every branch. The configured FlashInfer sampler setting alone does not establish which path executed.

The [exact upstream fix](https://github.com/vllm-project/vllm/commit/0a1276e5e3d9000488e7590cc0957edd709f2152) removes conversion of the probability pivot back into a huge logit threshold. It instead compares the computed probability to the pivot, preserves a maximum-logit candidate in the keep predicate, and reuses the probability for boundary-tie handling. The [two production hunks](https://github.com/fakoli/anvil-serving/blob/cf0b0db9efbd8d1213f3ff3fd4b765b0e2e3eb50/configs/runtime-patches/vllm/8244d1c4-split-topp/split-topp.patch) were applied unchanged; other changes from the larger PR were not imported.

The separate trace patch is default-off. This diagnostic enabled it to emit at most eight numeric receipts within 256 noncapturing checks after split-mask dispatch and synchronization, conditioned on a post-mask maximum of at least `1e8`. The six receipts here belong to the synthetic regression child, not a model-serving worker. They are Python dispatch receipts rather than profiler traces and do not support timing claims.

## Exact configuration and method

The Anvil Serving executable source was frozen at `cf0b0db9efbd8d1213f3ff3fd4b765b0e2e3eb50` after independent review. The base image is `sha256:745feb916ee816f5409fd58386114be4f22fbede7f9c0e0a7b8c4fe8dac0fb29`; the derived candidate is `sha256:b9d7e1c083047e7b5cbca464913df2ddbad0fff2a3d29236a3c07be89585519e`. [Build receipt](2026-10-25-sampling-fix-evidence/build.json) and [source freeze](2026-10-25-sampling-fix-evidence/source-freeze.json) retain the immutable inputs.

The original sampler SHA-256 is `0b5fc337058e22bc28a8530ccec904d6ccd89b9548b02955c657ac429b1669d2`; the fix plus trace is `2b5305c406d8495e515f048ce0322c7ae5946a49e5f27bd5bbbf30a1e1fea0e6`. The image keeps the original module separately, verifies both module hashes, and invokes the [bounded regression](https://github.com/fakoli/anvil-serving/blob/cf0b0db9efbd8d1213f3ff3fd4b765b0e2e3eb50/configs/runtime-patches/vllm/8244d1c4-split-topp/kernel_regression.py) before the original entrypoint. The child has a 180-second timeout. The terminal allocation assertion is below 1 GiB of PyTorch tensors, excluding CUDA context/compiler overhead; that terminal assertion was not reached, so no measured peak-memory pass is claimed.

For each vocabulary, the runner checks batch 4 and then 64. Each shape first requires exact original/patched equality on seeded ordinary logits, then checks the two forced-logit values. A forced case requires original logits to be entirely `-inf`, original softmax to be all NaN with argmax 0, and patched logits to retain exactly the selected token with no NaN probabilities and the expected argmax. The original module is the reproduction control, not an independent oracle for ordinary-input correctness.

The [frozen experiment plan](2026-10-25-sampling-fix-evidence/plan.json) allowed one subsequent probe invocation, at most two requests and no retries. Both were conditional on the kernel and readiness gates. Neither request ran. The previous baseline request was retained as prior evidence; the original model was not reloaded merely to repeat it.

## Results and stop condition

| Vocabulary | Batch rows | Ordinary parity | Forced `1e9` | Forced `1e9 / 0.6` |
|---|---:|---|---|---|
| 131,072 | 4 | Passed | Passed | Passed |
| 131,072 | 64 | Passed | Passed | Passed |
| 154,856 | 4 | Passed | Passed; token 154,842 retained | Passed; token 154,842 retained |
| 154,856 | 64 | **Failed** | Not run | Not run |

The [retained excerpt](2026-10-25-sampling-fix-evidence/kernel-log-excerpt.json) ends at `AssertionError: Ordinary-logit exact mask parity failed`. Execution reached the fourth ordinary comparison only after completing the prior six forced-case assertions. The [case ledger](2026-10-25-sampling-fix-evidence/kernel-cases.json) records that derivation and the two unrun cases. No adaptive retry or weaker comparison was substituted.

The startup wrapper exited nonzero and did not execute the model-serving entrypoint. The candidate status reports exit 1 and `oom_killed=false`. Its effective cgroup values became unavailable after exit; the resulting containment warning is not evidence that the kernel assertion was caused by memory exhaustion or that containment was breached.

## Restoration and remaining uncertainty

The managed failed-entry rollback restored the original image and model revision. All captured source, template and tokenizer bytes matched; command, entrypoint, mounts and selected environment matched; router configuration was unchanged. The replacement incumbent container was authenticated, ready, admitting and idle, with zero running or waiting engine requests. These are readiness and identity checks; no generation smoke was sent. The [native closure](2026-10-25-sampling-fix-evidence/closure.json) and [restoration record](2026-10-25-sampling-fix-evidence/restoration.json) retain the checks. No candidate promotion or Ignis trial occurred.

The trial window was 627.143 seconds. Host available memory changed from 71,505,248,256 to 71,948,771,328 bytes. Host OOM kills did not increase; all restored-container cgroup memory-event counters were zero, with a recorded peak of 33,015,083,008 bytes (30.75 GiB). Swap input rose by three pages (12 KiB) and output by five pages (20 KiB). These phase-bound observations do not establish continuous host peaks or zero swap activity. The failed candidate remains exited for log retention.

The misleading post-exit containment warning is retained as an [open product gap](https://github.com/fakoli/anvil-serving/blob/codex/glm-coding-diagnostics/.tickets/sampling-preweight-exit-status.md). Its repair is outside this publication scope; the authoritative startup failure remains the ordinary-mask assertion.

No mismatching token indices, count, probability mass or independently sorted reference result was captured for the failed ordinary control. The difference may require a correctness fix, or the old implementation may itself differ from the intended probability rule; this run cannot choose between those explanations. There is no candidate end-to-end boundary result, answer-oracle result, performance result, or complete eight-case kernel pass.

## Smallest next experiment

Retain the failing seeded 64 × 154,856 ordinary-input case and compare original and patched finite masks against an independent stable reference. Record differing token counts/indices, retained probability mass, selected boundary and tie behavior, and whether each mask meets the declared top-p rule. Preserve the original assertion failure. Only that evidence can justify a reviewed correction or an explicitly revised invariant; do not replace exact parity with a tolerance simply to admit the candidate.

If the revised kernel gate subsequently passes in a separately frozen and reviewed trial, with advance notice before touching the running model, rerun the frozen reasoning-boundary request with a fresh serving-worker trace. Keep forced-boundary evidence and answer correctness separate, then restore the incumbent. No broader benchmark or promotion follows automatically.
