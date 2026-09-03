# GLM-5.3-Flash concurrency and KV-capacity interpretation

**Date:** 2026-09-03

**Scope:** derivative comparison of retained local dual-RTX-PRO-6000 TP=2
capacity evidence; no new live run

**Decision:** keep the published 393,216/C1 selection and corrected 524,288
rollback unchanged; qualify any higher current-profile concurrency before use

<!-- benchmark-result-card/v1 -->

This is a cross-run interpretation of retained local GLM-5.3-Flash evidence.
It adds no new benchmark, changes no recipe, and makes no claim about active
deployment state.

## Result card

| Field | Value |
|---|---|
| Model | Current ormandj W4A16/NVFP4 SGLang profile; immediate EXL3 K3 plus corrected DFlash2 K5 rollback; historical BrandonMusic TR3/EXL3 4 bpw profiles |
| Hardware | 2x NVIDIA RTX PRO 6000 Blackwell Max-Q, exclusive TP=2 over PCIe without NVLink under WSL2 |
| Runtime | Digest-pinned SGLang rc14 current profile; digest-pinned corrected vLLM/B12X rollback; historical digest-pinned Purtell vLLM/B12X runtime |
| Recipe | [Current 393K/C1](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-ormandj-sglang-sm120-tp2-393k-c1-adaptive-mtp-recipe.toml); [immediate 524K rollback](https://github.com/fakoli/anvil-serving/blob/main/configs/glm53-flash-purtell-k3-dflash2-k5-fp8-524k-vision-xgrammar-sm120-tp2-wsl2-recipe.toml); [historical BrandonMusic profiles](../benchmarks/configurations.md#glm53-524k) |
| Measurement path | Derivative comparison of retained online-direct capacity artifacts; no live request was sent for this interpretation |
| Contract | Keep configured scheduler concurrency, reported/configured KV-token capacity, short-request completion, and measured long-context concurrency as separate claims |
| Evidence | [Current SGLang bundle](2026-09-02-glm53-sglang-sm120-qualification-evidence/README.md); [corrected rollback bundle](2026-08-31-glm53-xgrammar-524k-qualification-evidence/README.md); [historical BrandonMusic bundle](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/README.md) |
| Decision | Keep 393,216/C1 as the published current profile and the corrected 524,288 EXL3/DFlash2 profile as the immediate exact rollback; treat a higher scheduler ceiling as unqualified until the intended request depth is measured concurrently |

| Headline measurement | Local result | Conditions |
|---|---:|---|
| Current deep-context capacity | 304,491 prompt tokens, C1 | 393,216 configured shared token pool; 256-token output cap exercised; p50 of three at the nominal 380K target; the separate published profile contract allows up to 4,096 output tokens |
| Immediate rollback long concurrency | 2/2 at 206,630 prompt tokens/request | nominal 250K, C2, 8,192-token completion allowance; 2,493,817 reported KV tokens |
| Historical BrandonMusic fixed-K5 short concurrency | 16/16 at 4K | 524,288 configured context; 23.85 aggregate output tok/s; not sixteen full-context requests |

**Why it matters:** the prior recipes did not prove sixteen simultaneous
524K conversations. They combined a scheduler ceiling of 16 with a larger
reported token pool and successful short-request batching. The corrected
rollback separately proved useful C2 long-context headroom; the current SGLang
profile is deliberately configured and qualified at C1.

**Important caveat:** reported token pools are runtime-specific accounting.
The retained campaigns changed engine, target quantization, draft model, KV
format, DCP, graph/state-cache settings, and context together. They show the
resulting capacity, but they do not isolate how much memory each difference
contributed.

Source evidence indexes: [current SGLang](2026-09-02-glm53-sglang-sm120-qualification-evidence/README.md) ·
[corrected rollback](2026-08-31-glm53-xgrammar-524k-qualification-evidence/README.md) ·
[historical BrandonMusic](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/README.md).
No new artifact manifest or publication summary was created because this
interpretation sent no request and produced no new measurement.

## Three different concurrency claims

1. **Scheduler concurrency** is the configured admission ceiling, such as
   `--max-running-requests 1` or `--max-num-seqs 16`. It says how many requests
   the scheduler may run; it does not say how deep those requests can be.
2. **KV-resident capacity** is the shared token budget exposed by the selected
   runtime and recipe. Dividing it by configured context gives a useful
   full-window-equivalent ratio, but not a latency or correctness result.
3. **Measured long-context concurrency** requires concurrent requests at the
   intended prompt and output sizes. A C16 batch of 4K prompts and a C2 batch
   of roughly 206K prompts answer different questions.

The retained local results line up as follows:

| Profile | Scheduler ceiling | Shared/reported token capacity | Full configured windows | Deepest measured concurrent request | Short concurrency evidence |
|---|---:|---:|---:|---|---|
| Current ormandj SGLang adaptive EAGLE, 393K | 1 | 393,216 configured shared tokens | 1.00 by configuration | C1, 304,491 prompt tokens | C1 only |
| Immediate EXL3 K3 + corrected DFlash2 K5 rollback, 524K | 16 | 2,493,817 reported KV tokens | 4.76 | C2, 206,630 prompt tokens/request, 2/2 | no corrected-524K C16 artifact retained |
| BrandonMusic vision fixed K5, 262K | 16 | 560,866 reported KV tokens | 2.14 | C1, 206,296 prompt tokens | C16 at 4K, 16/16, 28.3 aggregate tok/s |
| BrandonMusic text fixed K5, 524K | 16 | 565,898 reported KV tokens | 1.08 | C1, 495,045 prompt tokens | C16 at 4K, 16/16, 23.85 aggregate tok/s |
| BrandonMusic text no spec, 524K | 16 | 1,603,111 reported KV tokens | 3.06 | C1, 495,045 prompt tokens | C16 was measured only on the 262K companion, 16/16 at 33.69 aggregate tok/s |

The original BrandonMusic result therefore demonstrates successful short-request
continuous batching. Its fixed-K5 524K lane has only 1.08 full windows of
reported KV capacity, so its C16 setting cannot represent sixteen simultaneous
524K requests. The no-speculation lane exposes more KV headroom because it
does not retain the speculative draft state, but the 524K campaign still did
not measure long-context C16.

## Why the immediate rollback has more measured headroom

The corrected rollback exposes 2,493,817 reported KV tokens, compared with the
current SGLang recipe's configured 393,216-token shared pool. It also uses an
EXL3 K3 target, FP8 DS-MLA target KV, TP=2/DCP=2, and a different runtime and
state-cache implementation. Those differences plausibly create the larger
observed pool, but they were not varied one at a time. The defensible result is
the measured outcome: the rollback completed two concurrent nominal-250K
requests, while the current recipe has only been configured and qualified at
C1.

The trade is visible in the other measurements. The current SGLang profile is
the faster selected interactive profile at 112.07 tok/s decode at 4K and
99.79 tok/s at 304,491 prompt tokens. The corrected rollback measured 83.08
tok/s at 4K and a pooled 69.99 tok/s at 240K, while preserving the larger KV
pool and proven C2 long-context headroom.

## Safe next qualification envelope

Raising only the current profile's admission flag would not prove usable
concurrency. The recipe also pins batch-size-one decode graphs and carries
adaptive-EAGLE/Mamba state. A C2 candidate needs a fresh managed load with the
scheduler and graph/state limits made consistent, followed by functional,
capacity, quality, endurance, and post-workload VRAM checks.

The existing 393,216-token shared pool provides these planning bounds:

| Candidate | Prompt + output allowance | Combined request tokens | Pool remaining | Status |
|---|---:|---:|---:|---|
| C2 first gate | 180,000 + 4,096 per request | 368,192 | 25,024 total | recommended first text-only experiment; not yet measured |
| C4 follow-up | 80,000 + 4,096 per request | 336,384 | 56,832 total | test only after C2; not yet measured |
| Two full 393,216-token windows | 393,216 per request before output reserve | 786,432 | -393,216 | outside the current configured pool |

These are scheduler-pool arithmetic, not physical-fit or performance claims.
Media tokens, protocol overhead, allocator behavior, speculative state, and
output growth can reduce the practical envelope. Promotion or a public
concurrency increase still requires an actual concurrent benchmark and the
same fix-forward safety gates used for the current profile.

## Evidence boundary

- Current SGLang C1 evidence: [393K deep-capacity artifact](2026-09-02-glm53-sglang-sm120-qualification-evidence/safe393k-capacity-c1-380k-r3.json) and [393K promotion finding](2026-09-02-glm53-sglang-sm120-393k-promotion.md).
- Current SGLang rejected high-concurrency evidence: [499K/C4 long-capacity artifact](2026-09-02-glm53-sglang-sm120-qualification-evidence/full499k-unsafe-capacity-c4-120k-r4.json). It completed 4/4 but measured 148.86-second median TTFT and only 0.61 aggregate output tok/s; it is not support for promoting C4.
- Immediate rollback C2 evidence: [corrected 524K C2 artifact](2026-08-31-glm53-xgrammar-524k-qualification-evidence/dflash2-c2-ctx250000-max8192-r2.json) and [qualification finding](2026-08-31-glm53-xgrammar-524k-qualification.md).
- Historical BrandonMusic C16 evidence: [fixed-K5 524K artifact](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/fixed-mtp5-524k-capacity-4k-c16-low-r16.json), [vision fixed-K5 262K artifact](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/vision-fixed-mtp5-capacity-4k-c16-low-r1.json), [no-spec 262K artifact](2026-08-29-glm53-cardillo-adaptive-mtp-evidence/nospec-capacity-4k-c16-low-r16.json), and [qualification finding](2026-08-29-glm53-cardillo-purtell-qualification.md).
- Community and Discord figures remain external priors. None is relabeled as a local result here.

The selected published profile, immediate rollback, and historical recipes are
evidence decisions. Active routes, container state, and availability remain
private operator state and were not inspected or changed for this analysis.
