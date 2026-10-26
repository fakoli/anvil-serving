# GLM split-top-p reference: forced cases pass, strict ordinary gate stops inference

**Date:** 2026-10-25 local time; kernel attempt on 2026-10-26 UTC.

**Decision:** `functional`, `no-promotion`. The patched candidate was not admitted to model inference. Ignis remains held. Exact operational restoration passed, but the restored incumbent failed its single strict JSON smoke, so functional restoration acceptance is incomplete.

<!-- benchmark-result-card/v1 -->
## Result card

> A predeclared independent binary64 numerical reference resolved the earlier unknown ordinary-mask difference. All eight forced-logit cases passed, but one of 272 ordinary rows retained one additional token under the patch while stock exactly matched the reference. The strict local no-worse gate stopped before model weights or inference.

| Setup | Qualified value |
|---|---|
| Intended model | `nvidia/GLM-5.3-Flash-FP4-Local@76348ddb2be2e35e39ecfc00e75dc07703f4f3d5`; public served label `served-model-1` |
| Measured hardware | One RTX PRO 6000 Blackwell Max-Q GPU on Primary Node |
| Reserved topology | Two GPUs reserved for a planned TP2/DCP2 serve; model weights and distributed inference did not start |
| Runtime | vLLM fork `8244d1c44348ae8a5bbfc9f1b607021652283ed4` plus upstream `0a1276e5e3d9000488e7590cc0957edd709f2152`, bounded tracing, and the preweight reference gate |
| Recipe / reproduction | [Pinned source and diagnostic](https://github.com/fakoli/anvil-serving/tree/3fa3d856ba3f5a797a661a50a468fdee481f58ab/configs/runtime-patches/vllm/8244d1c4-split-topp); managed lifecycle and exact [identity pins](2026-10-25-sampling-reference-evidence/identity.json) |
| Measurement path | Synthetic float32 CUDA kernel regression against a CPU binary64 numerical reference before loading weights; JIT/cache condition uncontrolled |
| Kernel workload | Seeds 58,231 and 42; top-p input 0.95, no top-k; batches 4/64; vocabularies 131,072/154,856; forced values `1e9` and `1e9 / 0.6` |

| Headline measurement | Local result | Boundary |
|---|---:|---|
| Forced-logit cases | 8 / 8 passed | Synthetic split-mask checks only |
| Ordinary rows | 271 / 272 stock/patch-identical | Eight cases; all 136 seed-42 rows identical |
| Strict ordinary gate | 1 / 272 rows failed | Patch kept one extra token where stock exactly matched the numerical reference |
| Exact-reference rows | Stock 36 / 272; patch 35 / 272 | Exact equality was diagnostic, not the general acceptance rule |
| Peak PyTorch tensor allocation | 170,521,600 bytes (162.62 MiB) | Below the predeclared 1 GiB limit; excludes CUDA context/compiler overhead |
| Candidate model inference requests | 0 | Candidate exited 1 before weights; OOM-killed false |
| Restoration | Operational identity/readiness passed; strict JSON smoke failed | Campaign closed, but restoration acceptance is incomplete |

**Why it matters:** The independent reference shows that the earlier stock-versus-patch inequality was not an all-masked failure or an under-keep. The patch added one low-probability boundary token on one row. That small variance is within upstream's count-compatibility allowance, but it violates the separately predeclared stricter local rule that the patch must not be farther from the reference than stock on any row.

**Important caveat:** This result does not show that the upstream fix is ineffective. All forced cases passed. Across the ordinary rows, there was no missing reference token, prefix hole, invalid output, or input mutation. The run also does not prove an end-to-end reasoning-boundary fix because no candidate model request or serving-worker trace ran.

[Authoritative summary](2026-10-25-sampling-reference-evidence/summary.json) · [Evidence index](2026-10-25-sampling-reference-evidence/README.md) · [Artifact manifest](2026-10-25-sampling-reference-evidence/artifact-manifest.json)

## Why the gate changed from the prior trial

The [preceding sampling-fix trial](2026-10-25-sampling-fix.md) stopped when stock and patch differed on the fourth ordinary case. That trial retained neither differing indices nor an independent reference, so it could not determine which implementation was closer to the declared top-p rule. Its six completed forced cases and restoration remain historical evidence; none of its counts are reused as measurements here.

Before this run, an independent Sol review separated the exact-minimal numerical reference from the current split kernel's approximate contract. The frozen [reference design](2026-10-25-sampling-reference-evidence/reference-design.md) uses original fp32 inputs and the actual fp32 `p`, stable `(logit descending, token ID ascending)` ordering, binary64 exponentials, a `math.fsum` normalizer, and a compensated prefix scan certified at the boundary with `math.fsum`. It is an independent double-precision numerical convention, not exact-real arithmetic.

The current split implementation searches a bounded probability-space ladder and may fall back to a mass-preserving but nonminimal pivot. The open [upstream exactness issue](https://github.com/vllm-project/vllm/issues/59964) documents related over-retention and boundary-tie limitations. Accordingly, exact equality was recorded but was not required on every row. The frozen local policy instead required both versions to contain the reference prefix, required the patch to remain a canonical prefix, bounded extra count, and prohibited the patch from increasing per-row symmetric difference from the reference.

The official upstream tests allow small kept-count differences between the Triton and sort implementations: an absolute batch maximum of at most three, or otherwise less than 0.5% of the batch's maximum reference count. That compatibility policy informed the bound but did not override the stricter no-worse rule. The separate `7.7e-5` mass value in a third-party comment on [PR #58231](https://github.com/vllm-project/vllm/pull/58231) was not measured locally and was not used as a tolerance.

## Exact configuration and method

The executable source was frozen at `3fa3d856ba3f5a797a661a50a468fdee481f58ab` after independent review. The derived image was `sha256:67f0d352dc21a068fb310d2c4b06a08f66b7a798775f6f82c850cc0616eb20fc`. The original sampler SHA-256 was `0b5fc337058e22bc28a8530ccec904d6ccd89b9548b02955c657ac429b1669d2`; the patch plus trace SHA-256 was `2b5305c406d8495e515f048ce0322c7ae5946a49e5f27bd5bbbf30a1e1fea0e6`. The [source freeze](2026-10-25-sampling-reference-evidence/source-freeze.json), [build receipt](2026-10-25-sampling-reference-evidence/build.json), and [identity](2026-10-25-sampling-reference-evidence/identity.json) bind the inputs.

The runner evaluated four fixed shapes for each of two seeds, for eight ordinary cases and 272 rows. Seed 58,231 preserved the earlier reproduction case; independent seed 42 contributed 136 holdout rows. The candidate executed all eight forced cases first, ensuring a later ordinary failure could not suppress forced-case evidence. It then evaluated ordinary stock and patched masks from identical cloned input bytes.

Every ordinary row required valid output, unchanged input bytes, stock and patch containment of the reference prefix, a canonical patched prefix, the count envelope, and a patched symmetric difference no larger than stock's. The child timeout was 180 seconds and the tensor-allocation ceiling was 1 GiB. No threshold, corpus, seed, or retry was adapted after execution.

The eight bounded `ANVIL_SAMPLING_TRACE` warnings came from the synthetic regression child after split-mask completion. Their process identifier was removed from the public projection. No fresh serving-worker receipt exists because startup stopped before weights; the trace evidence must not be described as proof of the model request path.

## Results and stop condition

| Seed | Vocabulary | Rows | Stock exact reference | Patch exact reference | Stock/patch differences | Failed rows |
|---:|---:|---:|---:|---:|---:|---:|
| 58,231 | 131,072 | 4 | 0 | 0 | 0 | 0 |
| 58,231 | 131,072 | 64 | 10 | 10 | 0 | 0 |
| 58,231 | 154,856 | 4 | 0 | 0 | 0 | 0 |
| 58,231 | 154,856 | 64 | 11 | 10 | 1 | 1 |
| 42 | 131,072 | 4 | 0 | 0 | 0 | 0 |
| 42 | 131,072 | 64 | 7 | 7 | 0 | 0 |
| 42 | 154,856 | 4 | 0 | 0 | 0 | 0 |
| 42 | 154,856 | 64 | 8 | 8 | 0 | 0 |

The sole differing row was seed 58,231, vocabulary 154,856, batch 64, row 31. The numerical reference and stock each retained the canonical 114,428-token prefix. The patched set remained a canonical prefix but extended it to 114,429 tokens; its sole extra element was token 30,317 at the next boundary rank, with binary64 reference mass `2.0449949975313364e-6`. The patch omitted no reference token and had zero prefix holes. Its retained mass was `0.9500022054030309`, compared with `0.9500001604080334` for stock/reference and fp32 `p = 0.949999988079071`.

This one-token difference passed the predeclared count envelope derived from upstream tests but failed `patched_difference_not_worse`: stock's symmetric difference from the reference was zero and the patch's was one. This does not establish that the upstream test suite passed; its native fp32 comparison is not the same as this independent binary64 reference. The final regression receipt therefore reported `passed=false`. The startup wrapper exited nonzero without invoking the model-serving entrypoint. The [kernel receipts](2026-10-25-sampling-reference-evidence/kernel-receipts.log), [derived summary](2026-10-25-sampling-reference-evidence/kernel-summary.json), and [independent outcome review](2026-10-25-sampling-reference-evidence/outcome-review.json) retain the bounded accounting and decision.

## Restoration and remaining uncertainty

Managed restoration returned the exact operational configuration, original image and model revision, captured source/runtime-template/tokenizer bytes, launch command, entrypoint, mounts, exclusive two-GPU ownership, router configuration, readiness, and admission. The engine reported zero running and zero waiting requests; the router was ready and admitting with zero active requests. Host OOM kills did not increase. Available memory changed from 71,636,434,944 to 71,831,248,896 bytes; swap input rose 17 pages and output 27 pages; the restored container's recorded peak was 35,670,380,544 bytes. These are phase-bound observations, with the preload and stopped-candidate cgroup gaps recorded in [restoration evidence](2026-10-25-sampling-reference-evidence/restoration.json).

The sole restored-incumbent functional smoke failed strict JSON acceptance. It completed with `stop` after 54 completion tokens and 0.792 seconds, but emitted reasoning-like prose before the requested object:

> The user wants me to return ONLY a JSON object: {"language":"python","ok":true}. No prose.
>
> This is a simple, clear instruction. I should return exactly that JSON object with no additional text.{"language":"python","ok":true}

The complete public-safe response and parser error are retained in the [smoke artifact](2026-10-25-sampling-reference-evidence/restoration-smoke.json). No matched pretrial smoke exists, so a regression is unproven. The candidate cannot have caused this behavior because it never loaded weights and the exact original operational identity was restored. Operational restoration is verified; functional restoration acceptance remains incomplete. The campaign's `complete=true` means all planned stop and restoration steps finished, not that every gate passed.

The stopped candidate reports exit code 1 and `oom_killed=false`. Effective candidate cgroup values were unavailable after exit, which is a secondary observability limitation rather than evidence of resource exhaustion or the cause of the kernel failure.

The result leaves two separate questions unresolved. First, the exact internal cause of the one-token over-retention is unproven because no pivot, minimum-larger value, or duplicate budget was captured. Known absolute tie bands are a source hypothesis, not a measured cause. Second, even if a later reviewed policy admits the kernel, the original model reasoning-boundary failure remains untested on the patched serving path. Passing synthetic forced cases alone cannot resolve it. The independent outcome review confirms that this frozen local gate failed correctly and must not be retroactively weakened.

There is no performance, capacity, quality, answer-correctness, serving-worker path, end-to-end reasoning, or promotion result. Ignis remains held.

## Smallest next gate

Keep the kernel-policy failure and restoration-smoke failure separate. A future kernel experiment may prospectively adopt an upstream-compatible policy or capture the internal pivot and duplicate budget, but it cannot relabel this run as passed. A future operational experiment needs a matched pretrial strict-output smoke so restoration regressions are attributable. Any later model contact still requires advance notice and a fresh serving-worker path receipt.
